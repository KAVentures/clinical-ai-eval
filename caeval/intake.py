"""Intended-use intake (EVAL_STANDARD.md §2) — always the first task.

Produces eval_plan.yaml from a target descriptor and classifies the target into
one or more target profiles. The intake questions (what the system does, who the
user is, what decisions it influences, what info it normally receives/lacks, what
oversight is expected, what failure could cause harm) are captured verbatim in the
plan so a reviewer can see the basis for every downstream test-family choice.
"""
from __future__ import annotations

import yaml

# §2 target-profile table (profile key -> audience + most-relevant families).
TARGET_PROFILES = {
    "clinician_rag": {
        "audience": "clinician",
        "families": ["citation accuracy", "retrieval omission", "stale guideline", "source conflict", "unsupported synthesis"],
    },
    "clinician_decision_support": {
        "audience": "clinician",
        "families": ["missing information", "over-commitment", "contradiction handling", "unsafe management"],
    },
    "medication_assistant": {
        "audience": "clinician",
        "families": ["missing information", "renal/hepatic dosing", "interaction", "contraindication"],
    },
    "patient_triage_chatbot": {
        "audience": "patient",
        "families": ["red-flag detection", "under-triage", "over-reassurance", "escalation", "health-literacy robustness"],
    },
    "medical_scribe": {
        "audience": "clinician",
        "families": ["omission", "fabrication", "speaker attribution", "medication/negation errors"],
    },
}

INTAKE_QUESTIONS = [
    "what_it_does",
    "who_is_the_user",
    "decisions_it_can_influence",
    "information_normally_received",
    "information_normally_unavailable",
    "expected_human_oversight",
    "plausible_harm",
]


def build_eval_plan(target_meta: dict) -> dict:
    """target_meta must include the intake answers and a `profiles` list (one or
    more keys from TARGET_PROFILES). Returns the eval_plan dict (write with
    yaml.safe_dump)."""
    profiles = target_meta.get("profiles") or []
    unknown = [p for p in profiles if p not in TARGET_PROFILES]
    if unknown:
        raise ValueError(f"unknown target profile(s): {unknown}. Known: {sorted(TARGET_PROFILES)}")
    if not profiles:
        raise ValueError("intake must classify the target into >=1 target profile (§2).")

    audiences = sorted({TARGET_PROFILES[p]["audience"] for p in profiles})
    intake = {q: target_meta.get(q, "(not provided)") for q in INTAKE_QUESTIONS}

    return {
        "target": {
            "name": target_meta.get("name", "unnamed_target"),
            "version": target_meta.get("version", "unknown"),
            "endpoint": target_meta.get("endpoint", "(local/mock)"),
        },
        "intake": intake,
        "target_profile": {
            "types": profiles,
            "audience": audiences,
            "input_modalities": target_meta.get("input_modalities", ["free_text"]),
            "output_actions": target_meta.get("output_actions", []),
        },
        "relevant_test_families_by_profile": {p: TARGET_PROFILES[p]["families"] for p in profiles},
        "notes": (
            "This plan is the basis for suite selection (§4). Primary output is a "
            "screen plus an evidence package, NOT a deployment-readiness verdict (§0)."
        ),
    }


def write_eval_plan(plan: dict, path: str) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(plan, f, sort_keys=False, default_flow_style=False)
