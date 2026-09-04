"""SDK, private vault, and Track B validation-scaffold guards.

The properties tested here are the ones the whole assurance argument rests on:
capability fail-closed, structural blinding, role separation, and the
preregistration lock.
"""
import json
import tempfile
import unittest
from pathlib import Path

from caeval import family_sdk, pipeline, study, vault
from caeval.vault import AccessContext
from targets import demo_target


class TestFamilySDK(unittest.TestCase):
    RUNNABLE = ["missing_information", "conflicting_evidence"]
    # patient_red_flag moved RUNNABLE in v0.12 when caeval/patient/ implemented the
    # four capabilities it had declared missing. It is still `experimental` — see
    # test_runnable_patient_family_is_still_experimental below, which is the guard
    # that actually matters now.
    RUNNABLE_EXPERIMENTAL_PATIENT = ["patient_red_flag"]
    BLOCKED = ["decision_certifiability"]

    def test_shipped_families_validate(self):
        for fid in self.RUNNABLE:
            fam = family_sdk.load(fid)          # raises if the declaration is invalid
            self.assertTrue(fam.version)
            self.assertTrue(fam.supported()[0])

    def test_design_targets_fail_closed(self):
        for fid in self.BLOCKED:
            fam = family_sdk.YamlFamily(family_sdk.load_family_definition(fid))
            ok, why = fam.supported()
            self.assertFalse(ok, f"{fid} must NOT be runnable")
            self.assertIn("does not provide", why)
            # loading through the pipeline must raise, not half-run
            with self.assertRaises(family_sdk.UnsupportedCapabilityError):
                pipeline.load_family(fid)

    def test_design_targets_are_not_selectable(self):
        """applies_to_profiles must be empty so nothing can route to them."""
        for fid in self.BLOCKED:
            d = family_sdk.load_family_definition(fid)
            self.assertEqual(d.get("applies_to_profiles", []), [])

    def test_runnable_patient_family_is_still_experimental(self):
        """Runnable is not validated. Implementing the machinery must never, by
        itself, raise a family's maturity — that takes clinician evidence."""
        for fid in self.RUNNABLE_EXPERIMENTAL_PATIENT:
            d = family_sdk.load_family_definition(fid)
            self.assertTrue(family_sdk.YamlFamily(d).supported()[0])
            self.assertEqual(d["maturity"]["level"], "experimental", fid)
            self.assertFalse(d["case_source"]["clinician_reviewed"], fid)
            self.assertTrue(d["maturity"]["evidence_required"]["calibrated"], fid)

    def test_patient_family_guards_against_blanket_referral(self):
        """A patient family whose only bar is under-triage can be passed by
        referring everyone to the emergency department."""
        d = family_sdk.load_family_definition("patient_red_flag")
        metrics = {h["acceptance_criterion"]["metric"] for h in d["hazards"]}
        self.assertIn("over_triage_rate", metrics)
        self.assertIn("no_disposition_rate", metrics)

    def test_declaration_requires_all_schema_keys(self):
        d = family_sdk.load_family_definition("missing_information")
        d.pop("required_capabilities")
        with self.assertRaises(family_sdk.FamilyDefinitionError):
            family_sdk.YamlFamily(d).validate_definition()

    def test_hazards_must_have_predeclared_criteria(self):
        d = family_sdk.load_family_definition("missing_information")
        d["hazards"] = [{"hazard_id": "H-X", "acceptance_criterion": {"metric": "m"}}]  # no operator/threshold
        with self.assertRaises(family_sdk.FamilyDefinitionError):
            family_sdk.YamlFamily(d).validate_definition()

    def test_generate_variants_matches_pipeline(self):
        """SDK variant generation is the same machinery the pipeline uses."""
        fam = family_sdk.load("missing_information")
        cases = demo_target.base_cases()
        sdk_rows = [r for c in cases for r in fam.generate_variants(c)]
        pipe_rows, _ = pipeline.build_manifest(fam.d, cases)
        self.assertEqual(sorted(r["perturbation_id"] for r in sdk_rows),
                         sorted(r["perturbation_id"] for r in pipe_rows))


class TestVaultBoundaries(unittest.TestCase):
    def _vault(self):
        d = Path(tempfile.mkdtemp())
        (d / "suites/S/cases").mkdir(parents=True)
        (d / "runs").mkdir()
        (d / "suites/S/suite.json").write_text(json.dumps(
            {"suite_id": "S", "family_id": "missing_information", "locked": True}))
        (d / "suites/S/cases/c1.json").write_text(json.dumps({
            "suite_id": "S", "input_text": "case text",
            "hazard": {"hazard_id": "H-RENAL-001"},
            "defect": {"defect_status": "injected", "defect_class": "x",
                       "implementation_author": "dev_independent"}}))
        (d / "runs/R1.json").write_text(json.dumps({"case_ids": ["c1"], "analysis_locked": False}))
        return d, vault.DirectoryVault(d)

    def ctx(self, role="analysis"):
        return AccessContext(role=role, actor_id="tester", run_id="R1")

    def test_subject_sees_only_the_facing_input(self):
        _, v = self._vault()
        p = v.get_subject_payload("c1", self.ctx("evaluated_system"))
        self.assertEqual(set(p), {"case_id", "input_text"})
        self.assertNotIn("defect", p)

    def test_blinded_judge_gets_no_defect_spec_but_cued_does(self):
        _, v = self._vault()
        self.assertNotIn("defect_specification", v.get_rubric_payload("c1", "blinded", self.ctx("blinded_judge")))
        self.assertIn("defect_specification", v.get_rubric_payload("c1", "rubric_aware", self.ctx("rubric_aware_judge")))

    def test_case_refs_are_opaque(self):
        _, v = self._vault()
        ref = v.materialize_run("R1", "blinded_adjudicator")[0]
        self.assertNotIn("case text", repr(ref))
        self.assertNotIn("injected", repr(ref))

    def test_labels_refused_before_analysis_lock(self):
        d, v = self._vault()
        with self.assertRaises(vault.VaultError):
            v.reveal_labels("R1")                              # no assertion
        with self.assertRaises(vault.VaultError):
            v.reveal_labels("R1", after_analysis_lock=True)    # asserted but not actually locked
        (d / "runs/R1.json").write_text(json.dumps(
            {"case_ids": ["c1"], "analysis_locked": True, "analysis_lock_hash": "abc"}))
        out = v.reveal_labels("R1", after_analysis_lock=True,
                              context=self.ctx("analysis"), protocol_lock_hash="abc")
        self.assertEqual(out["labels"]["c1"]["defect_status"], "injected")

    def test_entitlements_fail_closed(self):
        with self.assertRaises(vault.VaultError):
            vault.authorize("evaluated_system", "labels")
        with self.assertRaises(vault.VaultError):
            vault.authorize("blinded_adjudicator", "defect")
        vault.authorize("analysis", "labels")   # allowed

    def test_unknown_role_rejected(self):
        _, v = self._vault()
        with self.assertRaises(vault.VaultError):
            v.materialize_run("R1", "some_random_role")


