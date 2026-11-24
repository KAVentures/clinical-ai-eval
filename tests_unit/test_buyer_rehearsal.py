"""Browser rehearsal exercises the existing offline scientific runner."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_plan, buyer_rehearsal, intake


class RehearsalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "buyer"
        buyer.init(self.root, "Test", "Decision support", "ED", "SE")
        buyer.add_vendor(self.root, "vendor", "Candidate")
        self.spec = {
            "vendor_id": "vendor", "target_name": "Real candidate must not be evaluated",
            "target_version": "1.2", "profiles": ["clinician_decision_support"],
            "answers": {q: "Synthetic answer " + q for q in intake.INTAKE_QUESTIONS},
        }

    def test_real_offline_execution_and_tamper_detection(self):
        plan = buyer_plan.preview(self.root, self.spec)
        result = buyer_rehearsal.run(self.root, plan["plan_id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertFalse(result["candidate_evaluated"])
        self.assertFalse(result["decision_grade"])
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual({p["family_id"] for p in result["packages"]},
                         set(plan["plan"]["selection"]["runnable_suites"]))
        self.assertTrue(all(p["effective_claim"] in ("demonstration", "none")
                            for p in result["packages"]))
        self.assertEqual(buyer_rehearsal.load(self.root, result["job_id"]), result)
        job = self.root / "rehearsals" / result["job_id"]
        config = json.loads((job / "project.yaml").read_text())
        self.assertNotIn(self.spec["target_name"], json.dumps(config))
        self.assertEqual(config["subject"]["kind"], "mock")
        self.assertEqual(config["project"]["mode"], "demonstration")
        self.assertFalse((self.root / "assessment").exists())
        package = job / "out" / ("run_" + result["packages"][0]["family_id"])
        (package / "final_report.md").write_text("modified")
        self.assertEqual(buyer_rehearsal.load(self.root, result["job_id"])["status"], "invalid")

    def test_stale_plan_rejected_before_invoking_runner(self):
        plan = buyer_plan.preview(self.root, self.spec)
        with patch.object(buyer_plan, "_rules_hash", return_value="different"):
            with patch.object(buyer_rehearsal.subprocess, "run") as invoke:
                with self.assertRaises(buyer.BuyerError):
                    buyer_rehearsal.run(self.root, plan["plan_id"])
                invoke.assert_not_called()

    def test_unsupported_profile_rejected_before_invoking_runner(self):
        plan = buyer_plan.preview(self.root, {**self.spec, "profiles": ["patient_triage_chatbot"]})
        with patch.object(buyer_rehearsal.subprocess, "run") as invoke:
            with self.assertRaises(buyer.BuyerError):
                buyer_rehearsal.run(self.root, plan["plan_id"])
            invoke.assert_not_called()

    def test_failed_subprocess_is_not_complete(self):
        plan = buyer_plan.preview(self.root, self.spec)
        with patch.object(buyer_rehearsal.subprocess, "run") as invoke:
            invoke.return_value.returncode = 2
            result = buyer_rehearsal.run(self.root, plan["plan_id"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["packages"])
        self.assertFalse(result["decision_grade"])

    def test_reject_real_judges_before_runner(self):
        plan = buyer_plan.preview(self.root, self.spec)
        with patch.object(buyer_rehearsal.pipeline, "load_panel",
                          return_value={"judges": [{"mock": False}]}):
            with patch.object(buyer_rehearsal.subprocess, "run") as invoke:
                with self.assertRaises(buyer.BuyerError):
                    buyer_rehearsal.run(self.root, plan["plan_id"])
                invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
