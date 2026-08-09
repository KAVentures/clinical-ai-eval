"""Self-service intake guards.

Until v0.7 `plan`/`inspect`/`init` all read a hardcoded DEMO_TARGET_META, so a new
user could produce an authoritative-looking evaluation plan that described the DEMO
product rather than theirs. Everything downstream derives from the intake, so that
is the core self-service hazard.
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from caeval import project as P


def _filled(mode="demonstration", subject_kind="mock", reviewers=None):
    d = P.template("t", mode)
    d["target"] = {"name": "X", "version": "1", "vendor": "V"}
    d["target_profile"]["types"] = ["clinician_decision_support"]
    d["intake"] = {q: f"answer for {q}" for q in P.MANDATORY_INTAKE}
    d["governance"] = {q: "yes" for q in P.MANDATORY_GOVERNANCE}
    d["subject"]["kind"] = subject_kind
    if subject_kind == "http":
        d["subject"]["url"] = "https://example.invalid/answer"
    if subject_kind in ("openai", "anthropic"):
        d["subject"]["model"] = "some-model"
    if reviewers:
        d["clinical_review"]["reviewers"] = reviewers
        # v0.17: two reviewers need a tie adjudicator who is not one of them.
        d["clinical_review"]["tie_reviewer"] = "drTie"
    return d


def _write(d):
    tmp = Path(tempfile.mkdtemp())
    (tmp / P.PROJECT_FILE).write_text(yaml.safe_dump(d))
    return P.load(tmp)


class TestIntakeFailsClosed(unittest.TestCase):
    def test_blank_template_is_not_usable(self):
        proj = _write(P.template("t"))
        problems = proj.validate()
        self.assertTrue(problems)
        with self.assertRaises(P.ProjectError):
            proj.require_valid()

    def test_every_mandatory_question_is_enforced(self):
        for q in P.MANDATORY_INTAKE:
            d = _filled()
            d["intake"][q] = ""
            self.assertTrue(any(q in x for x in _write(d).validate()), f"{q} not enforced")

    def test_unanswered_is_not_the_same_as_no(self):
        """A blank answer must block, not default."""
        for blank in ["", "   ", "TODO", "(not provided)", None]:
            d = _filled()
            d["intake"]["plausible_harm"] = blank
            self.assertTrue(_write(d).validate())

    def test_filled_project_is_valid(self):
        self.assertEqual(_write(_filled()).validate(), [])

    def test_profiles_required(self):
        d = _filled()
        d["target_profile"]["types"] = []
        self.assertTrue(any("target_profile.types" in x for x in _write(d).validate()))

    def test_unknown_profile_rejected(self):
        d = _filled()
        d["target_profile"]["types"] = ["not_a_real_profile"]
        self.assertTrue(any("unknown target profile" in x for x in _write(d).validate()))


class TestModeGuards(unittest.TestCase):
    def test_mock_subject_cannot_carry_a_non_demo_claim(self):
        for mode in ["internal_regression", "calibrated_assessment",
                     "procurement_comparison", "surveillance"]:
            d = _filled(mode=mode, subject_kind="mock", reviewers=["a", "b"])
            self.assertTrue(any("mock" in x for x in _write(d).validate()),
                            f"mode {mode} accepted a mock subject")

    def test_calibrated_requires_reviewers(self):
        d = _filled(mode="calibrated_assessment", subject_kind="http")
        self.assertTrue(any("clinical_review.reviewers" in x for x in _write(d).validate()))

    def test_calibrated_with_real_subject_and_reviewers_is_valid(self):
        d = _filled(mode="calibrated_assessment", subject_kind="http", reviewers=["drA", "drB"])
        self.assertEqual(_write(d).validate(), [])

    def test_one_reviewer_does_not_satisfy_the_two_clinician_gate(self):
        """The error text promised >=2 named clinicians while the check only tested
        for a non-empty list, so one reviewer passed a gate the message described
        as requiring two."""
        d = _filled(mode="calibrated_assessment", subject_kind="http", reviewers=["drA"])
        problems = _write(d).validate()
        self.assertTrue(any("TWO named clinicians" in x for x in problems), problems)

    def test_duplicate_reviewers_are_one_reviewer(self):
        d = _filled(mode="calibrated_assessment", subject_kind="http",
                    reviewers=["drA", "drA"])
        self.assertTrue(any("duplicates" in x for x in _write(d).validate()))

    def test_tie_adjudicator_is_required_and_must_be_independent(self):
        d = _filled(mode="calibrated_assessment", subject_kind="http",
                    reviewers=["drA", "drB"])
        d["clinical_review"]["tie_reviewer"] = ""
        self.assertTrue(any("tie_reviewer" in x for x in _write(d).validate()))
        d["clinical_review"]["tie_reviewer"] = "drA"
        self.assertTrue(any("party to" in x for x in _write(d).validate()))

    def test_product_identity_is_required(self):
        """Evidence must bind to a specific tested product and version."""
        for field in ("name", "version"):
            d = _filled(mode="demonstration")
            d["target"][field] = ""
            self.assertTrue(any(f"target.{field}" in x for x in _write(d).validate()))

    def test_vendor_required_beyond_demonstration(self):
        d = _filled(mode="calibrated_assessment", subject_kind="http",
                    reviewers=["drA", "drB"])
        d["target"]["vendor"] = ""
        self.assertTrue(any("target.vendor" in x for x in _write(d).validate()))

    def test_every_mode_has_a_claim_label(self):
        for mode in P.VALID_MODES:
            self.assertIn(mode, P.MODE_LABELS)
            self.assertTrue(_write(_filled(mode=mode, subject_kind="http",
                                           reviewers=["a", "b"])).claim_label())

    def test_demonstration_label_disclaims_clinical_evidence(self):
        self.assertIn("NOT CLINICAL EVIDENCE", P.MODE_LABELS["demonstration"])

    def test_procurement_label_disclaims_certification(self):
        self.assertIn("NOT REGULATORY CERTIFICATION", P.MODE_LABELS["procurement_comparison"])


class TestConnectorSpecValidation(unittest.TestCase):
    def test_http_requires_url(self):
        d = _filled(subject_kind="http")
        d["subject"].pop("url")
        self.assertTrue(any("subject.url" in x for x in _write(d).validate()))

    def test_provider_requires_model(self):
        d = _filled(subject_kind="openai")
        d["subject"].pop("model")
        self.assertTrue(any("subject.model" in x for x in _write(d).validate()))

    def test_manual_requires_responses_file(self):
        d = _filled(subject_kind="manual")
        self.assertTrue(any("responses_file" in x for x in _write(d).validate()))


class TestTargetMetaComesFromTheProject(unittest.TestCase):
    def test_target_meta_is_the_users_not_the_demo(self):
        from caeval.cli import DEMO_TARGET_META
        d = _filled()
        d["target"]["name"] = "AcmeTriage"
        meta = _write(d).target_meta
        self.assertEqual(meta["name"], "AcmeTriage")
        self.assertNotEqual(meta["name"], DEMO_TARGET_META["name"])
        for q in P.MANDATORY_INTAKE:
            self.assertNotEqual(meta[q], DEMO_TARGET_META.get(q),
                                f"{q} leaked from the demo constant")

    def test_plan_built_from_a_project_selects_from_its_profiles(self):
        from caeval.intake import build_eval_plan
        from caeval.selection import select_suites
        meta = _write(_filled()).target_meta
        plan = build_eval_plan(meta)
        self.assertEqual(plan["target_profile"]["types"], ["clinician_decision_support"])
        self.assertIn("missing_information", select_suites(plan["target_profile"]["types"])["runnable_suites"])

    def test_missing_project_file_is_a_clear_error(self):
        with self.assertRaises(P.ProjectError):
            P.load(Path(tempfile.mkdtemp()))

    def test_refuses_to_overwrite_an_existing_project(self):
        tmp = Path(tempfile.mkdtemp())
        P.write_template(tmp, "a")
        with self.assertRaises(P.ProjectError):
            P.write_template(tmp, "a")


if __name__ == "__main__":
    unittest.main()
