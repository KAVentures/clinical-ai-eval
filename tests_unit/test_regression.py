"""Version-to-version regression.

The weekly loop a health-AI team can run before any of this is procurement-grade.
The property that makes it meaningful: a delta is attributable to the PRODUCT only
if nothing else moved. Otherwise "we fixed it" may actually mean "we swapped judges".
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from caeval import pipeline, regression as reg, report
from caeval.subject import build_subject
from caeval.workspace import Workspace
from targets import demo_target

PANEL = {"min_distinct_providers": 2, "judges": [
    {"name": "mock_strict", "provider": "mock_a", "model": "s", "mock": True},
    {"name": "mock_lenient", "provider": "mock_b", "model": "l", "mock": True}]}


def _run(arm, family_id="missing_information", version="1.0"):
    d = Path(tempfile.mkdtemp())
    fam = pipeline.load_family(family_id)
    cases = demo_target.base_cases()
    spec = {"kind": "mock", "arm": arm, "name": "demo", "version": version}
    responses = pipeline.generate_responses(build_subject(spec), fam, cases)
    ws = Workspace(d).ensure()
    ws.write_responses(responses)
    ws.write_run_meta({"family_id": family_id, "subject_spec": spec,
                       "panel": {"names": [j["name"] for j in PANEL["judges"]],
                                 "judges": PANEL["judges"],
                                 "conformance_level": "L0", "all_mock": True}})
    scored = pipeline.score_responses(responses, PANEL)
    result = pipeline.analyze(scored, responses, fam, spec, PANEL)
    (ws.path / "analysis.json").write_text(json.dumps(result))
    report.build_evidence_package(result, fam, str(ws.path))
    return ws.path


class TestImprovementIsDetected(unittest.TestCase):
    def test_repair_shows_as_repaired_not_regression(self):
        c = reg.compare_runs(_run("flawed"), _run("repaired"))
        self.assertEqual(c["status"], reg.COMPARABLE)
        self.assertGreater(c["counts"]["repaired"], 0)
        self.assertEqual(c["counts"]["newly_failing"], 0)

    def test_genuine_repair_also_improves_helpfulness(self):
        """A real fix raises helpfulness; only an abstention trade lowers it."""
        c = reg.compare_runs(_run("flawed"), _run("repaired"))
        self.assertLess(c["dimensions"]["unsafe_overconfident"]["delta_pp"], 0)
        self.assertGreater(c["dimensions"]["identifies_removed_evidence"]["delta_pp"], 0)


class TestRegressionIsDetected(unittest.TestCase):
    def test_bad_release_shows_newly_failing(self):
        c = reg.compare_runs(_run("repaired"), _run("flawed"))
        self.assertGreater(c["counts"]["newly_failing"], 0)
        self.assertEqual(c["counts"]["repaired"], 0)

    def test_regressions_carry_response_level_diffs(self):
        c = reg.compare_runs(_run("repaired"), _run("flawed"))
        regs = [d for d in c["response_diffs"] if d["direction"] == "REGRESSED"]
        self.assertTrue(regs)
        d = regs[0]
        for k in ("case_as_shown", "baseline_response", "candidate_response", "severity"):
            self.assertTrue(d[k] is not None, f"{k} missing from the diff")
        self.assertNotEqual(d["baseline_response"], d["candidate_response"])


class TestAbstentionTradeIsNotAWin(unittest.TestCase):
    """The trap: unsafe rate falls because the product refuses everything."""

    def test_safety_gain_by_refusal_is_visible_as_such(self):
        c = reg.compare_runs(_run("flawed"), _run("over_abstaining"))
        d = c["dimensions"]
        self.assertLess(d["unsafe_overconfident"]["delta_pp"], 0)          # looks like a win
        self.assertGreater(d["excessive_abstention"]["delta_pp"], 50)      # but
        self.assertLess(d["guideline_concordant_next_step"]["delta_pp"], 0)

    def test_dimensions_are_never_collapsed_into_one_number(self):
        c = reg.compare_runs(_run("flawed"), _run("repaired"))
        self.assertNotIn("overall_score", c)
        self.assertNotIn("safety_score", c)
        self.assertGreaterEqual(len(c["dimensions"]), 5)


class TestEnvironmentMustNotMove(unittest.TestCase):
    def test_different_family_blocks_attribution(self):
        c = reg.compare_runs(_run("flawed"), _run("repaired", family_id="conflicting_evidence"))
        self.assertEqual(c["status"], reg.ENVIRONMENT_CHANGED)
        self.assertNotIn("counts", c)
        self.assertTrue(any("family_id" in d for d in c["environment"]["differences"]))

    def test_changed_judge_prompt_is_detected(self):
        from caeval.util import repo_root
        base = _run("flawed")
        p = repo_root() / "prompts" / "judge_prompt.txt"
        original = p.read_text()
        try:
            p.write_text(original + "\n")
            cand = _run("repaired")
        finally:
            p.write_text(original)
        c = reg.compare_runs(base, cand)
        self.assertEqual(c["status"], reg.ENVIRONMENT_CHANGED)
        self.assertTrue(any("judge_prompt" in d for d in c["environment"]["differences"]))

    def test_forced_comparison_is_labelled_unattributable(self):
        c = reg.compare_runs(_run("flawed"), _run("repaired", family_id="conflicting_evidence"),
                             allow_environment_change=True)
        self.assertFalse(c["attributable_to_product"])
        self.assertIn("UNATTRIBUTABLE", reg.render_markdown(c))

    def test_missing_manifest_is_incomparable(self):
        base, cand = _run("flawed"), _run("repaired")
        (Path(base) / "assessment_manifest.json").unlink()
        c = reg.compare_runs(base, cand)
        self.assertEqual(c["status"], reg.INCOMPARABLE)


class TestIncompleteEvaluationIsNotSafe(unittest.TestCase):
    def test_incomplete_cells_are_indeterminate_not_passing(self):
        base, cand = _run("flawed"), _run("repaired")
        p = Path(cand) / "results.jsonl"
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        for r in rows[:3]:
            r["evaluation_complete"] = False
        p.write_text("\n".join(json.dumps(r) for r in rows))
        c = reg.compare_runs(base, cand, allow_environment_change=True)
        self.assertGreater(c["counts"]["indeterminate"], 0)


if __name__ == "__main__":
    unittest.main()
