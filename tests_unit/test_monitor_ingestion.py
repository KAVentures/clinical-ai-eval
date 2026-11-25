"""Synthetic scoped ingestion, transactional replay and minimal alert feed tests."""
import copy
import io
import json
import unittest
from unittest.mock import patch
from caeval import monitor_ingestion as api, institution_lifecycle_monitor as monitor
from caeval import institution_access as access
from tests_unit.test_lifecycle_monitor import spec, report, incident
from tests_unit.test_unified_assessment import UnifiedAssessmentTests


class IngestionTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    freeze=UnifiedAssessmentTests.freeze

    def prepare(self,scopes=("report","incident","check"),max_uses=3):
        pid=self.plan()
        programme=self.freeze({"programme_id":"programme","plans":[pid],"requirements":[
            {"id":"fhir-a","kind":"fhir","plan_id":pid,"family":None}]})
        self.admin=self.actors["admin"]
        self.operator=self.actors["operator"]
        s=spec(programme)
        self.p=monitor.preview(self.root,self.admin,s)
        self.ident=monitor.freeze(self.root,self.admin,s,self.p["digest"],confirmed=True)
        self.grant=api.issue(self.root,self.admin,{"monitor_id":self.ident,
            "owner":self.operator["user_id"],"scopes":list(scopes),"ttl_seconds":600,
            "max_uses":max_uses},confirmed=True)
        self.token=self.grant["bearer_token"]

    def batch(self,*events,request_id="request-one"):
        return {"schema":"lifecycle-ingestion/1","request_id":request_id,"events":[
            {"kind":kind,"document":doc} for kind,doc in events],"confirmed":True}

    def call(self,method,path,doc=None,**changes):
        raw=json.dumps(doc).encode() if doc is not None else b""
        env={"HTTP_HOST":"127.0.0.1:8772","HTTP_AUTHORIZATION":"Bearer "+self.token,
             "PATH_INFO":path,"REQUEST_METHOD":method,"CONTENT_TYPE":"application/json",
             "CONTENT_LENGTH":str(len(raw)),"wsgi.input":io.BytesIO(raw),**changes}
        result={}
        def start(status,headers):
            result["status"]=status
            result["headers"]=dict(headers)
        result["body"]=json.loads(b"".join(api.App(self.root,8772)(env,start)))
        return result

    def test_atomic_batch_and_idempotency(self):
        self.prepare()
        r=report(self.p)
        bad=report(self.p,1)
        bad["metrics"][0]["numerator"]=100
        with self.assertRaises(ValueError):
            api.submit(self.root,self.token,self.batch(("report",r),("report",bad)))
        self.assertEqual(len(monitor.detail(self.root,self.admin,self.ident)["records"]["report"]),0)
        self.assertEqual(api.grants(self.root,self.admin)[0]["used"],0)
        doc=self.batch(("report",r),("incident",incident(self.p)))
        receipt=api.submit(self.root,self.token,doc)
        self.assertEqual(receipt,api.submit(self.root,self.token,doc))
        self.assertEqual(len(receipt["event_digests"]),2)
        self.assertEqual(api.grants(self.root,self.admin)[0]["used"],1)
        changed=self.batch(("report",report(self.p,1)))
        with self.assertRaises(access.AccessDenied):
            api.submit(self.root,self.token,changed)
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM lifecycle_monitor_events").fetchone()[0],2)

    def test_scope_usage_and_revocation(self):
        self.prepare(scopes=("report",),max_uses=1)
        with self.assertRaises(ValueError):
            api.submit(self.root,self.token,self.batch(("incident",incident(self.p))))
        with self.assertRaises(access.AccessDenied):
            api.check(self.root,self.token)
        doc=self.batch(("report",report(self.p)))
        receipt=api.submit(self.root,self.token,doc)
        self.assertEqual(receipt,api.submit(self.root,self.token,doc))
        with self.assertRaises(access.AccessDenied):
            api.submit(self.root,self.token,self.batch(("report",report(self.p,1)),request_id="two"))
        api.revoke(self.root,self.admin,self.grant["grant_id"])
        with self.assertRaises(access.AccessDenied):
            api.submit(self.root,self.token,doc)

    def test_expiration_and_disabled_operator(self):
        self.prepare()
        with patch.object(api.time,"time",return_value=self.grant["expires"]):
            with self.assertRaises(access.AccessDenied):
                api.check(self.root,self.token)
        access.disable(self.root,self.operator["user_id"])
        with self.assertRaises(access.AccessDenied):
            api.check(self.root,self.token)

    def test_minimal_feed_stability_and_http_boundary(self):
        self.prepare()
        path="/v1/monitor/check"
        out=self.call("GET",path)
        self.assertEqual(out["status"],"200 OK")
        feed=out["body"]
        self.assertEqual(feed,api.check(self.root,self.token))
        self.assertTrue(feed["attention_required"])
        self.assertTrue(all(set(a)=={"id","kind","severity"} for a in feed["alerts"]))
        self.assertNotIn("numerator",json.dumps(feed))
        for changes in ({"HTTP_HOST":"example.org"},{"HTTP_COOKIE":"sid=test"},
                        {"HTTP_ORIGIN":"https://example.org"},{"HTTP_AUTHORIZATION":"Bearer invalid"},
                        {"QUERY_STRING":"token=x"}):
            self.assertEqual(self.call("GET",path,**changes)["status"],"403 Forbidden")
        result=self.call("POST","/v1/monitor/ingest",self.batch(("report",report(self.p))))
        self.assertEqual(result["status"],"200 OK")
        self.assertEqual(result["headers"]["Cache-Control"],"no-store")
        self.assertIn("/v1/monitor/ingest",api.openapi()["paths"])

    def test_grant_browser_secret_only_once(self):
        self.prepare()
        self.assertEqual(self.request("operator","/lifecycle-ingestion-grants")["status"],"403 Forbidden")
        out=self.request("admin","/lifecycle-ingestion-grants")
        self.assertEqual(out["status"],"200 OK")
        self.assertNotIn(self.token,str(out["body"]))
        form={"monitor_id":self.ident,"owner":self.operator["user_id"],"ttl_seconds":"600",
              "max_uses":"2","check":"yes","confirm":"yes"}
        self.assertEqual(self.request("admin","/lifecycle-ingestion-issue","POST",form)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/lifecycle-ingestion-issue",form)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/lifecycle-ingestion-issue",form)["status"],"200 OK")
        with access.db(self.root) as conn:
            row=conn.execute("SELECT token_hash FROM lifecycle_ingestion_grants WHERE id=?",(self.grant["grant_id"],)).fetchone()
            self.assertEqual(row["token_hash"],api._hash(self.token))
            self.assertNotEqual(row["token_hash"],self.token)

    def test_deleted_receipt_refuses_grant_replay(self):
        self.prepare()
        api.submit(self.root,self.token,self.batch(("report",report(self.p))))
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM lifecycle_ingestion_requests")
        with self.assertRaises(access.AccessDenied):
            api.check(self.root,self.token)


if __name__=="__main__":
    unittest.main()
