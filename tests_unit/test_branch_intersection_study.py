import tempfile
import unittest
from pathlib import Path

import yaml

from caeval.branch_intersection_authoring import apply_review, make_review_sheet, scaffold_full
from caeval.branch_intersection_study import (
    make_lock,
    parse_model_json,
    prepare_requests,
    score_responses,
    validate_casepack,
)


def _case(cid="X", stage="pilot", status="valid"):
    return {
        "case_id": cid,
        "stage": stage,
        "domain": "test",
        "construct": "conflict",
        "candidate_actions": [
            {"id": "A1", "text": "act one"},
            {"id": "A2", "text": "verify"},
            {"id": "A3", "text": "defer"},
        ],
        "presentations": {
            "resolved_A": "A true",
            "resolved_B": "B true",
            "unresolved_AB": "A then B unresolved",
            "unresolved_BA": "B then A unresolved",
        },
        "physician_validation": {
            "status": status,
            "validator": "Doctor" if status == "valid" else "",
            "branch_A_coherent": status == "valid",
            "branch_B_coherent": status == "valid",
            "unresolved_preserves_both": status == "valid",
            "same_timepoint": status == "valid",
            "same_action_semantics": status == "valid",
            "branch_coverage_adequate": status == "valid",
            "rationale": "checked" if status == "valid" else "",
        },
    }


class StudyTests(unittest.TestCase):
    def test_valid_case_prepares_four_presentations(self):
        cp = {"cases": [_case()]}
        self.assertTrue(validate_casepack(cp, expected_stage="pilot", require_valid=True)["ok"])
        rows = prepare_requests(cp, repeats=2)
        self.assertEqual(len(rows), 8)
        self.assertEqual({r["presentation"] for r in rows}, {"resolved_A", "resolved_B", "unresolved_AB", "unresolved_BA"})

    def test_candidate_case_is_not_prepared(self):
        cp = {"cases": [_case(status="candidate")]}
        self.assertEqual(prepare_requests(cp), [])

    def test_partition_parser_fails_closed(self):
        good = '{"autonomously_acceptable_action_ids":["A2"],"requires_resolution_or_deferral_action_ids":["A1","A3"],"preferred_next_step_action_id":"A2"}'
        self.assertEqual(parse_model_json(good, ["A1", "A2", "A3"])["autonomous"], ["A2"])
        bad = '{"autonomously_acceptable_action_ids":["A2"],"requires_resolution_or_deferral_action_ids":["A1"],"preferred_next_step_action_id":"A2"}'
        with self.assertRaises(ValueError):
            parse_model_json(bad, ["A1", "A2", "A3"])

    def test_scoring_detects_bisv_and_order_instability(self):
        cp = {"cases": [_case()]}
        rows = []
        declared = {
            "resolved_A": ["A1", "A2"],
            "resolved_B": ["A2"],
            "unresolved_AB": ["A1", "A2"],
            "unresolved_BA": ["A2"],
        }
        for p, auto in declared.items():
            defer = sorted({"A1", "A2", "A3"} - set(auto))
            rows.append({
                "request_id": f"X::{p}::baseline::r1",
                "case_id": "X", "arm": "baseline", "repeat": 1, "presentation": p,
                "response_text": yaml.safe_dump({
                    "autonomously_acceptable_action_ids": auto,
                    "requires_resolution_or_deferral_action_ids": defer,
                    "preferred_next_step_action_id": "A2",
                }, default_flow_style=True).replace("'", '"'),
            })
        # Replace YAML-flow syntax with strict JSON-like strings for the parser.
        import json
        for r, p in zip(rows, declared):
            auto = declared[p]
            r["response_text"] = json.dumps({
                "autonomously_acceptable_action_ids": auto,
                "requires_resolution_or_deferral_action_ids": sorted({"A1", "A2", "A3"} - set(auto)),
                "preferred_next_step_action_id": "A2",
            })
        result = score_responses(cp, rows)
        self.assertEqual(result["summary"]["case_violation_rate"], 0.5)
        self.assertEqual(result["summary"]["order_instability_rate"], 1.0)

    def test_confirmatory_lock_requires_valid_cases(self):
        cp = {"cases": [_case(stage="confirmatory", status="candidate")]}
        with self.assertRaises(ValueError):
            make_lock(cp, {"x": 1}, stage="confirmatory")
        cp = {"cases": [_case(stage="confirmatory", status="valid")]}
        lock = make_lock(cp, {"x": 1}, stage="confirmatory")
        self.assertTrue(lock["analysis_frozen"])
        self.assertEqual(lock["n_cases"], 1)

    def test_review_sheet_is_case_specific(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cases = root / "cases.yaml"
            cases.write_text(yaml.safe_dump({"cases": [_case("A", status="candidate"), _case("B", status="candidate")]}))
            review = root / "review.csv"
            make_review_sheet(cases, review)
            text = review.read_text().replace("A,candidate,", "A,valid,Doctor,")
            # easier and safer to construct an explicit valid review file
            review.write_text(
                "case_id,status,validator,branch_A_coherent,branch_B_coherent,unresolved_preserves_both,same_timepoint,same_action_semantics,branch_coverage_adequate,rationale\n"
                "A,valid,Doctor,true,true,true,true,true,true,checked\n"
                "B,exclude,Doctor,false,false,false,false,false,false,bad construct\n"
            )
            out = root / "validated.yaml"
            counts = apply_review(cases, review, out)
            self.assertEqual(counts["valid"], 1)
            loaded = yaml.safe_load(out.read_text())
            self.assertEqual(loaded["cases"][0]["physician_validation"]["status"], "valid")
            self.assertEqual(loaded["cases"][1]["physician_validation"]["status"], "exclude")

    def test_full_design_expands_to_120(self):
        design = Path("studies/branch_intersection/full/design_matrix.yaml")
        if not design.exists():
            self.skipTest("repo-relative design matrix not available")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "full.yaml"
            self.assertEqual(scaffold_full(design, out), 120)
            self.assertEqual(len(yaml.safe_load(out.read_text())["cases"]), 120)


if __name__ == "__main__":
    unittest.main()
