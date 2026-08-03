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


class TestLimitationsMatchTheCode(unittest.TestCase):
    """A limitation that contradicts the implementation is drift, even when it
    understates rigor: readers quote limitations as fact."""

    def test_limitations_do_not_claim_judges_are_cued_by_default(self):
        from caeval.providers import DEFAULT_JUDGE_MODE
        from caeval import report as report_mod
        rr = _flawed_analysis()["flawed"]
        txt = report_mod._limitations_md(rr).lower()
        if DEFAULT_JUDGE_MODE == "blinded":
            self.assertNotIn("judges are metadata-informed", txt,
                             "limitations claim cued judges, but the default mode is blinded")
            self.assertIn("blinded", txt)

    def test_limitations_mention_the_cueing_split(self):
        from caeval import report as report_mod
        txt = report_mod._limitations_md(_flawed_analysis()["flawed"]).lower()
        self.assertIn("rubric-aware", txt)


class TestPatientTriageCoverageIsVisible(unittest.TestCase):
    """Missing coverage must be VISIBLE, never silently dropped.

    Adapted from an external review proposal. The proposal's framing was right —
    a regression here could make a patient-triage evidence package look more
    complete than it is — but its central assertion (that `missing_information`
    stays RUNNABLE for `patient_triage_chatbot`) is the opposite of the required
    behaviour: that family's patient bar names `missed_red_flag` and
    `over_reassurance`, which the scoring schema cannot produce, so the audience
    gate BLOCKS it (v0.3, CORRECTIONS.md §14.1). Asserting it runnable would
    re-introduce the audience fail-open one layer down.
    """

    PROFILE = "patient_triage_chatbot"

    def _sel(self):
        from caeval.selection import select_suites
        return select_suites([self.PROFILE])

    def test_patient_triage_yields_zero_runnable_suites(self):
        """The strong invariant: this build cannot evaluate patient-facing AI."""
        self.assertEqual(self._sel()["runnable_suites"], [],
                         "a patient-facing profile must yield NO runnable suites until "
                         "missed_red_flag/over_reassurance exist in the scoring schema")

    def test_missing_information_is_BLOCKED_not_runnable_for_patient_triage(self):
        sel = self._sel()
        blocked = {b["suite"]: b["blocked_reason"] for b in sel["required_but_not_run"]}
        self.assertIn("missing_information", blocked)
        self.assertNotIn("missing_information", sel["runnable_suites"])
        reason = blocked["missing_information"].lower()
        self.assertIn("patient", reason)
        for field in ("missed_red_flag", "over_reassurance"):
            self.assertIn(field, reason, "the blocked_reason must name the unscorable field")

    def test_red_flag_and_over_reassurance_stay_visible_as_required_but_not_run(self):
        """The proposal's good assertion: never silently drop these."""
        blocked = {b["suite"] for b in self._sel()["required_but_not_run"]}
        for suite in ("red_flag_detection", "over_reassurance"):
            self.assertIn(suite, blocked, f"{suite} vanished from the plan instead of "
                                          f"being reported as required-but-not-run")

    def test_every_blocked_suite_states_a_non_empty_reason(self):
        """The proposal's other good assertion."""
        for b in self._sel()["required_but_not_run"]:
            self.assertTrue(str(b.get("blocked_reason", "")).strip(),
                            f"{b['suite']} is blocked with no stated reason")

    def test_no_blocked_reason_claims_partial_coverage_by_a_blocked_family(self):
        """A blocked family cannot 'partially cover' anything — that wording made
        the plan look more complete than it is."""
        sel = self._sel()
        blocked_names = {b["suite"] for b in sel["required_but_not_run"]}
        for b in sel["required_but_not_run"]:
            reason = b["blocked_reason"].lower()
            if "partially covered by" in reason:
                for name in blocked_names:
                    self.assertNotIn(f"partially covered by {name}", reason,
                                     f"{b['suite']} claims coverage by {name}, which is itself blocked")


class TestInventoriesAgree(unittest.TestCase):
    """The SDK registry is canonical; selection_rules.yaml must mirror it."""

    def test_every_sdk_family_appears_in_selection_rules(self):
        import yaml
        from caeval import family_sdk
        rules = yaml.safe_load((repo_root() / "selection_rules.yaml").read_text())
        suites = rules["suites"]
        for fid in family_sdk.list_families():
            self.assertIn(fid, suites,
                          f"family '{fid}' is in the SDK registry but absent from selection_rules.yaml")

    def test_implemented_flags_match_capability_gate(self):
        import yaml
        from caeval import family_sdk
        rules = yaml.safe_load((repo_root() / "selection_rules.yaml").read_text())
        for row in family_sdk.family_status():
            declared = rules["suites"].get(row["family_id"], {}).get("implemented")
            self.assertEqual(bool(declared), row["runnable"],
                             f"{row['family_id']}: selection_rules says implemented={declared} "
                             f"but the capability gate says runnable={row['runnable']}")

    def test_blocked_families_have_a_stated_reason(self):
        import yaml
        rules = yaml.safe_load((repo_root() / "selection_rules.yaml").read_text())
        for name, meta in rules["suites"].items():
            if not meta.get("implemented"):
                self.assertTrue(meta.get("blocked_reason"), f"{name} blocked with no reason")


class TestSpecSupersession(unittest.TestCase):
    """EVAL_STANDARD.md must not re-assert claims that were retracted."""

    RETRACTED_PHRASES = ["weaker evidence than", "high-sensitivity, low-specificity",
                         "high-sensitivity/low-specificity"]
    RETRACTION_MARKERS = ["retracted", "withdrawn", "previously", "no longer", "do not assert"]

    SCANNED_GLOBS = ["*.md", "tests/**/*.yaml", "prompts/*.txt", "configs/*.toml",
                     "schemas/*.json", "pyproject.toml", "selection_rules.yaml"]

    def _public_docs(self):
        root = repo_root()
        seen = []
        for pat in self.SCANNED_GLOBS:
            for f in sorted(root.glob(pat)):
                if f.is_file() and "out/" not in str(f):
                    seen.append(f)
        return seen

    def test_retracted_claims_absent_from_ALL_public_docs(self):
        """The guard must cover every artifact a reader could quote, not just the
        spec — README drift recurred precisely because the scan was too narrow."""
        for f in self._public_docs():
            txt = f.read_text(errors="ignore")
            for para in txt.split("\n\n"):
                low = para.lower()
                if any(ph in low for ph in self.RETRACTED_PHRASES):
                    self.assertTrue(any(m in low for m in self.RETRACTION_MARKERS),
                                    f"{f.name} asserts a retracted claim without marking it:"
                                    f"\n---\n{para[:250]}\n---")

    def test_retracted_evaluator_claims_are_marked(self):
        """A retracted phrase may only appear inside a paragraph that marks it as
        retracted — never as a live assertion."""
        txt = (repo_root() / "EVAL_STANDARD.md").read_text()
        for para in txt.split("\n\n"):
            low = para.lower()
            if any(ph in low for ph in self.RETRACTED_PHRASES):
                self.assertTrue(any(m in low for m in self.RETRACTION_MARKERS),
                                f"EVAL_STANDARD.md asserts a retracted claim without marking it:"
                                f"\n---\n{para[:300]}\n---")

    def test_spec_declares_current_version_and_supersession(self):
        txt = (repo_root() / "EVAL_STANDARD.md").read_text()
        self.assertIn("v0.6", txt)
        self.assertIn("Supersession record", txt)
        self.assertNotIn("This is the single source of truth", txt)


if __name__ == "__main__":
    unittest.main()
