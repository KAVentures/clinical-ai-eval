"""Prepared assessment bridge: canonical project, no automatic execution."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_manual as manual, buyer_plan, buyer_assessment as assess, intake, project
from caeval.util import repo_root
from targets import demo_target


class AssessmentHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "buyer"
        buyer.init(self.root, "Test", "Decision support", "ED", "SE")
        buyer.add_vendor(self.root, "vendor", "Candidate")
        plan = buyer_plan.preview(self.root, {
            "vendor_id": "vendor", "target_name": "Example product", "target_version": "1.2",
            "profiles": ["clinician_decision_support"],
            "answers": {q: "Synthetic answer " + q for q in intake.INTAKE_QUESTIONS},
        })
        pack = self.root / "approved-packs" / "fixture"
        pack.mkdir(parents=True)
        (pack / "cases.json").write_text(json.dumps(demo_target.base_cases()), encoding="utf-8")
        template = manual.prepare(self.root, plan["plan_id"], "fixture")
        template["submission"]["operator"] = "Fixture operator"
        template["submission"]["tested_product_version"] = "1.2"
        for row in template["submission"]["answers"]:
            row.update(status="provided", answer="Synthetic answer")
        self.template = template
        self.sub = manual.import_response(self.root, json.dumps(template).encode())
        panels = self.root / "approved-panels"
        panels.mkdir()
        self.panel = panels / "mock.toml"
        self.panel.write_bytes((repo_root() / "configs" / "judge_panel.toml").read_bytes())
        self.gov = {
            "influences_diagnosis_or_treatment": "yes",
            "clinician_reviews_every_response": "yes",
            "regulated_medical_device": "unsure",
            "deployment_jurisdictions": "SE",
        }

    def prepare(self):
        return assess.prepare(self.root, self.sub["submission_id"], "mock", self.gov)

    def test_bound_project_is_valid_and_unexecuted(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            result = self.prepare()
            self.assertEqual(result, self.prepare())
            self.assertEqual(result, assess.check(self.root, result["assessment_id"]))
        self.assertEqual(assess.panels(self.root), ["mock"])
        self.assertEqual(result["mode"], "demonstration")
        self.assertFalse(result["network_authorized"])
        self.assertFalse(result["decision_grade"])
        dest = self.root / "prepared-assessments" / result["assessment_id"]
        cfg = project.load(dest)
        self.assertEqual(cfg.validate(), [])
        self.assertEqual(cfg.subject["kind"], "manual")
        self.assertEqual(cfg.data["target"]["name"], "Example product")
        self.assertEqual(cfg.data["governance"], self.gov)
        self.assertEqual(json.loads((dest / "responses.json").read_text()),
                         manual.responses(self.root, self.sub["submission_id"]))
        self.assertFalse((dest / "out").exists())

    def test_prepared_project_tampering_is_rejected(self):
        result = self.prepare()
        dest = self.root / "prepared-assessments" / result["assessment_id"]
        (dest / "responses.json").write_text("{}")
        with self.assertRaises(buyer.BuyerError):
            assess.check(self.root, result["assessment_id"])

    def test_panel_drift_is_rejected(self):
        result = self.prepare()
        self.panel.write_bytes(self.panel.read_bytes() + b"\n# changed\n")
        with self.assertRaises(buyer.BuyerError):
            assess.check(self.root, result["assessment_id"])

    def test_missing_governance_unknown_panel_and_incomplete_response_fail(self):
        for governance in ({}, {**self.gov, "regulated_medical_device": "maybe"}):
            with self.assertRaises(buyer.BuyerError):
                assess.prepare(self.root, self.sub["submission_id"], "mock", governance)
        with self.assertRaises(buyer.BuyerError):
            assess.prepare(self.root, self.sub["submission_id"], "../mock", self.gov)
        self.template["submission"]["answers"][0].update(status="not_available", answer="")
        incomplete = manual.import_response(self.root, json.dumps(self.template).encode())
        with self.assertRaises(buyer.BuyerError):
            assess.prepare(self.root, incomplete["submission_id"], "mock", self.gov)

    def test_extra_pack_file_rejected(self):
        result = self.prepare()
        dest = self.root / "prepared-assessments" / result["assessment_id"]
        (dest / "pack" / "extra.json").write_text("{}")
        with self.assertRaises(buyer.BuyerError):
            assess.check(self.root, result["assessment_id"])


if __name__ == "__main__":
    unittest.main()
