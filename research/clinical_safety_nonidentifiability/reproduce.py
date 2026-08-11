#!/usr/bin/env python3
"""Replay every finite claim in the hidden-fact impossibility package."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from caeval.identifiability import (  # noqa: E402
    ClinicalWorld,
    certified_label,
    find_witnesses,
    hitting_set_reduction,
    is_critical_question_closure,
    is_identifiable,
    minimum_critical_question_closures,
    randomized_bayes_error_lower_bound,
    randomized_worst_case_error_lower_bound,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_fixture(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_worlds(payload: Dict[str, Any]) -> List[ClinicalWorld]:
    return [
        ClinicalWorld(
            world_id=row["world_id"],
            observation=row["observation"],
            facts=row["facts"],
            labels=row["labels"],
        )
        for row in payload["worlds"]
    ]


def deterministic_evaluator_counterexample(worlds: List[ClinicalWorld], action: str) -> Dict[str, Any]:
    """Enumerate both deterministic labels available on one shared input."""

    observation = worlds[0].observation
    rows = []
    for predicted_unsafe in (0, 1):
        misclassified = [
            world.world_id
            for world in worlds
            if world.observation == observation
            and world.unsafe_label(action) != predicted_unsafe
        ]
        rows.append(
            {
                "prediction": predicted_unsafe,
                "misclassified_worlds": misclassified,
            }
        )
    return {
        "shared_observation": observation,
        "all_constant_predictions_fail": all(row["misclassified_worlds"] for row in rows),
        "predictions": rows,
    }


def reproduce(fixture_path: Path) -> Dict[str, Any]:
    payload = load_fixture(fixture_path)
    action = payload["action"]
    worlds = build_worlds(payload)
    queries = payload["candidate_queries"]

    witnesses = find_witnesses(worlds, action)
    closures = minimum_critical_question_closures(worlds, action, queries)
    observation = worlds[0].observation

    hs = payload["hitting_set_instance"]
    reduction_worlds, reduction_queries = hitting_set_reduction(
        hs["universe"], hs["subsets"], action=action
    )
    reduction_closures = minimum_critical_question_closures(
        reduction_worlds, action, reduction_queries
    )

    report: Dict[str, Any] = {
        "schema_version": "1.0",
        "fixture_sha256": sha256(fixture_path),
        "module_sha256": sha256(REPO_ROOT / "caeval" / "identifiability.py"),
        "finite_witness": {
            "identifiable_without_side_information": is_identifiable(worlds, action),
            "witness_count": len(witnesses),
            "witnesses": [witness.__dict__ for witness in witnesses],
            "randomized_worst_case_error_lower_bound": randomized_worst_case_error_lower_bound(
                worlds, action
            ),
            "randomized_bayes_error_lower_bound_equal_prior": randomized_bayes_error_lower_bound(
                worlds, action, unsafe_prior=0.5
            ),
            "deterministic_enumeration": deterministic_evaluator_counterexample(
                worlds, action
            ),
        },
        "critical_question_closure": {
            "renal_status_alone_sufficient": is_critical_question_closure(
                worlds, action, ["renal_status"]
            ),
            "contraindication_status_alone_sufficient": is_critical_question_closure(
                worlds, action, ["contraindication_status"]
            ),
            "minimum_closures": [list(item) for item in closures],
            "certified_before_answer": certified_label(
                worlds, action, observation, {}
            ),
            "certified_if_present": certified_label(
                worlds,
                action,
                observation,
                {"contraindication_status": "present"},
            ),
            "certified_if_absent": certified_label(
                worlds,
                action,
                observation,
                {"contraindication_status": "absent"},
            ),
        },
        "np_hardness_reduction": {
            "universe_size": len(hs["universe"]),
            "query_count": len(reduction_queries),
            "world_count": len(reduction_worlds),
            "minimum_closures": [list(item) for item in reduction_closures],
            "matches_expected": [list(item) for item in reduction_closures]
            == hs["expected_minimum_closures"],
        },
    }

    assert report["finite_witness"]["identifiable_without_side_information"] is False
    assert report["finite_witness"]["witness_count"] >= 1
    assert report["finite_witness"]["randomized_worst_case_error_lower_bound"] == 0.5
    assert report["finite_witness"]["deterministic_enumeration"][
        "all_constant_predictions_fail"
    ] is True
    assert report["critical_question_closure"][
        "contraindication_status_alone_sufficient"
    ] is True
    assert report["critical_question_closure"]["renal_status_alone_sufficient"] is False
    assert report["critical_question_closure"]["certified_before_answer"] is None
    assert report["critical_question_closure"]["certified_if_present"] == 1
    assert report["critical_question_closure"]["certified_if_absent"] == 0
    assert report["np_hardness_reduction"]["matches_expected"] is True
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=HERE / "witnesses.json",
        help="Path to the finite-world witness fixture.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for the generated JSON report.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare semantic results with EXPECTED_RESULT.json.",
    )
    args = parser.parse_args()

    report = reproduce(args.fixture.resolve())
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)

    if args.check:
        expected_path = HERE / "EXPECTED_RESULT.json"
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        semantic = {
            "finite_witness": report["finite_witness"],
            "critical_question_closure": report["critical_question_closure"],
            "np_hardness_reduction": report["np_hardness_reduction"],
        }
        if semantic != expected:
            print("semantic result drift", file=sys.stderr)
            print(json.dumps({"expected": expected, "actual": semantic}, indent=2), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
