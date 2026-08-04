"""Paired baseline/perturbation execution over a patient case pack.

Every stress condition is run against its own BASELINE episode for the same
(case, world, target). Unpaired stress results are not reported: without the
control you cannot tell a perturbation effect from a target that was already
failing.
"""
from __future__ import annotations

from .scoring import paired_effect, score_episode
from .session import STRESS_TESTS, run_episode


def run_case_pack(target_fn, cases, target_id: str, target_version: str = "0",
                  stress_tests=None) -> dict:
    """Run every (case, world) under BASELINE and each requested stress test."""
    stress_tests = [s for s in (stress_tests or sorted(STRESS_TESTS)) if s != "BASELINE"]
    episodes, scores, pairs = [], [], []
    for case in cases:
        for world in case.worlds:
            base_tr = run_episode(target_fn, case, world.world_id, "BASELINE",
                                  target_id, target_version)
            base_sc = score_episode(base_tr, world)
            episodes.append(base_tr.to_dict()); scores.append(base_sc)
            for st in stress_tests:
                tr = run_episode(target_fn, case, world.world_id, st, target_id, target_version)
                sc = score_episode(tr, world)
                episodes.append(tr.to_dict()); scores.append(sc)
                pairs.append(paired_effect(base_sc, sc))
    return {"target_id": target_id, "target_version": target_version,
            "family": "patient_multiturn_triage", "maturity": "experimental",
            "episodes": episodes, "scores": scores, "paired": pairs,
            "summary": summarize(scores)}


def summarize(scores) -> dict:
    """Safety and usefulness summarized SEPARATELY. No combined score is emitted."""
    n = len(scores) or 1
    safety_keys = scores[0]["safety"].keys() if scores else []
    use_keys = scores[0]["usefulness"].keys() if scores else []
    return {
        "n_episodes": len(scores),
        "safety": {k: round(sum(s["safety"][k] for s in scores) / n, 4) for k in safety_keys},
        "usefulness": {k: round(sum(s["usefulness"][k] for s in scores) / n, 4) for k in use_keys},
        "any_safety_failure_rate": round(sum(s["any_safety_failure"] for s in scores) / n, 4),
        "any_usefulness_failure_rate": round(sum(s["any_usefulness_failure"] for s in scores) / n, 4),
        "note": "Safety and usefulness are reported separately and are never combined "
                "into a single score (EVAL_STANDARD.md §0).",
    }
