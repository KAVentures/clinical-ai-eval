"""Bridge from patient episodes to the existing judge / review / L2 machinery.

The patient substrate must not become a second, parallel pipeline with its own
review path and its own idea of what a claim is. Everything downstream —
blinded-judge panels, review routing, HMAC review packets, the L2 adjudication
gate, content addressing — already exists and is the part that has been hardened.
This module makes an episode look like a scoreable unit to that machinery instead
of reimplementing it.

Two things are deliberately NOT bridged:

  * The deterministic patient metrics are NOT written into the judge's
    BINARY_FIELDS. They are a separate, deterministic evaluator; merging them
    would let a deterministic keyword match masquerade as a judge label.
  * Nothing here can raise a maturity level or a conformance level. Patient
    families are `experimental`, so any run built from these records is capped at
    `experimental` by claim.compute() regardless of how the judges vote.
"""
from __future__ import annotations

from ..util import stable_hash_text, utc_now_iso
from .registry import PATIENT_FAMILY_MATURITY

# Episode strata that MUST be routed to human review when one is run.
REVIEW_STRATA = [
    ("under_triage", lambda s: s["safety"]["under_triage"]),
    ("missed_red_flag", lambda s: s["safety"]["missed_red_flag"]),
    ("over_reassurance", lambda s: s["safety"]["over_reassurance"]),
    ("fabricated_history", lambda s: s["safety"]["fabricated_history"]),
    ("unsafe_self_treatment", lambda s: s["safety"]["unsafe_self_treatment"]),
    ("over_triage", lambda s: s["usefulness"]["over_triage"]),
    ("no_disposition", lambda s: s["usefulness"]["no_disposition_reached"]),
]


def episode_to_record(episode: dict, score: dict) -> dict:
    """One episode as a manifest-shaped record the rest of the harness accepts.

    `input_text` is the full transcript, so a blinded judge reads the conversation
    rather than a summary of it — the summary is exactly where a scoring bug would
    hide from the judge.
    """
    transcript = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in episode["turns"])
    return {
        "item_id": f"{score['case_id']}::{score['world_id']}",
        "perturbation_id": stable_hash_text(
            f"{score['case_id']}:{score['world_id']}:{score['stress_test']}:"
            f"{episode['trace_hash']}")[:16],
        "dataset": "patient_public_smoke",
        "perturbation_type": score["stress_test"],
        "test_id": score["stress_test"],
        "transform": score["stress_test"],
        "input_text": transcript,
        "original_text_hash": stable_hash_text(episode["turns"][0]["text"]),
        "removed_fields": ";".join(sorted(episode["never_asked_load_bearing"])),
        "synthetic_added_text": "",
        "expected_missing_evidence": ", ".join(score["obtainable_load_bearing"]),
        "ground_truth_label": score["required_disposition"],
        "severity": "high",
        "created_at": utc_now_iso(),
        # --- patient-specific, kept in a separate namespace from judge fields ---
        "patient": {
            "episode_id": score["episode_id"],
            "trace_hash": episode["trace_hash"],
            "given_disposition": score["given_disposition"],
            "required_disposition": score["required_disposition"],
            "deterministic_safety": score["safety"],
            "deterministic_usefulness": score["usefulness"],
            "family_maturity": PATIENT_FAMILY_MATURITY,
        },
        # deliberately absent: any BINARY_FIELDS key. Deterministic extraction is a
        # separate evaluator and must not be mistaken for a judge label.
    }


def to_records(run: dict) -> list:
    by_id = {e["episode_id"]: e for e in run["episodes"]}
    return [episode_to_record(by_id[s["episode_id"]], s) for s in run["scores"]
            if s["episode_id"] in by_id]


def review_queue(run: dict) -> list:
    """Episodes that must reach a human if a review round is run.

    Both directions are routed: over-triage and no-disposition episodes are queued
    alongside the under-triage ones, because a reviewer who only ever sees missed
    escalations will calibrate toward referring everything.
    """
    out = []
    for s in run["scores"]:
        strata = [name for name, fn in REVIEW_STRATA if fn(s)]
        if strata:
            out.append({"episode_id": s["episode_id"], "strata": strata,
                        "stress_test": s["stress_test"],
                        "given_disposition": s["given_disposition"],
                        "required_disposition": s["required_disposition"]})
    return out


def claim_inputs(run: dict) -> dict:
    """What this run may be claimed to show. Always capped at experimental."""
    return {
        "family_maturity": PATIENT_FAMILY_MATURITY,
        "subject_is_mock": run["target_id"].startswith("mock_"),
        "case_pack_clinician_reviewed": False,
        "note": "Patient families are experimental. A run over this substrate cannot "
                "support a published finding, a procurement decision, or a release "
                "gate, however the judges vote.",
    }
