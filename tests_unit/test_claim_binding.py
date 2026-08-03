"""Workflow binding + claim authority.

The v0.8 defect: `run` accepted --family/--subject/--cases/--panel independently of
the validated project, so a user could plan one assessment and execute another, and
receive an evidence package not bound to the validated plan. Separately,
PRODUCT_V1.md promised the claim is the weakest of three axes "enforced in code"
while the report never received the project mode.
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from caeval import claim, project as P
from caeval.claim import PlanBindingError, build_binding, compute, permits, verify_binding


def _project(mode="demonstration", profiles=("clinician_decision_support",)):
    d = P.template("t", mode)
    d["target"] = {"name": "X", "version": "1", "vendor": "V"}
    d["target_profile"]["types"] = list(profiles)
    d["intake"] = {q: f"a {q}" for q in P.MANDATORY_INTAKE}
    d["governance"] = {q: "yes" for q in P.MANDATORY_GOVERNANCE}
    d["subject"]["kind"] = "mock"
    tmp = Path(tempfile.mkdtemp())
    (tmp / P.PROJECT_FILE).write_text(yaml.safe_dump(d))
    return P.load(tmp)


class TestClaimAuthorityIsTheWeakestAxis(unittest.TestCase):
    def test_weakest_axis_wins(self):
        cases = [
            ("demonstration", "L2", "validated", "demonstration", "project_mode"),
            ("calibrated_assessment", "L1", "experimental", "internal_regression", "family_maturity"),
            ("calibrated_assessment", "L2", "validated", "calibrated_assessment", "project_mode"),
            ("procurement_comparison", "L0", "qualification_ready", "demonstration", "run_conformance"),
        ]
        for mode, conf, mat, expected, axis in cases:
            a = compute(mode, conf, mat)
            self.assertEqual(a.effective_claim, expected, f"{mode}/{conf}/{mat}")
            self.assertEqual(a.limiting_axis, axis)

    def test_experimental_family_never_permits_a_clinical_finding(self):
        for mode in P.VALID_MODES:
            for conf in ("L0", "L1", "L2"):
                a = compute(mode, conf, "experimental")
                self.assertFalse(permits(a, "clinical finding"),
                                 f"{mode}/{conf}/experimental permitted a clinical finding")
                self.assertIn("clinical finding", a.blocked_claims)

    def test_demo_project_cannot_be_upgraded_by_a_strong_panel(self):
        a = compute("demonstration", "L2", "validated")
        self.assertEqual(a.effective_claim, "demonstration")
        self.assertEqual(a.permitted_claims, [])

    def test_every_claim_level_has_a_label(self):
        for level in claim.CLAIM_STRENGTH:
            self.assertIn(level, claim.CLAIM_LABELS)

    def test_unknown_axis_values_collapse_to_none(self):
        a = compute("not_a_mode", "L9", "not_a_level")
        self.assertEqual(a.effective_claim, "none")
        self.assertEqual(a.permitted_claims, [])


class TestPlanBinding(unittest.TestCase):
    def test_identical_plans_verify(self):
        p = _project()
        b = build_binding(p, "missing_information", ["a", "b"], "H1")
        verify_binding(b, build_binding(p, "missing_information", ["a", "b"], "H1"))

    def test_any_drift_blocks(self):
        p = _project()
        base = build_binding(p, "missing_information", ["a", "b"], "H1")
        drifts = [
            build_binding(p, "conflicting_evidence", ["a", "b"], "H1"),      # family
            build_binding(p, "missing_information", ["a", "c"], "H1"),        # panel
            build_binding(p, "missing_information", ["a", "b"], "H2"),        # case pack
        ]
        for actual in drifts:
            with self.assertRaises(PlanBindingError):
                verify_binding(base, actual)

    def test_binding_covers_every_decision_dimension(self):
        b = build_binding(_project(), "missing_information", ["a"], "H")
        for f in ("target_name", "target_version", "audience", "profiles", "family_id",
                  "subject_kind", "subject_fingerprint", "case_pack_hash",
                  "panel_names", "project_mode"):
            self.assertIn(f, b)
        self.assertTrue(b["plan_hash"])

    def test_subject_fingerprint_excludes_secrets(self):
        """Connector credentials must never enter the plan hash or provenance."""
        p = _project()
        p.data["subject"] = {"kind": "http", "url": "https://x/answer",
                             "headers": {"Authorization": "Bearer SUPERSECRET"}}
        b = build_binding(p, "missing_information", ["a"], "H")
        self.assertNotIn("SUPERSECRET", str(b))

    def test_audience_must_be_unambiguous(self):
        """A run binds exactly one audience — the failure bar differs by audience."""
        p = _project(profiles=("clinician_decision_support", "patient_triage_chatbot"))
        with self.assertRaises(PlanBindingError):
            build_binding(p, "missing_information", ["a"], "H")

    def test_incomplete_plan_cannot_be_fingerprinted(self):
        with self.assertRaises(PlanBindingError):
            claim.plan_fingerprint({"family_id": "x"})


class TestRunRefusesOverrides(unittest.TestCase):
    def test_overridable_fields_are_declared(self):
        from caeval.cli import OVERRIDABLE_BY_PROJECT
        for f in ("family", "subject", "cases", "panel"):
            self.assertIn(f, OVERRIDABLE_BY_PROJECT)


if __name__ == "__main__":
    unittest.main()
