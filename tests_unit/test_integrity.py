"""Evidence-integrity guards.

The project's central promise is that its numbers mean what they say. These tests
enforce that promise mechanically: documented figures must be generated, endpoints
must be labelled, maturity must gate claims, and cued evaluators must never leak
into headline fields.
"""
import json
import unittest
from pathlib import Path

from caeval import fixtures, hazards, maturity, pipeline
from caeval.util import repo_root


def _flawed_analysis():
    from caeval.cli import build_fixture_analyses
    if not hasattr(_flawed_analysis, "_cache"):
        _flawed_analysis._cache = build_fixture_analyses()
    return _flawed_analysis._cache


class TestReadmeFixtures(unittest.TestCase):
    def test_readme_has_generated_markers(self):
        txt = (repo_root() / "README.md").read_text()
        self.assertIn(fixtures.BEGIN, txt)
        self.assertIn(fixtures.END, txt)

    def test_readme_block_matches_fresh_run(self):
        """The README's numbers must equal a fresh deterministic run's numbers."""
        block = fixtures.render_readme_block(_flawed_analysis())
        current = fixtures.extract_readme_block((repo_root() / "README.md").read_text())
        self.assertIsNotNone(current)
        self.assertEqual(current.strip(), block.strip(),
                         "README fixtures are STALE — run: python3 -m caeval.cli fixtures")

    def test_readme_quotes_no_bare_unsafe_rate(self):
        """Endpoint-labelling guard: the two endpoints legitimately differ, so the
        README must never call either simply 'the unsafe rate'."""
        txt = (repo_root() / "README.md").read_text().lower()
        self.assertNotIn("the unsafe rate is", txt)


class TestEndpointsAreDistinct(unittest.TestCase):
    def test_panel_any_and_mean_per_judge_are_separate_endpoints(self):
        a = _flawed_analysis()["flawed"]
        pa = fixtures.panel_any_rate(a)
        mj = fixtures.mean_per_judge_rate(a)
        # different denominators => different endpoints; both must be labelled
        self.assertNotEqual(pa.denominator, mj.denominator)
        self.assertIn("panel", pa.aggregation)
        self.assertIn("judge", mj.unit)
        for r in (pa, mj):
            self.assertIn("endpoint", r.labelled())


class TestCuedJudgeIsolation(unittest.TestCase):
    def test_cued_judges_do_not_leak_into_headline_fields(self):
        """A rubric-aware judge must not drive panel fields or review routing."""
        a = _flawed_analysis()["flawed"]
        blinded = set(a["panel"]["names"])
        for c in a["variant_cells"]:
            self.assertTrue(set(c.get("judge_scores", {})) <= blinded,
                            "cued judge leaked into headline judge_scores")
            # potentially_harmful_treatment is a REVIEW TRIGGER: it must be derivable
            # from the blinded scores alone.
            expect = int(any(s.get("potentially_harmful_treatment") == 1
                             for s in c.get("judge_scores", {}).values()))
            self.assertEqual(c.get("potentially_harmful_treatment"), expect)

    def test_cued_judges_excluded_from_quorum(self):
        panel = pipeline.load_panel()
        self.assertTrue(pipeline.cued_judges(panel), "fixture panel should include cued judges")
        headline = {j["name"] for j in pipeline.headline_judges(panel)}
        cued = {j["name"] for j in pipeline.cued_judges(panel)}
        self.assertFalse(headline & cued)
        self.assertEqual(set(pipeline.assess_panel(panel)["headline_judge_names"]), headline)


class TestMaturityGating(unittest.TestCase):
    FAMILIES = ["missing_information", "conflicting_evidence"]

    def test_all_families_declare_maturity(self):
        for f in self.FAMILIES:
            lvl = maturity.family_maturity(pipeline.load_family(f))
            self.assertIn(lvl, maturity.LEVELS)

    def test_experimental_family_cannot_carry_a_finding(self):
        for f in self.FAMILIES:
            fam = pipeline.load_family(f)
            if maturity.family_maturity(fam) == "experimental":
                with self.assertRaises(maturity.MaturityError):
                    maturity.require(fam, "published_finding")
                with self.assertRaises(maturity.MaturityError):
                    maturity.require(fam, "procurement_decision")
                maturity.require(fam, "regression_screen")   # allowed

    def test_experimental_verdicts_are_not_decision_grade(self):
        a = _flawed_analysis()["flawed"]
        fam = pipeline.load_family("missing_information")
        rep = hazards.evaluate_hazards(a, fam)
        self.assertFalse(rep["decision_grade"])
        self.assertIn("NOT decision-grade", rep["verdict_note"])


class TestHazardCriteria(unittest.TestCase):
    def test_every_hazard_has_a_predeclared_criterion(self):
        for f in ["missing_information", "conflicting_evidence"]:
            fam = pipeline.load_family(f)
            self.assertTrue(fam.get("hazards"), f"{f} declares no hazards")
            for hz in fam["hazards"]:
                c = hz.get("acceptance_criterion", {})
                for key in ("metric", "operator", "threshold"):
                    self.assertIn(key, c, f"{hz.get('hazard_id')} missing {key}")
                self.assertIn(hz.get("severity"), ("high", "moderate", "low"))

    def test_criteria_evaluate_with_denominators(self):
        a = _flawed_analysis()["flawed"]
        rep = hazards.evaluate_hazards(a, pipeline.load_family("missing_information"))
        scored = [r for r in rep["results"] if r["status"] in ("PASS", "FAIL")]
        self.assertTrue(scored)
        for r in scored:
            self.assertGreater(r["denominator"], 0)
            self.assertLessEqual(r["numerator"], r["denominator"])

    def test_flawed_arm_fails_a_high_severity_hazard(self):
        """Sanity: the deliberately-defective arm must trip at least one high-severity
        criterion, else the criteria are vacuous."""
        a = _flawed_analysis()["flawed"]
        rep = hazards.evaluate_hazards(a, pipeline.load_family("missing_information"))
        fails = [r for r in rep["results"] if r["status"] == "FAIL" and r["severity"] == "high"]
        self.assertTrue(fails)

    def test_over_abstaining_arm_trips_the_guard_hazard(self):
        """The reward-hacking guard must fire on the refuse-everything arm."""
        a = _flawed_analysis()["over_abstaining"]
        rep = hazards.evaluate_hazards(a, pipeline.load_family("missing_information"))
        guard = [r for r in rep["results"] if r["hazard_id"] == "H-OVERABSTAIN-001"]
        self.assertEqual(guard[0]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
