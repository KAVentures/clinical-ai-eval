"""Tests for the productionized stages: stats, checks, subject adapters, workspace
round-trip, generate/score separation, L2 adjudication, and the conflicting_evidence
family. Run: python3 -m unittest tests_unit.test_stages -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from caeval import checks, pipeline, report, stats
from caeval.adjudicate import adjudicate, mock_adjudicate
from caeval.subject import build_subject
from caeval.workspace import Workspace
from targets import demo_target

PANEL = {"min_distinct_providers": 2, "judges": [
    {"name": "mock_strict", "provider": "mock_a", "model": "s", "mock": True},
    {"name": "mock_lenient", "provider": "mock_b", "model": "l", "mock": True}]}


class TestStats(unittest.TestCase):
    def test_wilson(self):
        p, lo, hi = stats.wilson_ci(5, 10)
        self.assertAlmostEqual(p, 0.5, places=6)
        self.assertTrue(0 < lo < 0.5 < hi < 1)
        self.assertEqual(stats.wilson_ci(10, 10)[0], 1.0)

    def test_mcnemar(self):
        self.assertEqual(stats.mcnemar_exact_p(0, 0), 1.0)
        self.assertLess(stats.mcnemar_exact_p(10, 0), 0.01)
        self.assertAlmostEqual(stats.mcnemar_exact_p(3, 3), 1.0, places=6)

    def test_rate_ci(self):
        r = stats.rate_ci([1, 1, 0, 0])
        self.assertEqual(r["k"], 2)
        self.assertEqual(len(r["ci95"]), 2)


class TestChecks(unittest.TestCase):
    def test_hard_unsafe_flag(self):
        c = checks.deterministic_checks("The diagnosis is DVT. Prescribe enoxaparin 60 mg twice daily.",
                                        "missing_critical_lab", "critical laboratory results")
        self.assertEqual(c["det_hard_unsafe_flag"], 1)

    def test_over_abstain_flag(self):
        c = checks.deterministic_checks("There is insufficient information; I cannot help.",
                                        "missing_critical_lab", "critical laboratory results")
        self.assertEqual(c["det_hard_over_abstain_flag"], 1)


class TestSubjectAdapters(unittest.TestCase):
    def test_mock_adapter(self):
        subj = build_subject({"kind": "mock", "arm": "flawed"})
        txt = subj({"item_id": "c01", "perturbation_type": "missing_critical_lab",
                    "expected_missing_evidence": "critical laboratory results",
                    "input_text": "x", "ground_truth_label": "acute pancreatitis"})
        self.assertIn("diagnosis", txt.lower())

    def test_manual_adapter(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "resp.json"
            f.write_text(json.dumps({"c01::original": "canned answer"}))
            subj = build_subject({"kind": "manual", "responses_file": str(f)})
            self.assertEqual(subj({"cell_id": "c01::original"}), "canned answer")
            with self.assertRaises(KeyError):
                subj({"cell_id": "missing::x"})


class TestWorkspace(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            ws = Workspace(d).ensure()
            ws.write_responses([{"cell_id": "a", "x": 1}])
            ws.write_run_meta({"family_id": "missing_information"})
            self.assertTrue(ws.exists())
            self.assertEqual(ws.read_responses()[0]["cell_id"], "a")
            self.assertEqual(ws.read_run_meta()["family_id"], "missing_information")


class TestGenerateScoreSeparation(unittest.TestCase):
    def test_generate_then_score(self):
        family = pipeline.load_family("missing_information")
        cases = demo_target.base_cases()
        subj = build_subject({"kind": "mock", "arm": "flawed"})
        responses = pipeline.generate_responses(subj, family, cases)
        self.assertTrue(any(r["kind"] == "original" for r in responses))
        self.assertTrue(any(r["kind"] == "variant" for r in responses))
        scored = pipeline.score_responses(responses, PANEL)
        variants = [c for c in scored if c["kind"] == "variant"]
        # flawed subject -> some safe->unsafe flips vs the (safe) original
        self.assertTrue(any(c.get("flip_safe_to_unsafe") == 1 for c in variants))
        # deterministic checks carried through
        self.assertIn("det_hard_unsafe_flag", variants[0]["det_checks"])


class TestConflictingEvidence(unittest.TestCase):
    def test_flawed_vs_repaired(self):
        family = pipeline.load_family("conflicting_evidence")
        cases = demo_target.base_cases()
        for arm, expect_unsafe in [("flawed", True), ("repaired", False)]:
            subj = build_subject({"kind": "mock", "arm": arm})
            responses = pipeline.generate_responses(subj, family, cases)
            scored = pipeline.score_responses(responses, PANEL)
            result = pipeline.analyze(scored, responses, family, {"arm": arm}, PANEL)
            strict = result["dimensions"]["per_judge"]["mock_strict"]["safety"]["unsafe_overconfident"]
            if expect_unsafe:
                self.assertGreater(strict, 0.5)
            else:
                self.assertEqual(strict, 0.0)
                # repaired must identify the contradiction, not just abstain
                self.assertEqual(result["dimensions"]["per_judge"]["mock_strict"]["helpfulness"]["identifies_removed_evidence"], 1.0)


class TestAdjudicationL2(unittest.TestCase):
    def _build_workspace(self, d, arm="flawed"):
        family = pipeline.load_family("missing_information")
        cases = demo_target.base_cases()
        subj = build_subject({"kind": "mock", "arm": arm})
        responses = pipeline.generate_responses(subj, family, cases)
        ws = Workspace(d).ensure()
        ws.write_responses(responses)
        ws.write_run_meta({"family_id": "missing_information",
                           "subject_spec": {"kind": "mock", "arm": arm},
                           "panel": {"names": ["mock_strict", "mock_lenient"],
                                     "conformance_level": "L0", "all_mock": True}})
        scored = pipeline.score_responses(responses, PANEL)
        result = pipeline.analyze(scored, responses, family, {"kind": "mock", "arm": arm}, PANEL)
        (ws.path / "analysis.json").write_text(json.dumps({k: v for k, v in result.items() if k != "_response_rows"}))
        report.build_evidence_package(result, family, str(ws.path))
        return ws

    def test_strict_judge_is_high_sens_low_spec(self):
        with tempfile.TemporaryDirectory() as d:
            ws = self._build_workspace(d)
            files = mock_adjudicate(str(ws.path), n_reviewers=2)
            rep = adjudicate(str(ws.path), files)
            strict = rep["judge_vs_human"]["mock_strict"]
            # strict judge over-flags: catches all human-unsafe (sens 1.0), poor specificity
            self.assertEqual(strict["sensitivity"], 1.0)
            self.assertLess(strict["specificity"] or 0.0, 0.5)
            self.assertGreaterEqual(rep["queue_completion"], 0.8)


if __name__ == "__main__":
    unittest.main()
