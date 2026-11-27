"""Retention maintenance: explicit policy, holds, protected evidence and tombstones."""
import json
import time
import unittest
from unittest.mock import patch

from caeval import (institution_retention as retention, institution_access as access,
                    institution_fhir as fhir, institution_committee as committee,
                    fhir_readiness)
from tests_unit import test_fhir_readiness as fixtures


class RetentionTests(unittest.TestCase):
    setUp=fixtures.FhirBrowserTests.setUp
    request=fixtures.FhirBrowserTests.request
    post=fixtures.FhirBrowserTests.post
    fixture=fixtures.FhirBrowserTests.fixture

    def spec(self,**changes):
        return {"expired_session_grace_days":0,"failure_retention_days":1,
                "synthetic_fhir_retention_days":1,"hold_all":False,
                "synthetic_holds":[],"rationale":"Synthetic institutional policy",**changes}

    def approve(self,**changes):
        data=retention.view(self.root,self.actors["admin"])
        previous=data["current"]["id"] if data["current"] else None
        return retention.approve(self.root,self.actors["admin"],self.spec(**changes),previous,confirmed=True)

    def record(self,mode="exact"):
        doc=fixtures.exact(self.plan) if mode=="exact" else fhir_readiness.template(self.plan)
        ident=fhir.submit(self.root,self.actors["operator"],self.pid,doc,confirmed=True)
        with access.db(self.root) as conn:
            conn.execute("UPDATE audit SET at=? WHERE action=? AND object_id=?",
                         (int(time.time())-3*86400,fhir.EVENT,ident))
        return ident,doc

    def preview(self):
        return retention.preview(self.root,services_stopped=True)

    def apply(self,doc):
        return retention.apply(self.root,doc["as_of"],doc["digest"],services_stopped=True,confirmed=True)

    def test_browser_admin_policy_and_stale_revision(self):
        self.fixture()
        fields={"previous":"","expired_session_grace_days":"0","failure_retention_days":"1",
                "synthetic_fhir_retention_days":"","rationale":"Fixture policy","confirm":"yes"}
        self.assertEqual(self.request("operator","/retention")["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/retention")["status"],"200 OK")
        self.assertEqual(self.request("admin","/retention-policy","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/retention-policy",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/retention-policy",fields)["status"],"303 See Other")
        self.assertEqual(self.post("admin","/retention-policy",fields)["status"],"403 Forbidden")
        self.assertIsNone(retention.view(self.root,self.actors["admin"])["current"]["policy"]["synthetic_fhir_retention_days"])

    def test_expired_records_only_and_tombstone(self):
        self.fixture()
        ident,doc=self.record()
        self.approve()
        now=int(time.time())
        with access.db(self.root) as conn:
            conn.execute("INSERT INTO sessions VALUES(?,?,?,?)",("expired","admin","fixture",now-100))
            conn.execute("INSERT INTO failures VALUES(?,?)",("old",now-3*86400))
            conn.execute("INSERT INTO failures VALUES(?,?)",("recent",now))
        preview=self.preview()
        self.assertEqual(preview["expired_sessions"],1)
        self.assertEqual(preview["obsolete_login_failures"],1)
        self.assertEqual([r["id"] for r in preview["synthetic_rehearsals_to_delete"]],[ident])
        with self.assertRaises(access.AccessDenied):
            retention.apply(self.root,preview["as_of"],"0"*64,services_stopped=True,confirmed=True)
        with self.assertRaises(access.AccessDenied):
            retention.apply(self.root,preview["as_of"],preview["digest"],services_stopped=True)
        result=self.apply(preview)
        self.assertEqual(result["synthetic_rehearsals_removed"],[ident])
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sessions WHERE digest='expired'").fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT count(*) FROM failures WHERE user_key='recent'").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT count(*) FROM synthetic_fhir_reports").fetchone()[0],0)
        self.assertEqual(access.session(self.root,self.tokens["admin"])["user_id"],"admin")
        rows=fhir.inventory(self.root,self.actors["observer"])
        self.assertEqual(rows[0]["state"],"deleted_under_retention_policy")
        self.assertNotIn("package",rows[0])
        for uid in ("operator","other"):
            with self.assertRaises(access.AccessDenied):
                fhir.submit(self.root,self.actors[uid],self.pid,doc,confirmed=True)

    def test_hold_and_snapshot_protection(self):
        self.fixture()
        held,_=self.record()
        self.approve(synthetic_holds=[held])
        self.assertEqual(self.preview()["held_rehearsals"],[held])
        self.approve(hold_all=True)
        self.assertTrue(self.preview()["hold_all"])
        self.assertFalse(self.preview()["synthetic_rehearsals_to_delete"])
        self.approve()
        committee.freeze(self.root,self.actors["admin"])
        doc=self.preview()
        self.assertEqual(doc["protected_rehearsals"],[held])
        self.assertFalse(doc["synthetic_rehearsals_to_delete"])
        self.apply(doc)
        self.assertEqual(fhir.detail(self.root,self.actors["admin"],held)["id"],held)

    def test_export_protection_and_policy_drift(self):
        self.fixture()
        ident,_=self.record()
        self.approve()
        old=self.preview()
        access.event(self.root,"admin","synthetic-fhir-evidence-exported",ident)
        with self.assertRaises(access.AccessDenied):
            self.apply(old)
        self.assertEqual(self.preview()["protected_rehearsals"],[ident])
        old=self.preview()
        self.approve(hold_all=True)
        with self.assertRaises(access.AccessDenied):
            self.apply(old)
        with self.assertRaises(access.AccessDenied):
            retention.preview(self.root)


if __name__=="__main__":
    unittest.main()
