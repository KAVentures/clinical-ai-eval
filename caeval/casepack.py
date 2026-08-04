"""Case-pack studio — authoring, validating and content-addressing case packs.

A case pack is the part of an evaluation a clinician actually owns, and the part
this harness cannot generate. The studio's job is to make authoring one possible
without letting the tooling quietly certify it.

Three rules the studio enforces:

  1. **The studio never marks a pack clinician-reviewed.** Structural validation is
     a spell-check, not a clinical review. `review_status` is `unreviewed` until a
     named person with a recorded role signs it, and there is no code path that
     sets it otherwise. This is the same distinction the validity audit already
     draws (§5): automated screening is not clinical validation.
  2. **Validation is fail-closed and structural checks are named as such.** Every
     issue is an ERROR (blocks) or a WARNING (does not), and nothing is inferred:
     a missing field is an error, never a default.
  3. **A pack is content-addressed**, so a run can prove which pack it used and a
     silently edited case invalidates the address rather than the conclusion.

For patient packs the studio additionally checks the properties the stress tests
depend on — because a world set that is not underdetermined makes P2 measure
nothing, and a substitution that does not change the disposition makes P5 measure
nothing. Those are construct-validity checks on the fixture, and they are errors.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .util import stable_hash_text, utc_now_iso

ERROR, WARNING = "ERROR", "WARNING"
UNREVIEWED = "unreviewed"
REVIEW_STATUSES = [UNREVIEWED, "clinician_reviewed", "clinician_authored_and_reviewed"]

# Roles permitted to sign a pack as reviewed.
SIGNING_ROLES = {"clinician", "specialist_clinician"}


@dataclass
class Issue:
    level: str
    where: str
    message: str
    fix: str = ""

    def blocking(self) -> bool:
        return self.level == ERROR


@dataclass
class PackMeta:
    pack_id: str
    version: str
    kind: str                          # clinician_vignette | patient_worlds
    visibility: str                    # public_dev | private_qualification
    review_status: str = UNREVIEWED
    signed_by: list = field(default_factory=list)   # [{name, role, signed_at}]
    provenance: str = ""
    notes: str = ""

    def __post_init__(self):
        if self.kind not in ("clinician_vignette", "patient_worlds"):
            raise ValueError(f"unknown pack kind {self.kind!r}")
        if self.visibility not in ("public_dev", "private_qualification"):
            raise ValueError(f"unknown visibility {self.visibility!r}")
        if self.review_status not in REVIEW_STATUSES:
            raise ValueError(f"unknown review_status {self.review_status!r}")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate_clinician_pack(cases: list) -> list:
    issues = []
    seen = set()
    for i, c in enumerate(cases):
        at = f"case[{i}]"
        for f in ("item_id", "input_text"):
            if not str(c.get(f, "")).strip():
                issues.append(Issue(ERROR, at, f"missing required field {f!r}",
                                    "every case needs a stable id and the case text"))
        cid = c.get("item_id")
        if cid in seen:
            issues.append(Issue(ERROR, at, f"duplicate item_id {cid!r}",
                                "ids address content; duplicates make results unattributable"))
        seen.add(cid)
        if len(str(c.get("input_text", ""))) < 80:
            issues.append(Issue(WARNING, at, "case text is very short",
                                "a case too thin to remove information from cannot be perturbed"))
    return issues


def validate_patient_pack(cases: list) -> list:
    """Structural AND construct-validity checks on a world-based patient pack."""
    issues, seen = [], set()
    for i, case in enumerate(cases):
        at = f"{getattr(case, 'case_id', f'case[{i}]')}"
        if case.case_id in seen:
            issues.append(Issue(ERROR, at, "duplicate case_id", "case ids must be unique"))
        seen.add(case.case_id)

        if len(case.worlds) < 2:
            issues.append(Issue(ERROR, at, "a case needs at least two worlds",
                                "one world means the opening message fixes the answer, so no "
                                "history question is necessary and P1/P2 measure nothing"))
            continue
        if not case.is_underdetermined():
            issues.append(Issue(
                ERROR, at, "all worlds require the same disposition",
                "the case is not underdetermined, so a system can be right without asking "
                "anything; make the worlds diverge or drop the case"))
        distinguishing = case.distinguishing_facts()
        if not distinguishing:
            issues.append(Issue(
                ERROR, at, "no fact distinguishes the worlds",
                "P5 substitution has nothing to substitute; add the facts a clinician "
                "would actually ask about"))
        for w in case.worlds:
            wat = f"{at}/{w.world_id}"
            lb = [f.key for f in w.facts if f.load_bearing]
            if not lb:
                issues.append(Issue(ERROR, wat, "world has no load-bearing facts",
                                    "mark the facts that change the disposition"))
            if not w.obtainable_load_bearing():
                issues.append(Issue(
                    ERROR, wat, "every load-bearing fact is unavailable",
                    "the world is unwinnable: no history could reach the right answer, so "
                    "a failure here would measure the fixture, not the product"))
            for f in w.facts:
                if f.load_bearing and not f.asked_by and f.disclosure == "when_asked":
                    issues.append(Issue(
                        WARNING, f"{wat}/{f.key}",
                        "load-bearing fact has no `asked_by` phrasings",
                        "the only way to elicit it is to use the key verbatim; real products "
                        "will be scored as not asking when they did"))
            if w.required_disposition in ("emergency_now", "call_emergency") and not w.red_flags:
                issues.append(Issue(
                    WARNING, wat, "emergency world declares no red flags",
                    "missed_red_flag cannot fire for this world"))
            if w.required_disposition in ("emergency_now", "call_emergency") \
                    and not w.required_safety_net:
                issues.append(Issue(WARNING, wat, "emergency world declares no required safety net",
                                    "missing_safety_net cannot fire for this world"))
    return issues


def validate(meta: PackMeta, cases: list) -> dict:
    issues = (validate_patient_pack(cases) if meta.kind == "patient_worlds"
              else validate_clinician_pack(cases))
    if not cases:
        issues.append(Issue(ERROR, "pack", "pack is empty", "add at least one case"))
    if meta.visibility == "public_dev" and meta.review_status != UNREVIEWED:
        issues.append(Issue(
            ERROR, "pack", "a public_dev pack is marked reviewed",
            "public packs are wiring fixtures; a reviewed pack belongs in "
            "private_qualification, or a vendor can tune against the cases used to qualify it"))
    errors = [i for i in issues if i.blocking()]
    return {
        "pack_id": meta.pack_id,
        "valid": not errors,
        "n_cases": len(cases),
        "errors": [asdict(i) for i in errors],
        "warnings": [asdict(i) for i in issues if not i.blocking()],
        "review_status": meta.review_status,
        "note": "Structural validation only. This is NOT a clinical review and does not "
                "establish that any case is clinically sound.",
    }


# --------------------------------------------------------------------------
# Content addressing and signing
# --------------------------------------------------------------------------

def _canonical_case(case) -> dict:
    """Semantic view of a case — ordering and formatting must not change the hash."""
    if hasattr(case, "worlds"):
        return {
            "case_id": case.case_id,
            "opening_message": case.opening_message,
            "worlds": sorted(({
                "world_id": w.world_id,
                "required_disposition": w.required_disposition,
                "facts": sorted(({"key": f.key, "value": str(f.value),
                                  "disclosure": f.disclosure,
                                  "load_bearing": bool(f.load_bearing)}
                                 for f in w.facts), key=lambda d: d["key"]),
                "red_flags": sorted(w.red_flags),
                "forbidden_advice": sorted(w.forbidden_advice),
                "required_safety_net": sorted(w.required_safety_net),
            } for w in case.worlds), key=lambda d: d["world_id"]),
        }
    return {"item_id": case.get("item_id"), "input_text": case.get("input_text"),
            "ground_truth_label": case.get("ground_truth_label", "")}


def pack_hash(meta: PackMeta, cases: list) -> str:
    """Address the CONTENT, not the file. Reordering cases must not change it;
    editing one word must."""
    body = {"kind": meta.kind,
            "cases": sorted((_canonical_case(c) for c in cases),
                            key=lambda d: str(d.get("case_id") or d.get("item_id")))}
    return stable_hash_text(json.dumps(body, sort_keys=True))


def sign(meta: PackMeta, name: str, role: str, pack_digest: str) -> PackMeta:
    """Record a named person's clinical review of a SPECIFIC pack version.

    The signature binds to the content hash, so editing a case after signing
    invalidates the signature instead of silently inheriting it. This is a
    provenance record, not identity proof: nothing here authenticates that the
    signer is who they say they are.
    """
    if role not in SIGNING_ROLES:
        raise ValueError(f"role {role!r} may not sign a pack as clinically reviewed; "
                         f"permitted: {sorted(SIGNING_ROLES)}")
    if not name.strip():
        raise ValueError("a signature needs a named person; anonymous review is not review")
    if meta.visibility == "public_dev":
        raise ValueError("public_dev packs are wiring fixtures and must not be signed as "
                         "clinically reviewed")
    meta.signed_by = list(meta.signed_by) + [
        {"name": name, "role": role, "signed_at": utc_now_iso(), "pack_hash": pack_digest}]
    meta.review_status = "clinician_reviewed"
    return meta


def verify_signatures(meta: PackMeta, cases: list) -> dict:
    """A signature is valid only for the exact content it was made against."""
    current = pack_hash(meta, cases)
    valid = [s for s in meta.signed_by if s.get("pack_hash") == current]
    stale = [s for s in meta.signed_by if s.get("pack_hash") != current]
    ok = bool(valid) and meta.review_status != UNREVIEWED
    return {
        "pack_hash": current,
        "valid_signatures": valid,
        "stale_signatures": stale,
        "review_status_effective": meta.review_status if ok else UNREVIEWED,
        "ok": ok,
        "note": ("" if not stale else
                 f"{len(stale)} signature(s) were made against different pack content and do "
                 f"NOT carry over. The pack was edited after review; it must be re-reviewed."),
    }


def build(meta: PackMeta, cases: list) -> dict:
    """Validate, address, and emit the pack descriptor written next to a run."""
    report = validate(meta, cases)
    digest = pack_hash(meta, cases)
    sigs = verify_signatures(meta, cases)
    return {
        "meta": asdict(meta),
        "pack_hash": digest,
        "validation": report,
        "signatures": sigs,
        "usable_for_qualification": bool(
            report["valid"] and sigs["ok"] and meta.visibility == "private_qualification"),
        "built_at": utc_now_iso(),
    }
