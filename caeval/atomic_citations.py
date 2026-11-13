"""Atomic claim-to-source citation support with independent review labels.

Existence/currency and quote-location are deterministic. Semantic support is
NOT: it requires two consistent, independently authored clinician annotations.
An unassessed or contested claim must never count as "supported".
This does not imply the clinical claim itself is medically true.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


VERDICTS = {"supports", "contradicts", "does_not_support"}
SOURCE_STATUSES = {"current", "superseded"}
REVIEWER_KINDS = {"clinician", "automated"}


def canonical_hash(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_pack(pack: dict) -> dict:
    if not isinstance(pack, dict) or pack.get("schema") != "atomic_citation_support_v1":
        raise ValueError("Unsupported atomic citation pack schema")
    sources = pack.get("sources")
    claims = pack.get("claims")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a nonempty list")
    if not isinstance(claims, list) or not claims:
        raise ValueError("claims must be a nonempty list")
    indexed = {}
    for s in sources:
        if not isinstance(s, dict):
            raise ValueError("source must be an object")
        sid = s.get("source_id")
        if not isinstance(sid, str) or not sid.strip() or sid in indexed:
            raise ValueError("source_id must be unique and nonempty")
        if not isinstance(s.get("text"), str) or not s["text"].strip():
            raise ValueError(f"source {sid!r} needs nonempty text")
        if s.get("status") not in SOURCE_STATUSES:
            raise ValueError(f"source {sid!r} must declare current/superseded")
        if not isinstance(s.get("version"), str) or not s["version"].strip():
            raise ValueError(f"source {sid!r} needs a version")
        indexed[sid] = s
    seen = set()
    for c in claims:
        if not isinstance(c, dict):
            raise ValueError("claim must be an object")
        cid = c.get("claim_id")
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            raise ValueError("claim_id must be unique and nonempty")
        seen.add(cid)
        if not isinstance(c.get("text"), str) or not c["text"].strip():
            raise ValueError(f"claim {cid!r} requires atomic text")
        cites = c.get("cited_source_ids")
        if not isinstance(cites, list) or any(not isinstance(x, str) or
                                              not x.strip() for x in cites):
            raise ValueError(f"claim {cid!r} has invalid citation IDs")
        if len(cites) != len(set(cites)):
            raise ValueError(f"claim {cid!r} contains duplicate citations")
    return {"n_claims": len(claims), "n_sources": len(sources),
            "pack_sha256": canonical_hash(pack),
            "status": "STRUCTURALLY_VALID_SUPPORT_NOT_INFERRED"}


def prepare_review_queue(pack: dict) -> list:
    """Generate independent-review tasks without any other reviewer's answers."""
    meta = validate_pack(pack)
    sources = {s["source_id"]: s for s in pack["sources"]}
    tasks = []
    for claim in pack["claims"]:
        for sid in claim["cited_source_ids"]:
            source = sources.get(sid)
            if not source or source["status"] != "current":
                continue  # existence/currency are deterministic; no semantic judgement
            tasks.append({
                "claim_id": claim["claim_id"], "claim_text": claim["text"],
                "source_id": sid, "source_version": source["version"],
                "source_text": source["text"],
                "pack_sha256": meta["pack_sha256"],
                "review_instructions": (
                    "Independently label whether THIS source supports, contradicts "
                    "or does not support THIS atomic claim. For supports or "
                    "contradicts provide a verbatim, relevant source quote. "
                    "Quote matching is not semantic validation; clinical judgement "
                    "is required. Do not read another reviewer's labels."),
            })
    return tasks


def _annotate(pack: dict, annotations: list, min_clinicians: int) -> dict:
    if type(min_clinicians) is not int or min_clinicians < 2:
        raise ValueError("at least two independent clinician reviewers are required")
    meta = validate_pack(pack)
    pairs = {(c["claim_id"], sid) for c in pack["claims"]
             for sid in c["cited_source_ids"]}
    sources = {s["source_id"]: s for s in pack["sources"]}
    by_pair = {}
    seen = set()
    if not isinstance(annotations, list):
        raise ValueError("annotations must be a list")
    for a in annotations:
        if not isinstance(a, dict):
            raise ValueError("annotation must be an object")
        key = (a.get("claim_id"), a.get("source_id"))
        if key not in pairs:
            raise ValueError(f"unrecognized claim/source pair: {key!r}")
        source = sources.get(key[1])
        if not source or source["status"] != "current":
            raise ValueError(f"cannot annotate nonexistent or superseded source {key[1]!r}")
        if a.get("pack_sha256") != meta["pack_sha256"]:
            raise ValueError("annotation pack hash differs; reference content changed")
        rid = a.get("reviewer_id")
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError("annotation requires nonempty reviewer_id")
        if a.get("reviewer_kind") not in REVIEWER_KINDS:
            raise ValueError("reviewer_kind must be clinician or automated")
        verdict = a.get("verdict")
        if verdict not in VERDICTS:
            raise ValueError(f"invalid annotation verdict: {verdict!r}")
        ident = (key, rid)
        if ident in seen:
            raise ValueError("duplicate reviewer annotation for claim/source")
        seen.add(ident)
        quote = a.get("evidence_quote", "")
        if not isinstance(quote, str):
            raise ValueError("evidence_quote must be a string")
        if verdict in ("supports", "contradicts"):
            if not quote.strip() or quote.strip() not in source["text"]:
                raise ValueError("supports/contradicts requires a verbatim quote from that source")
        by_pair.setdefault(key, []).append(a)
    return by_pair


