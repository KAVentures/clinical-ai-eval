"""Paired baseline/perturbation execution over a patient case pack.

Every stress condition runs against its own BASELINE episode for the same
(case, world, target). Unpaired stress results are not reported: without the
control you cannot tell a perturbation effect from a target that was already
failing.

A condition a case cannot support is SKIPPED LOUDLY and counted. Silently running
a baseline episode under a stress label is worse than not running it — the hollow
episode is indistinguishable from a passing one, and coverage looks complete.
"""
from __future__ import annotations

from .scoring import paired_effect, score_episode, tracked_disposition_change
from .session import STRESS_TESTS, run_episode
from .stress import StressSpecError, applicable


def run_case_pack(target_fn, cases, target_id: str, target_version: str = "0",
                  stress_tests=None, pack_descriptor: dict | None = None,
                  target_spec=None) -> dict:
    """Run every (case, world) under BASELINE and each applicable stress test."""
    stress_tests = [s for s in (stress_tests or sorted(STRESS_TESTS)) if s != "BASELINE"]
    episodes, scores, pairs, skipped, substitution_effects = [], [], [], [], []
    for case in cases:
        for world in case.worlds:
            base_tr = run_episode(target_fn, case, world.world_id, "BASELINE",
                                  target_id, target_version)
            base_sc = score_episode(base_tr, world)
            episodes.append(base_tr.to_dict()); scores.append(base_sc)
            for st in stress_tests:
                ok, why = applicable(case, world.world_id, st)
                if not ok:
                    skipped.append({"case_id": case.case_id, "world_id": world.world_id,
                                    "stress_test": st, "reason": why})
                    continue
                try:
                    tr = run_episode(target_fn, case, world.world_id, st,
                                     target_id, target_version)
                except StressSpecError as e:
                    skipped.append({"case_id": case.case_id, "world_id": world.world_id,
                                    "stress_test": st, "reason": str(e)})
                    continue
                # Score against the world the episode ACTUALLY ran, not the
                # declared one: under P5 the substituted world is the truth.
                sc = score_episode(tr, _effective_world(case, tr, world))
                episodes.append(tr.to_dict()); scores.append(sc)
                pair = paired_effect(base_sc, sc)
                pairs.append(pair)
                if st == "P5_STATE_SUBSTITUTION":
                    substitution_effects.append(
                        {"episode": sc["episode_id"],
                         **tracked_disposition_change(base_sc, sc)})
    return {"target_id": target_id, "target_version": target_version,
            "family": "patient_red_flag", "maturity": "experimental",
            "target_spec": _describe_target(target_spec, target_id, target_version),
            "case_pack": pack_descriptor or _undeclared_pack(),
            "episodes": episodes, "scores": scores, "paired": pairs,
            "substitution_effects": substitution_effects,
            "skipped": skipped,
            "coverage": coverage(cases, stress_tests, skipped),
            "summary": summarize(scores)}


def _effective_world(case, trace, declared):
    from .stress import prepare
    if trace.stress_test in ("BASELINE",):
        return declared
    try:
        world, _traj, _applied = prepare(case, trace.world_id, trace.stress_test)
        return world
    except StressSpecError:
        return declared


def _describe_target(spec, target_id, target_version) -> dict:
    """Target provenance from the REGISTERED spec, never inferred from the id.

    v0.14 inferred mock status from `target_id.startswith("mock_")`, which breaks
    silently the moment a real product is registered with an unlucky name — and a
    real run mislabelled as mock, or the reverse, changes what may be claimed.
    """
    if spec is None:
        return {"target_id": target_id, "version": target_version, "kind": "unregistered",
                "is_mock": None,
                "note": "Target was not registered. Provenance is UNKNOWN, so this run "
                        "cannot support any claim: the harness will not guess whether the "
                        "subject was a mock."}
    return {"target_id": spec.target_id, "version": spec.version, "kind": spec.kind,
            "is_mock": spec.is_mock, "description": spec.description}


def _undeclared_pack() -> dict:
    return {"pack_id": None, "clinician_reviewed": None,
            "note": "No case-pack descriptor supplied. Provenance is UNKNOWN; build one "
                    "with caeval.casepack.build() before using this run for anything."}


def coverage(cases, stress_tests, skipped) -> dict:
    """What was NOT run, stated positively. A silent cap reads as full coverage."""
    total = sum(len(c.worlds) for c in cases) * len(stress_tests)
    by_test = {}
    for s in skipped:
        by_test.setdefault(s["stress_test"], []).append(f"{s['case_id']}/{s['world_id']}")
    return {
        "stress_cells_possible": total,
        "stress_cells_run": total - len(skipped),
        "stress_cells_skipped": len(skipped),
        "skipped_by_test": {k: sorted(v) for k, v in sorted(by_test.items())},
        "note": ("" if not skipped else
                 "Skipped cells are cases that cannot support the condition (no spontaneous "
                 "fact to withhold, no sibling world, no declared trajectory). They are NOT "
                 "passes and must not be read as coverage."),
    }


def summarize(scores) -> dict:
    """Safety and usefulness summarized SEPARATELY. No combined score is emitted.

    An EMPTY slice reports `None` (NA), never 0.0. A zero failure rate over zero
    episodes is not a clean result — it is the absence of a result, and rendering
    it as 0.0 makes a stress condition that never ran look like one the product
    passed.
    """
    n = len(scores)
    safety_keys = scores[0]["safety"].keys() if scores else []
    use_keys = scores[0]["usefulness"].keys() if scores else []

    def rate(f):
        return round(sum(f(s) for s in scores) / n, 4) if n else None

    return {
        "n_episodes": n,
        "safety": {k: rate(lambda s, k=k: s["safety"][k]) for k in safety_keys},
        "usefulness": {k: rate(lambda s, k=k: s["usefulness"][k]) for k in use_keys},
        "any_safety_failure_rate": rate(lambda s: s["any_safety_failure"]),
        "any_usefulness_failure_rate": rate(lambda s: s["any_usefulness_failure"]),
        "episodes_needing_human_review": sum(
            1 for s in scores if s["assertions"]["needs_human_review"]),
        "note": "Safety and usefulness are reported separately and are never combined "
                "into a single score (EVAL_STANDARD.md §0)."
                + ("" if n else " NO EPISODES: every rate is NA, not 0."),
    }
