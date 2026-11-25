"""Version planning never inherits evidence or execution approval."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import (buyer_agent as buyer,buyer_plan as plans,intake,
                    institution_access as access,version_reassessment as version)


class VersionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"buyer"
        buyer.init(self.root,"Synthetic institution")
        buyer.add_vendor(self.root,"fixture","Fictional product")
        access.provision(self.root,"operator","operator","Synthetic long password")
        self.actor={"user_id":"operator","role":"operator"}
        self.baseline=plans.preview(self.root,{"vendor_id":"fixture","target_name":"Product","target_version":"1",
            "profiles":["clinician_decision_support"],"answers":{q:"Fictional "+q for q in intake.INTAKE_QUESTIONS}})
        self.pid=self.baseline["plan_id"]

    def test_exact_preview_and_idempotent_freeze(self):
        args=(self.root,self.actor,self.pid,"2")
        with patch("urllib.request.urlopen",side_effect=AssertionError("no network")):
            p=version.preview(*args)
            self.assertFalse(p["frozen"])
            self.assertEqual(len(list((self.root/"plans").glob("*.json"))),1)
            self.assertFalse(p["prior_evidence_transferred"])
            with self.assertRaises(access.AccessDenied):
                version.freeze(*args,p["digest"])
            with self.assertRaises(access.AccessDenied):
                version.freeze(*args,"0"*64,confirmed=True)
            f=version.freeze(*args,p["digest"],confirmed=True)
            self.assertEqual(f,version.freeze(*args,p["digest"],confirmed=True))
            self.assertEqual(len(list((self.root/"plans").glob("*.json"))),2)
            self.assertEqual(plans.load(self.root,f["successor_id"])["plan"]["target"]["version"],"2")
            report=version.history(self.root,self.actor,self.pid)
            self.assertEqual(len(report["successors"]),1)
            self.assertIsNone(report["comparative_verdict"])
            self.assertTrue(all(not x["packages"] for x in report["successors"][0]["coverage"]["suites"]))
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit WHERE action=?",(version.EVENT,)).fetchone()[0],1)

    def test_exact_version_connectors_and_no_duplicate_scope(self):
        args=(self.root,self.actor,self.pid,"2")
        p=version.preview(*args)
        version.freeze(*args,p["digest"],confirmed=True)
        folder=self.root/"approved-connectors"
        folder.mkdir()
        cfg={"vendor_id":"fixture","target_name":"Product","target_version":"2",
             "url":"http://127.0.0.1:8769/evaluate","protocol":"clinical-ai-eval-candidate/1",
             "headers":{},"timeout_seconds":5}
        (folder/"new.json").write_text(json.dumps(cfg))
        (folder/"old.json").write_text(json.dumps({**cfg,"target_version":"1"}))
        self.assertEqual([x["connector_id"] for x in version.history(self.root,self.actor,self.pid)["successors"][0]["matching_staged_connectors"]],["new"])
        with self.assertRaises(access.AccessDenied):
            version.preview(self.root,self.actor,self.pid,"1")

    def test_discover_unplanned_versions_across_native_transports(self):
        folder=self.root/"approved-connectors"
        folder.mkdir()
        cfg={"vendor_id":"fixture","target_name":"Product","target_version":"2",
             "url":"http://127.0.0.1:8769/evaluate","protocol":"clinical-ai-eval-candidate/1",
             "headers":{},"timeout_seconds":5}
        (folder/"next.json").write_text(json.dumps(cfg))
        (folder/"unrelated.json").write_text(json.dumps({**cfg,"target_name":"Other","target_version":"9"}))
        (folder/"old.json").write_text(json.dumps({**cfg,"target_version":"1"}))
        conversation=self.root/"approved-conversations"
        conversation.mkdir()
        (conversation/"multi.json").write_text(json.dumps({**cfg,"target_version":"3",
             "protocol":"clinical-ai-eval-conversation/1"}))
        (conversation/"invalid.json").write_text(json.dumps({**cfg,"target_version":"4",
             "protocol":"clinical-ai-eval-conversation/1","url":"https://external.invalid/evaluate"}))
        result=version.history(self.root,self.actor,self.pid)
        self.assertEqual([r["version"] for r in result["staged_unplanned_versions"]],["2","3"])
        self.assertEqual(result["staged_unplanned_versions"][1]["connectors"],
             [{"connector_id":"multi","protocol":"clinical-ai-eval-conversation/1"}])
        p=version.preview(self.root,self.actor,self.pid,"2")
        version.freeze(self.root,self.actor,self.pid,"2",p["digest"],confirmed=True)
        self.assertEqual([r["version"] for r in version.history(self.root,self.actor,self.pid)["staged_unplanned_versions"]],["3"])

    def test_definition_drift_and_frozen_history(self):
        args=(self.root,self.actor,self.pid,"2")
        old=version.preview(*args)
        with patch.object(plans,"_rules_hash",return_value="f"*64):
            changed=version.preview(*args)
            self.assertTrue(changed["definition_delta"]["selection_rules_changed"])
            with self.assertRaises(access.AccessDenied):
                version.freeze(*args,old["digest"],confirmed=True)
            version.freeze(*args,changed["digest"],confirmed=True)
        report=version.history(self.root,self.actor,self.pid)
        self.assertFalse(report["successors"][0]["coverage"]["definitions_current"])
        with self.assertRaises(access.AccessDenied):
            version.freeze(*args,old["digest"],confirmed=True)

    def test_missing_audited_record_cannot_be_recreated(self):
        args=(self.root,self.actor,self.pid,"2")
        p=version.preview(*args)
        version.freeze(*args,p["digest"],confirmed=True)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM version_reassessments")
        with self.assertRaises(access.AccessDenied):
            version.history(self.root,self.actor,self.pid)
        with self.assertRaises(access.AccessDenied):
            version.freeze(*args,p["digest"],confirmed=True)

    def test_missing_successor_refused(self):
        args=(self.root,self.actor,self.pid,"2")
        p=version.preview(*args)
        version.freeze(*args,p["digest"],confirmed=True)
        (self.root/"plans"/(p["successor_id"]+".json")).unlink()
        with self.assertRaises(buyer.BuyerError):
            version.history(self.root,self.actor,self.pid)
        with self.assertRaises(buyer.BuyerError):
            version.freeze(*args,p["digest"],confirmed=True)
