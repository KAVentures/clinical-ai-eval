"""Guided onboarding freezes exact approved pack and connector."""
import json
import unittest
from unittest.mock import patch
from caeval import (guided_onboarding as guide,buyer_connector as connector,
                    buyer_agent as buyer,institution_access as access)
from tests_unit.test_buyer_assessment import AssessmentHandoffTests


class GuidedTests(unittest.TestCase):
    setUp=AssessmentHandoffTests.setUp

    def test_exact_preview_queue_idempotence_and_drift(self):
        access.provision(self.root,"operator","operator","Synthetic long password")
        actor={"user_id":"operator","role":"operator"}
        from caeval import buyer_manual as manual
        pid=manual._load(self.root,self.sub["exchange_id"])["plan_id"]
        folder=self.root/"approved-connectors"
        folder.mkdir()
        cfg={"vendor_id":"vendor","target_name":"Example product","target_version":"1.2",
             "url":"http://127.0.0.1:8769/evaluate","protocol":"clinical-ai-eval-candidate/1",
             "headers":{},"timeout_seconds":5}
        path=folder/"fixture.json"
        path.write_text(json.dumps(cfg))
        (folder/"other.json").write_text(json.dumps({**cfg,"target_version":"wrong"}))
        options=guide.options(self.root,actor,pid)
        self.assertEqual([c["connector_id"] for c in options["connectors"]],["fixture"])
        args=(self.root,actor,pid,"fixture","fixture")
        with patch.object(connector,"_call",side_effect=AssertionError("no candidate calls")):
            d=guide.preview(*args)
            with self.assertRaises(access.AccessDenied):
                guide.approve(*args,d["digest"])
            path.write_text(json.dumps({**cfg,"timeout_seconds":6}))
            with self.assertRaises(access.AccessDenied):
                guide.approve(*args,d["digest"],confirmed=True)
            path.write_text(json.dumps(cfg))
            a=guide.approve(*args,d["digest"],confirmed=True)
            b=guide.approve(*args,d["digest"],confirmed=True)
            self.assertEqual(a,b)
            self.assertEqual(a["state"],"queued")
            self.assertEqual(len(connector.list_collections(self.root)),1)
            self.assertFalse(a["network_authorized"])
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit WHERE action='guided-collection-approved'").fetchone()[0],1)
