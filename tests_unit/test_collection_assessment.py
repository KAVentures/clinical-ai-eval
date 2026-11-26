"""Real canonical prompt exchange to collected response assessment handoff."""
import json
import unittest
from unittest.mock import patch

from caeval import (buyer_connector as connector,buyer_assessment as assessment,
                    collection_assessment as handoff,buyer_agent as buyer,
                    institution_access as access)
from tests_unit.test_buyer_assessment import AssessmentHandoffTests


class CollectionHandoffTests(unittest.TestCase):
    setUp=AssessmentHandoffTests.setUp

    def collect(self,fail=False):
        access.provision(self.root,"admin","admin","Synthetic long password")
        access.provision(self.root,"operator","operator","Synthetic long password")
        self.actor={"user_id":"admin","role":"admin"}
        folder=self.root/"approved-connectors"
        folder.mkdir()
        self.config=folder/"fixture.json"
        self.config.write_text(json.dumps({
            "vendor_id":"vendor","target_name":"Example product","target_version":"1.2",
            "url":"http://127.0.0.1:8769/evaluate","protocol":"clinical-ai-eval-candidate/1",
            "headers":{},"timeout_seconds":5}))
        row=connector.queue(self.root,self.sub["exchange_id"],"fixture")
        if fail:
            with patch.object(connector,"_call",side_effect=RuntimeError("fixture unavailable")):
                return connector.execute(self.root,row["id"],row["binding_hash"],"Fixture operator")
        with patch.object(connector,"_call",return_value="Synthetic collected response"):
            return connector.execute(self.root,row["id"],row["binding_hash"],"Fixture operator")

    def test_complete_collection_preview_and_approved_prepare(self):
        row=self.collect()
        self.assertEqual(row["state"],"complete_unscored")
        args=(self.root,self.actor,row["id"],"mock",self.gov)
        with patch.object(connector,"_call",side_effect=AssertionError("no model calls")):
            doc=handoff.preview(*args)
            self.assertFalse(doc["decision_grade"])
            with self.assertRaises(buyer.BuyerError):
                handoff.prepare(*args,doc["digest"])
            with self.assertRaises(buyer.BuyerError):
                handoff.prepare(*args,"0"*64,confirmed=True)
            self.assertEqual(handoff.prepare(*args,doc["digest"],confirmed=True),doc)
            self.assertEqual(handoff.prepare(*args,doc["digest"],confirmed=True),doc)
            self.assertEqual(assessment.check(self.root,doc["assessment_id"])["submission_id"],row["receipt"]["submission_id"])
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit WHERE action='collection-assessment-prepared'").fetchone()[0],1)

    def test_incomplete_collection_refused(self):
        row=self.collect(fail=True)
        self.assertEqual(row["state"],"incomplete")
        with self.assertRaises(buyer.BuyerError):
            handoff.preview(self.root,self.actor,row["id"],"mock",self.gov)

    def test_panel_drift_rejects_approved_preview(self):
        row=self.collect()
        args=(self.root,self.actor,row["id"],"mock",self.gov)
        doc=handoff.preview(*args)
        self.panel.write_bytes(self.panel.read_bytes()+b"\\n# changed\\n".replace(b"\\n",b"\n"))
        with self.assertRaises(buyer.BuyerError):
            handoff.prepare(*args,doc["digest"],confirmed=True)

    def test_operator_preview_but_not_prepare_and_connector_drift(self):
        row=self.collect()
        args=(self.root,{"user_id":"operator","role":"operator"},row["id"],"mock",self.gov)
        doc=handoff.preview(*args)
        with self.assertRaises(access.AccessDenied):
            handoff.prepare(*args,doc["digest"],confirmed=True)
        cfg=json.loads(self.config.read_text())
        self.config.write_text(json.dumps({**cfg,"timeout_seconds":6}))
        with self.assertRaises(buyer.BuyerError):
            handoff.preview(*args)

    def test_local_cli_contract_and_no_job_execution(self):
        from caeval import buyer_jobs as jobs
        row=self.collect()
        args=(self.root,None,row["id"],"mock",self.gov)
        with patch.object(jobs,"execute",side_effect=AssertionError("must not execute")):
            doc=handoff.preview(*args)
            self.assertFalse((self.root/"prepared-assessments").exists())
            self.assertEqual(handoff.prepare(*args,doc["digest"],confirmed=True),doc)
            self.assertEqual(jobs.list_jobs(self.root),[])
