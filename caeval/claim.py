"""Claim authority — the single computed object that decides what a run may say.

PRODUCT_V1.md promised that a run's claim is the WEAKEST of run mode, run
conformance level and family maturity, "all enforced in code". It was not: the
report received only subject, panel and family, so the headline was driven by
panel conformance and family maturity while the project mode never reached it.
This module makes the promise real and puts the result in every artifact.

Also the workflow-binding half: an evaluation plan is content-hashed at plan time
and every later stage verifies the hash. Planning one assessment and executing
another is the defect this exists to prevent — a mismatch BLOCKS, never warns.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .util import stable_hash_text, utc_now_iso

# Ordered weakest -> strongest. The effective claim is the MINIMUM across axes.
CLAIM_STRENGTH = [
    "none",                     # nothing may be claimed
    "demonstration",            # synthetic; not clinical evidence
    "internal_regression",      # a change-detection screen
    "automated_screen",         # L1: "the automated screen suggests"
    "calibrated_assessment",    # L2 + calibrated family, within audited scope
    "procurement_comparison",   # calibrated + a locked comparative pack
]
_RANK = {c: i for i, c in enumerate(CLAIM_STRENGTH)}

# What each axis permits, at most.
_MODE_CEILING = {
    "demonstration": "demonstration",
    "internal_regression": "internal_regression",
    "surveillance": "internal_regression",
    "calibrated_assessment": "calibrated_assessment",
    "procurement_comparison": "procurement_comparison",
}
_CONFORMANCE_CEILING = {"L0": "demonstration", "L1": "automated_screen", "L2": "calibrated_assessment"}
_MATURITY_CEILING = {
    "experimental": "internal_regression",
    "calibrated": "automated_screen",
    "validated": "calibrated_assessment",
    "externally_replicated": "calibrated_assessment",
    "qualification_ready": "procurement_comparison",
    "surveillance_ready": "procurement_comparison",
}

CLAIM_LABELS = {
    "none": "NO CLAIM SUPPORTED",
    "demonstration": "DEMONSTRATION — NOT CLINICAL EVIDENCE",
    "internal_regression": "INTERNAL REGRESSION SCREEN",
    "automated_screen": "AUTOMATED SCREEN — NOT A CLINICAL FINDING",
    "calibrated_assessment": "CALIBRATED ASSESSMENT WITHIN THE STATED SCOPE",
    "procurement_comparison": "COMPARATIVE PROCUREMENT EVIDENCE — NOT REGULATORY CERTIFICATION",
}

ALL_CLAIM_USES = ["clinical finding", "published finding", "procurement comparison",
                  "release decision", "regulatory submission"]

# What each effective claim UNLOCKS. Everything else is blocked.
_PERMITS = {
    "none": [],
    "demonstration": [],
    "internal_regression": [],
    "automated_screen": [],
    "calibrated_assessment": ["clinical finding", "published finding"],
    "procurement_comparison": ["clinical finding", "published finding", "procurement comparison"],
}


class PlanBindingError(RuntimeError):
    """The executed assessment does not match the validated plan."""


# CASE-PACK AUTHORITY. A run is bounded by the evidence status of the cases it
# used: a real product measured on a synthetic smoke fixture is a demonstration,
# whatever else is true. v0.16 recorded `demonstration_only` on the pack descriptor
# but never fed it into claim authority, so such a run could be labelled
# "exploratory".
_PACK_CEILING = {
    "demonstration_fixture": "demonstration",
    "unreviewed": "internal_regression",
    "clinician_reviewed": "procurement_comparison",
    "unknown": "none",
}

# TARGET PROVENANCE. A mock subject can never support more than a demonstration,
# and an unregistered subject supports nothing at all — unknown provenance must
# block, not default.
_TARGET_CEILING = {
    "mock": "demonstration",
    "real": "procurement_comparison",
    "unknown": "none",
}


@dataclass
class ClaimAuthority:
    project_mode: str
    run_conformance: str
    family_maturity: str
    case_pack_authority: str = "unknown"
    target_provenance: str = "unknown"
    effective_claim: str = ""
    label: str = ""
    permitted_claims: list = field(default_factory=list)
    blocked_claims: list = field(default_factory=list)
    limiting_axis: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def compute(project_mode: str, run_conformance: str, family_maturity: str,
            case_pack_authority: str = "unknown",
            target_provenance: str = "unknown") -> ClaimAuthority:
    """The effective claim is the WEAKEST of the axes.

    Five axes, not three. The case pack and the subject are part of what was
    measured: a real product on a synthetic fixture, or a mock on a
    clinician-authored pack, is a demonstration either way.

    Both new axes default to `unknown` -> `none`, so a caller that does not state
    them gets NO claim rather than a permissive one.
    """
    axes = {
        "project_mode": _MODE_CEILING.get(project_mode, "none"),
        "run_conformance": _CONFORMANCE_CEILING.get(run_conformance, "none"),
        "family_maturity": _MATURITY_CEILING.get(family_maturity, "none"),
        "case_pack_authority": _PACK_CEILING.get(case_pack_authority, "none"),
        "target_provenance": _TARGET_CEILING.get(target_provenance, "none"),
    }
    limiting = min(axes, key=lambda k: _RANK[axes[k]])
    effective = axes[limiting]
    permitted = list(_PERMITS.get(effective, []))
    return ClaimAuthority(
        project_mode=project_mode, run_conformance=run_conformance,
        family_maturity=family_maturity,
        case_pack_authority=case_pack_authority, target_provenance=target_provenance,
        effective_claim=effective,
        label=CLAIM_LABELS.get(effective, CLAIM_LABELS["none"]),
        permitted_claims=permitted,
        blocked_claims=[c for c in ALL_CLAIM_USES if c not in permitted],
        limiting_axis=limiting)


def permits(authority: ClaimAuthority, use: str) -> bool:
    return use in authority.permitted_claims


# --------------------------------------------------------------------------
# Plan binding: hash what was PLANNED; verify it before and after execution.
# --------------------------------------------------------------------------
BOUND_FIELDS = ("target_name", "target_version", "audience", "profiles", "family_id",
                "subject_kind", "subject_fingerprint", "case_pack_hash",
                "panel_names", "project_mode")


def plan_fingerprint(bound: dict) -> str:
    """Content hash over exactly the fields that define WHICH assessment this is."""
    missing = [f for f in BOUND_FIELDS if f not in bound]
    if missing:
        raise PlanBindingError(f"cannot fingerprint an incomplete plan; missing {missing}")
    payload = {k: bound[k] for k in BOUND_FIELDS}
    return stable_hash_text(json.dumps(payload, sort_keys=True, default=str))


def build_binding(project, family_id: str, panel_names: list, case_pack_hash: str) -> dict:
    """Derive the bound plan from the VALIDATED project — not from CLI flags."""
    meta = project.target_meta
    subject = project.subject
    bound = {
        "target_name": meta.get("name"),
        "target_version": meta.get("version"),
        "audience": _audience_for(project),
        "profiles": sorted(project.profiles),
        "family_id": family_id,
        "subject_kind": subject.get("kind"),
        # identity of the connector WITHOUT secrets (headers/tokens excluded)
        "subject_fingerprint": stable_hash_text(json.dumps(
            {k: v for k, v in sorted(subject.items())
             if k in ("kind", "model", "url", "prompt_field", "answer_path", "arm")},
            sort_keys=True, default=str))[:16],
        "case_pack_hash": case_pack_hash,
        "panel_names": sorted(panel_names),
        "project_mode": project.mode,
        "bound_at": utc_now_iso(),
    }
    bound["plan_hash"] = plan_fingerprint(bound)
    return bound


def _audience_for(project) -> str:
    from .intake import TARGET_PROFILES
    from .score import audience_key
    auds = {audience_key(TARGET_PROFILES[p]["audience"])
            for p in project.profiles if p in TARGET_PROFILES}
    if len(auds) != 1:
        raise PlanBindingError(
            f"project spans audiences {sorted(auds) or '[]'}; a run must bind exactly one "
            f"(the failure bar and high-severity fields differ by audience)")
    return auds.pop()


def verify_binding(expected: dict, actual: dict) -> None:
    """BLOCK on any divergence between the plan and what is about to run."""
    exp_hash, act_hash = expected.get("plan_hash"), plan_fingerprint(actual)
    if exp_hash == act_hash:
        return
    diffs = [f"{f}: planned={expected.get(f)!r} actual={actual.get(f)!r}"
             for f in BOUND_FIELDS if expected.get(f) != actual.get(f)]
    raise PlanBindingError(
        "EXECUTION DOES NOT MATCH THE VALIDATED PLAN — refusing to run.\n  "
        + "\n  ".join(diffs or [f"plan_hash {exp_hash} != {act_hash}"])
        + "\nAn evidence package must describe the assessment that was planned, "
          "reviewed and validated. Re-plan, or correct the run.")


def pack_authority(pack_descriptor: dict) -> str:
    """Map a resolved case-pack descriptor onto its claim axis."""
    if not pack_descriptor:
        return "unknown"
    if pack_descriptor.get("demonstration_only") or pack_descriptor.get("is_builtin"):
        return "demonstration_fixture"
    reviewed = pack_descriptor.get("clinician_reviewed")
    if reviewed is None:
        return "unknown"
    return "clinician_reviewed" if reviewed else "unreviewed"


def target_provenance(target_descriptor: dict) -> str:
    """Map a registered target descriptor onto its claim axis."""
    if not target_descriptor:
        return "unknown"
    is_mock = target_descriptor.get("is_mock")
    if is_mock is None:
        return "unknown"
    return "mock" if is_mock else "real"
