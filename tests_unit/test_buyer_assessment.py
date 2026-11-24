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

    def test_queue_and_execute_canonical_candidate_assessment_then_review(self):
        from caeval import buyer_jobs as jobs, buyer_physician_review as reviews
        bound = self.prepare()
        job = jobs.enqueue(self.root, bound["assessment_id"])
        self.assertEqual(job["status"], "queued")
        self.assertFalse(job["real_judges"])
        self.assertEqual(jobs.list_jobs(self.root)[0]["id"], job["id"])
        result = jobs.execute(self.root, job["id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertFalse(result["decision_grade"])
        self.assertEqual(jobs.status(self.root, job["id"]), result)
        family = result["result"]["packages"][0]["family_id"]
        doc = reviews.template(self.root, job["id"], family)
        self.assertTrue(doc["request"]["items"])
        for item in doc["request"]["items"]:
            self.assertEqual(set(item), {"slot", "case", "response"})
        doc["submission"]["reviewer_id"] = "reviewer-a"
        for item in doc["submission"]["verdicts"]:
            item.update(verdict="cannot_judge", notes="Fixture")
        saved = reviews.submit(self.root, doc)
        self.assertFalse(saved["identity_verified"])
        self.assertEqual(saved, reviews.submit(self.root, doc))
        doc["submission"]["verdicts"][0]["verdict"] = "safe"
        with self.assertRaises(buyer.BuyerError):
            reviews.submit(self.root, doc)
        doc["submission"]["reviewer_id"] = "reviewer-b"
        reviews.submit(self.root, doc)
        inv = reviews.inventory(self.root, job["id"], family)
        self.assertEqual(inv["n_submitted_reviewer_ids"], 2)
        self.assertFalse(inv["adjudicated"])
        self.assertEqual(jobs.status(self.root, job["id"]), result)
        ws = self.root / "evaluation-jobs" / job["id"] / "out" / ("run_" + family)
        (ws / "final_report.md").write_text("modified")
        self.assertEqual(jobs.status(self.root, job["id"])["status"], "invalid")
        with self.assertRaises(buyer.BuyerError):
            reviews.template(self.root, job["id"], family)

    def test_queue_cancel_and_duplicate_execution_fail_closed(self):
        from caeval import buyer_jobs as jobs
        bound = self.prepare()
        job = jobs.enqueue(self.root, bound["assessment_id"])
        self.assertEqual(jobs.cancel(self.root, job["id"])["status"], "cancelled")
        with patch.object(jobs.subprocess, "run") as runner:
            with self.assertRaises(buyer.BuyerError):
                jobs.execute(self.root, job["id"])
            runner.assert_not_called()
        job = jobs.enqueue(self.root, bound["assessment_id"])
        with patch.object(jobs.subprocess, "run") as runner:
            runner.return_value.returncode = 1
            self.assertEqual(jobs.execute(self.root, job["id"])["status"], "failed")
            with self.assertRaises(buyer.BuyerError):
                jobs.execute(self.root, job["id"])

    def test_real_judge_requires_explicit_permission(self):
        from caeval import buyer_jobs as jobs
        self.panel.write_text('min_distinct_providers = 1\n[[judges]]\nname = "real"\nprovider = "openai"\nmodel = "test"\nmock = false\n')
        bound = self.prepare()
        job = jobs.enqueue(self.root, bound["assessment_id"])
        self.assertTrue(job["real_judges"])
        with patch.object(jobs.subprocess, "run") as runner:
            with self.assertRaises(buyer.BuyerError):
                jobs.execute(self.root, job["id"])
            runner.assert_not_called()
        self.assertEqual(jobs.status(self.root, job["id"])["status"], "queued")

    def test_extra_pack_file_rejected(self):
        result = self.prepare()
        dest = self.root / "prepared-assessments" / result["assessment_id"]
        (dest / "pack" / "extra.json").write_text("{}")
        with self.assertRaises(buyer.BuyerError):
            assess.check(self.root, result["assessment_id"])


if __name__ == "__main__":
    unittest.main()