def _support_for_pair(by_pair: dict, key: tuple, min_clinicians: int) -> str:
    reviews = [r["verdict"] for r in by_pair.get(key, [])
               if r["reviewer_kind"] == "clinician"]
    if len(reviews) < min_clinicians:
        return "unassessed"
    return reviews[0] if all(v == reviews[0] for v in reviews) else "contested"


def score(pack: dict, annotations: list, *, min_clinicians: int = 2) -> dict:
    """Score *only* independently adjudicated claim-source support.

    Missing labels are unassessed, not supported. References may be incomplete:
    this module does NOT independently extract all claims from free-form answers.
    """
    meta = validate_pack(pack)
    by_pair = _annotate(pack, annotations, min_clinicians)
    sources = {s["source_id"]: s for s in pack["sources"]}
    results = []
    for claim in pack["claims"]:
        citations = claim["cited_source_ids"]
        missing = [sid for sid in citations if sid not in sources]
        superseded = [sid for sid in citations if sid in sources and
                      sources[sid]["status"] != "current"]
        current = [sid for sid in citations if sid in sources and
                   sources[sid]["status"] == "current"]
        support = {sid: _support_for_pair(by_pair, (claim["claim_id"], sid),
                                          min_clinicians) for sid in current}
        decisions = set(support.values())
        if not citations:
            status = "unsupported"  # no source at all
        elif not current:
            status = "unsupported"  # only missing or obsolete citations
        elif "contested" in decisions:
            status = "unassessed"
        elif "supports" in decisions and "contradicts" in decisions:
            status = "conflicting_evidence"
        elif "supports" in decisions:
            status = "supported"
        elif "unassessed" in decisions:
            status = "unassessed"
        elif "contradicts" in decisions:
            status = "contradicted"
        else:
            status = "unsupported"
        results.append({
            "claim_id": claim["claim_id"], "status": status,
            "n_citations": len(citations),
            "missing_source_ids": missing,
            "superseded_source_ids": superseded,
            "per_current_source": support,
            "review_complete": status not in ("unassessed", "conflicting_evidence"),
            "note": ("A supported citation is a reference-annotation verdict, not "
                     "independent proof that the clinical claim is medically true."),
        })
    counts = {name: sum(r["status"] == name for r in results)
              for name in ("supported", "unsupported", "contradicted",
                           "unassessed", "conflicting_evidence")}
    evaluated = counts["supported"] + counts["unsupported"] + counts["contradicted"]
    invalid = counts["unsupported"] + counts["contradicted"]
    return {
        "schema": "atomic_citation_support_report_v1",
        "claim_authority": "EXPLORATORY_ONLY",
        "pack": meta,
        "reviewer_minimum": min_clinicians,
        "coverage": {
            "n_claims": len(results), "n_adjudicated_or_deterministically_invalid": evaluated,
            "n_unassessed_or_conflicting": len(results) - evaluated,
            "adjudication_fraction": round(evaluated / len(results), 6),
        },
        "support": {
            "supported": counts["supported"], "unsupported": counts["unsupported"],
            "contradicted": counts["contradicted"],
            "unassessed": counts["unassessed"],
            "conflicting_evidence": counts["conflicting_evidence"],
            "unsupported_claim_rate_on_assessed": (
                round(invalid / evaluated, 6) if evaluated else None),
            "note": "The denominator excludes unknown/contested support; never "
                    "treat unassessed citations as supported. See coverage.",
        },
        "citation_integrity": {
            "claims_with_missing_sources": sum(bool(r["missing_source_ids"]) for r in results),
            "claims_with_superseded_sources": sum(bool(r["superseded_source_ids"]) for r in results),
        },
        "results": results,
        "limitations": [
            "Claims must be atomized and supplied by the caller; claim extraction coverage is unverified.",
            "Clinician reviewer identities are metadata claims, not cryptographic identity verification.",
            "Verbatim quote provenance does not establish semantic entailment or medical truth.",
            "Independent review agreement is necessary but does not establish clinical validation.",
        ],
    }


def load_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path: str | Path) -> list:
    return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines()
            if x.strip()]


def write_jsonl(path: str | Path, rows: list) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n"
                         for x in rows), encoding="utf-8")
