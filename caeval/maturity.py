"""Per-TEST-FAMILY maturity labels.

The platform is deliberately BROAD; the scientific claims must stay NARROW. A
single repo-level status cannot express that, so maturity is declared per family
(`maturity:` in the family YAML) and enforced here: a family may only be used for
a claim its maturity level supports.

    experimental          pipeline runs; measurement validity UNKNOWN
    calibrated            compared against clinician labels on a limited sample
    validated             detects known injected defects AND recognizes repairs,
                          at predeclared performance
    externally_replicated reproduced by another organization
    qualification_ready   suitable for a defined procurement / release decision
    surveillance_ready    validated for repeated version + drift monitoring

Ordering is cumulative: each level presupposes the ones before it.
"""
from __future__ import annotations

LEVELS = ["experimental", "calibrated", "validated",
          "externally_replicated", "qualification_ready", "surveillance_ready"]

_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}

DESCRIPTIONS = {
    "experimental": "Pipeline works; measurement validity unknown. NOT claim-bearing.",
    "calibrated": "Compared with clinician labels on a limited sample. Screening only.",
    "validated": "Detects known defects and recognizes repairs at predeclared performance.",
    "externally_replicated": "Reproduced by another organization.",
    "qualification_ready": "Suitable for a defined procurement or release decision.",
    "surveillance_ready": "Validated for repeated version and drift monitoring.",
}

# What each USE of a result requires.
CLAIM_REQUIREMENTS = {
    "internal_debugging": "experimental",
    "regression_screen": "experimental",
    "clinician_screening": "calibrated",
    "published_finding": "validated",
    "procurement_decision": "qualification_ready",
    "release_gate": "qualification_ready",
    "drift_monitoring": "surveillance_ready",
}


class MaturityError(RuntimeError):
    """Raised when a family is used for a claim its maturity does not support."""


def family_maturity(family: dict) -> str:
    lvl = (family or {}).get("maturity", {}).get("level", "experimental")
    if lvl not in _RANK:
        raise MaturityError(f"unknown maturity level {lvl!r}; expected one of {LEVELS}")
    return lvl


def rank(level: str) -> int:
    return _RANK[level]


def supports(level: str, claim: str) -> bool:
    required = CLAIM_REQUIREMENTS.get(claim)
    if required is None:
        raise MaturityError(f"unknown claim type {claim!r}; expected one of {sorted(CLAIM_REQUIREMENTS)}")
    return _RANK[level] >= _RANK[required]


def require(family: dict, claim: str) -> None:
    """Fail closed when a family is used beyond its demonstrated maturity."""
    lvl = family_maturity(family)
    if not supports(lvl, claim):
        raise MaturityError(
            f"family '{family.get('family_id')}' is maturity '{lvl}' "
            f"({DESCRIPTIONS[lvl]}) but claim '{claim}' requires "
            f"'{CLAIM_REQUIREMENTS[claim]}'. Raise the family's maturity by completing "
            f"the evidence it names in `maturity.evidence_required`, or make a weaker claim."
        )


def describe(family: dict) -> dict:
    lvl = family_maturity(family)
    m = (family or {}).get("maturity", {})
    return {
        "level": lvl,
        "description": DESCRIPTIONS[lvl],
        "claims_supported": sorted(c for c in CLAIM_REQUIREMENTS if supports(lvl, c)),
        "claims_blocked": sorted(c for c in CLAIM_REQUIREMENTS if not supports(lvl, c)),
        "evidence_required_to_advance": m.get("evidence_required", []),
        "rationale": m.get("rationale", ""),
    }
