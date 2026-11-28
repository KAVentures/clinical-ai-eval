"""Mixed programme is explicit, idempotent and never a clinical verdict."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from caeval import (buyer_agent as buyer,buyer_plan,intake,mixed_evaluation as mixed,
                    institution_access as access)


class MixedTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"buyer"
        buyer.init(self.root,"Synthetic institution")
        buyer.add_vendor(self.root,"fixture","Fictional product")
        access.provision(self.root,"operator","operator","Synthetic long password")
        self.actor={"user_id":"operator","role":"operator"}
        self.parent=buyer_plan.preview(self.root,{"vendor_id":"fixture","target_name":"Product","target_version":"1",
            "profiles":["clinician_decision_support","patient_triage_chatbot"],
            "answers":{q:"Fictional "+q for q in intake.INTAKE_QUESTIONS}})
        self.pid=self.parent["plan_id"]

    def test_pure_preview_explicit_freeze_and_coverage(self):
        args=(self.root,self.actor,self.pid)
        with patch("urllib.request.urlopen",side_effect=AssertionError("no network")):
            p=mixed.preview(*args)
            self.assertFalse(p["frozen"])
            self.assertEqual(len(p["lanes"]),2)
            self.assertEqual(len(list((self.root/"plans").glob("*.json"))),1)
            with self.assertRaises(access.AccessDenied):
                mixed.freeze(*args,p["digest"])
            with self.assertRaises(access.AccessDenied):
                mixed.freeze(*args,"0"*64,confirmed=True)
            frozen=mixed.freeze(*args,p["digest"],confirmed=True)
            self.assertEqual(frozen,mixed.freeze(*args,p["digest"],confirmed=True))
            self.assertEqual(len(list((self.root/"plans").glob("*.json"))),3)
            report=mixed.status(*args)
            self.assertFalse(report["all_runnable_lane_suites_have_packages"])
            self.assertFalse(report["parent_canonical_requirements_satisfied"])
            self.assertEqual(report["parent_required_but_not_run"],self.parent["plan"]["selection"]["required_but_not_run"])
            for lane in report["lanes"]:
                actual=buyer_plan.load(self.root,lane["plan_id"])
                self.assertEqual(actual["plan"]["target_profile"]["types"],[lane["profile"]])
                self.assertTrue(all(not e["packages"] for e in lane["evidence"]))
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit WHERE action=?",(mixed.EVENT,)).fetchone()[0],1)

    def test_missing_frozen_child_and_approval_refused(self):
        args=(self.root,self.actor,self.pid)
        p=mixed.preview(*args)
        mixed.freeze(*args,p["digest"],confirmed=True)
        child=self.root/"plans"/(p["lanes"][0]["plan_id"]+".json")
        child.unlink()
        with self.assertRaises(buyer.BuyerError):
            mixed.freeze(*args,p["digest"],confirmed=True)
        with self.assertRaises(buyer.BuyerError):
            mixed.status(*args)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM mixed_evaluation_programmes")
        with self.assertRaises(access.AccessDenied):
            mixed.preview(*args)
