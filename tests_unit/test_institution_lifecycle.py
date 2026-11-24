"""Institutional lifecycle acceptance, stale updates and evidence binding."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from caeval import buyer_agent as buyer, institution_access as access, institution_lifecycle as life


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"buyer"
        buyer.init(self.root,"Pilot","CDS","ED","SE")
        buyer.add_vendor(self.root,"a","Candidate")
        for user,role in (("admin","admin"),("operator","operator"),("observer","observer"),("reviewer","reviewer")):
            access.provision(self.root,user,role,"Synthetic test password!")
        self.admin={"user_id":"admin","role":"admin"}
        self.operator={"user_id":"operator","role":"operator"}
        self.observer={"user_id":"observer","role":"observer"}
        self.reviewer={"user_id":"reviewer","role":"reviewer"}
        self.spec={"vendor_id":"a","product_version":"1.2","scope":"Synthetic pilot",
                   "owner":"operator","review_date":"2030-01-01","rollback_plan":"Disable pilot locally"}
        self.pilot=life.create_pilot(self.root,self.operator,self.spec)
        self.item={"kind":"risk","severity":"high","title":"Synthetic risk",
                   "action":"Review evidence","owner":"operator","due":"2030-01-02"}

    def current(self):
        return life.overview(self.root,self.observer)["pilots"][0]

    def test_risk_pause_and_resolution_require_role_and_revision(self):
        risk=life.add_item(self.root,self.operator,self.pilot["id"],self.item,1)
        p=self.current()
        self.assertEqual(p["state"],"paused")
        self.assertEqual(p["blocking_open"],1)
        with self.assertRaises(access.AccessDenied):
            life.add_item(self.root,self.operator,p["id"],self.item,1)
        with self.assertRaises(access.AccessDenied):
            life.transition(self.root,self.admin,p["id"],p["revision"],"in_review","review")
        with self.assertRaises(access.AccessDenied):
            life.resolve_item(self.root,self.operator,risk["id"],1,"resolved")
        life.resolve_item(self.root,self.admin,risk["id"],1,"verified mitigation")
        p=self.current()
        self.assertEqual(p["blocking_open"],0)
        p=life.transition(self.root,self.admin,p["id"],p["revision"],"in_review","record review")
        self.assertEqual(p["state"],"in_review")
        p=life.transition(self.root,self.admin,p["id"],p["revision"],"closed","end pilot")
        with self.assertRaises(access.AccessDenied):
            life.add_item(self.root,self.admin,p["id"],self.item,p["revision"])
        events=life.history(self.root,self.admin,p["id"])
        self.assertTrue(any(e["action"]=="item-resolved" for e in events))
        self.assertFalse(life.overview(self.root,self.observer)["deployment_authorized"])

    def test_role_boundaries_and_invalid_inputs(self):
        with self.assertRaises(access.AccessDenied):
            life.create_pilot(self.root,self.observer,self.spec)
        with self.assertRaises(access.AccessDenied):
            life.overview(self.root,self.reviewer)
        with self.assertRaises(access.AccessDenied):
            life.history(self.root,self.operator,self.pilot["id"])
        for change in ({"vendor_id":"unknown"},{"owner":"reviewer"},{"review_date":"not-date"}):
            with self.assertRaises(access.AccessDenied):
                life.create_pilot(self.root,self.operator,{**self.spec,**change})

    def test_version_change_pauses_even_low_severity(self):
        life.add_item(self.root,self.operator,self.pilot["id"],
                      {**self.item,"kind":"version_change","severity":"low"},1)
        self.assertEqual(self.current()["state"],"paused")
        self.assertEqual(self.current()["blocking_open"],1)

    def test_retest_link_rechecks_integrity(self):
        with patch.object(life,"_evidence",return_value={"missing_information":"a"*64}):
            life.link_evidence(self.root,self.operator,self.pilot["id"],1,"job-fixture","retest")
            self.assertEqual(self.current()["evidence"][0]["integrity"],"matches")
        with patch.object(life,"_evidence",return_value={"missing_information":"b"*64}):
            self.assertEqual(self.current()["evidence"][0]["integrity"],"changed")
        with patch.object(life,"_evidence",side_effect=access.AccessDenied("unavailable")):
            self.assertEqual(self.current()["evidence"][0]["integrity"],"unavailable")

    def test_evidence_rejects_nonmatching_version(self):
        row={"status":"completed","result":{"packages":[{"family_id":"missing_information","manifest_hash":"a"*64}]}}
        with patch.object(life.buyer_jobs,"status",return_value=row), \
             patch.object(life.buyer_manual,"_read",return_value={"exchange_id":"fixture"}), \
             patch.object(life.buyer_manual,"_load",return_value={"vendor_id":"a","target":{"version":"2.0"}}):
            with self.assertRaises(access.AccessDenied):
                life._evidence(self.root,"job-fixture",self.pilot)


if __name__=="__main__":
    unittest.main()