class TestStudyScaffold(unittest.TestCase):
    def _staffed(self):
        p = study.default_protocol("S1", "missing_information")
        p.roles.clinical_hazard_authors = ["hazard_author"]
        p.roles.defect_implementer = "independent_dev"
        p.roles.blinded_adjudicators = ["drA", "drB"]
        p.roles.tie_adjudicator = "drC"
        p.case_set_hash = study.hash_case_set(demo_target.base_cases())
        return p

    def test_unfilled_roles_block_but_allow_dry_runs(self):
        st = study.default_protocol("S0", "missing_information").status()
        self.assertFalse(st["validation_claim_allowed"])
        self.assertTrue(st["dry_run_allowed"])
        self.assertIn("independent defect implementer not assigned", st["blocked_reasons"])

    def test_implementer_must_be_independent_of_hazard_authors(self):
        p = self._staffed()
        p.roles.defect_implementer = "hazard_author"
        self.assertIn("defect implementer must be independent of the hazard authors",
                      p.status()["blocked_reasons"])

    def test_adjudicator_cannot_have_constructed_the_defects(self):
        p = self._staffed()
        p.roles.blinded_adjudicators = ["independent_dev", "drB"]
        self.assertTrue(any("would not be blind" in r for r in p.status()["blocked_reasons"]))

    def test_two_adjudicators_required(self):
        p = self._staffed()
        p.roles.blinded_adjudicators = ["drA"]
        self.assertTrue(any("2 blinded clinicians required" in r for r in p.status()["blocked_reasons"]))

    def test_cross_fitted_roles_allow_case_level_rotation(self):
        p = self._staffed()
        assignments = [
            {"source_id": "s1", "construct_reviewer": "drA", "response_reviewers": ["drB", "drC"]},
            {"source_id": "s2", "construct_reviewer": "drB", "response_reviewers": ["drA", "drC"]},
            {"source_id": "s3", "construct_reviewer": "drC", "response_reviewers": ["drA", "drB"]},
        ]
        p.roles.role_separation_mode = "cross_fitted"
        p.roles.crossfit_assignments_hash = study.validate_crossfit_assignments(assignments)
        p.roles.blinded_adjudicators = ["drA", "drB", "drC"]
        self.assertFalse(any("would not be blind" in r for r in p.status()["blocked_reasons"]))

    def test_crossfit_rejects_constructor_as_response_reviewer(self):
        with self.assertRaises(study.StudyBlocked):
            study.validate_crossfit_assignments([
                {"source_id": "s1", "construct_reviewer": "drA", "response_reviewers": ["drA", "drB"]}
            ])

    def test_lock_then_tamper_is_detected(self):
        p = self._staffed()
        p.lock()
        self.assertTrue(p.status()["validation_claim_allowed"])
        p.predeclared_thresholds["defect_detection_sensitivity"]["value"] = 0.1
        self.assertFalse(p.status()["validation_claim_allowed"])
        self.assertTrue(any("CHANGED after lock" in r for r in p.status()["blocked_reasons"]))

    def test_cannot_lock_without_case_set_or_outcomes(self):
        p = study.default_protocol("S2", "missing_information")
        with self.assertRaises(study.StudyBlocked):
            p.lock()                       # no case_set_hash
        p.case_set_hash = "abc"
        p.primary_outcomes = []
        with self.assertRaises(study.StudyBlocked):
            p.lock()                       # no predeclared primary outcome

    def test_analysis_is_marked_dry_run_when_blocked(self):
        p = study.default_protocol("S3", "missing_information")
        out = study.analyze_validation(p, {}, {})
        self.assertTrue(out["dry_run"])
        self.assertIn("NOT a validation finding", out["interpretation"])

    def test_require_claim_allowed_raises_when_blocked(self):
        with self.assertRaises(study.StudyBlocked):
            study.default_protocol("S4", "missing_information").require_claim_allowed()

    def test_case_set_hash_detects_edits(self):
        cases = demo_target.base_cases()
        h1 = study.hash_case_set(cases)
        cases[0]["input_text"] += " (edited after lock)"
        self.assertNotEqual(h1, study.hash_case_set(cases))


if __name__ == "__main__":
    unittest.main()
