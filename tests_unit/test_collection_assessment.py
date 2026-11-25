"""Complete collection -> canonical preparation; no external model calls."""
import json
import unittest
from unittest.mock import patch
from caeval import buyer_agent as buyer,buyer_connector as connector,buyer_assessment as assessment
from caeval import collection_assessment as handoff,buyer_jobs as jobs,institution_access as access
from tests_unit.test_buyer_assessment import AssessmentHandoffTests


class HandoffTests(unittest.TestCase):
    setUp=AssessmentHandoffTests.setUp

    def collected(self,incomplete=False):
        cfg={"vendor_id":"vendor","target_name":"Example product","target_version":"1.2",
             "url":"http://127.0.0.1:8769/evaluate","prompt_field":"prompt","answer_path":"answer",
             "headers":{},"timeout_seconds":5}
        folder=self.root/"approved-connectors"
        folder.mkdir()
        self.cfg=folder/"local.json"
        self.cfg.write_text(json.dumps(cfg))
        queued=connector.queue(self.root,self.template["request"]["exchange_id"],"local")
        with patch.object(connector,"_call",side_effect=RuntimeError("unavailable") if incomplete else None,
                          return_value="Synthetic candidate output"):
            return connector.execute(self.root,queued["id"],queued["binding_hash"],"Fixture operator")

    def test_end_to_end_collection_preparation_without_execution(self):
        row=self.collected()
        with patch.object(jobs,"execute",side_effect=AssertionError("must not execute")):
            doc=handoff.preview(self.root,row["id"],"mock",self.gov)
            self.assertFalse((self.root/"prepared-assessments").exists())
            with self.assertRaises(buyer.BuyerError):
                handoff.prepare(self.root,row["id"],"mock",self.gov,None,doc["digest"])
            result=handoff.prepare(self.root,row["id"],"mock",self.gov,None,doc["digest"],confirmed=True)
            self.assertEqual(result["assessment_id"],doc["assessment_id"])
            self.assertEqual(result,handoff.prepare(self.root,row["id"],"mock",self.gov,None,doc["digest"],confirmed=True))
            self.assertEqual(assessment.check(self.root,result["assessment_id"])["submission_id"],row["receipt"]["submission_id"])
            self.assertEqual(jobs.list_jobs(self.root),[])

    def test_drift_and_role_refused(self):
        row=self.collected()
        doc=handoff.preview(self.root,row["id"],"mock",self.gov)
        access.provision(self.root,"operator","operator","Synthetic long password")
        actor={"user_id":"operator","role":"operator"}
        with self.assertRaises(access.AccessDenied):
            handoff.prepare(self.root,row["id"],"mock",self.gov,None,doc["digest"],confirmed=True,actor=actor)
        self.panel.write_bytes(self.panel.read_bytes()+b"\n# changed\n")
        with self.assertRaises(buyer.BuyerError):
            handoff.prepare(self.root,row["id"],"mock",self.gov,None,doc["digest"],confirmed=True)
        cfg=json.loads(self.cfg.read_text())
        self.cfg.write_text(json.dumps({**cfg,"timeout_seconds":6}))
        with self.assertRaises(buyer.BuyerError):
            handoff.preview(self.root,row["id"],"mock",self.gov)

    def test_incomplete_collection_is_not_preparable(self):
        row=self.collected(incomplete=True)
        self.assertEqual(row["state"],"incomplete")
        with self.assertRaises(buyer.BuyerError):
            handoff.preview(self.root,row["id"],"mock",self.gov)


if __name__=="__main__":
    unittest.main()
