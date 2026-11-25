"""Escalation inbox tests using real lifecycle records and verified-report fixtures."""
import io
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, institution_access as access
from caeval import institution_lifecycle as lifecycle, institution_escalation as inbox
from caeval import institution_web as web


class EscalationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/"workspace"
        buyer.init(self.root,"Synthetic hospital","Clinical AI evaluation","ED","SE")
        buyer.add_vendor(self.root,"candidate","Example candidate")
        self.password="Synthetic escalation password!"
        for uid,role in (("admin","admin"),("owner","operator"),("other","operator"),("observer","observer"),("reviewer","reviewer")):
            access.provision(self.root,uid,role,self.password)
        self.admin={"user_id":"admin","role":"admin"}
        self.owner={"user_id":"owner","role":"operator"}
        self.other={"user_id":"other","role":"operator"}
        self.observer={"user_id":"observer","role":"observer"}
        self.pilot=lifecycle.create_pilot(self.root,self.admin,{"vendor_id":"candidate","product_version":"1",
             "scope":"Synthetic scope","owner":"owner","review_date":"2000-01-01","rollback_plan":"Synthetic rollback"})
        self.item=lifecycle.add_item(self.root,self.admin,self.pilot["id"],{"kind":"incident","severity":"critical",
            "title":"Private incident text","action":"Private action text","owner":"owner","due":"2000-01-01"},1)
        self.app=web.InstitutionApp(self.root,"https://clinical.example")

    def notices(self,actor):
        return inbox.overview(self.root,actor)["alerts"]

    def request(self,path,uid,method="GET",form=None):
        token=access.login(self.root,uid,self.password)
        raw=urllib.parse.urlencode(form or {}).encode()
        env={"REQUEST_METHOD":method,"PATH_INFO":path,"QUERY_STRING":"","HTTP_HOST":"clinical.example",
             "HTTP_ORIGIN":"https://clinical.example","HTTP_COOKIE":web.COOKIE+"="+token,
             "CONTENT_TYPE":"application/x-www-form-urlencoded","CONTENT_LENGTH":str(len(raw)),
             "wsgi.input":io.BytesIO(raw)}
        result={}
        def start(status,headers):
            result["status"]=status
        result["body"]=b"".join(self.app(env,start)).decode()
        return result

    def test_scoped_acknowledgement_does_not_resolve_or_suppress_notice(self):
        rows=self.notices(self.owner)
        self.assertEqual(len(rows),3)
        alert=next(a for a in rows if a["kind"]=="blocking_register_item")
        self.assertEqual(alert["priority"],"critical")
        self.assertEqual(self.notices(self.other),[])
        with self.assertRaises(access.AccessDenied):
            inbox.acknowledge(self.root,self.other,alert["id"])
        with self.assertRaises(access.AccessDenied):
            inbox.acknowledge(self.root,self.observer,alert["id"])
        inbox.acknowledge(self.root,self.owner,alert["id"])
        inbox.acknowledge(self.root,self.owner,alert["id"])
        found=next(a for a in self.notices(self.owner) if a["id"]==alert["id"])
        self.assertIsNotNone(found["acknowledged_at"])
        self.assertIsNone(next(a for a in self.notices(self.admin) if a["id"]==alert["id"])["acknowledged_at"])
        p=lifecycle.overview(self.root,self.admin)["pilots"][0]
        self.assertEqual(p["state"],"paused")
        self.assertEqual(p["items"][0]["state"],"open")
        with access.db(self.root) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM escalation_acknowledgements").fetchone()[0],1)
        lifecycle.resolve_item(self.root,self.admin,self.item["id"],1,"Synthetic investigation completed")
        self.assertNotIn(alert["id"],{a["id"] for a in self.notices(self.owner)})
        with self.assertRaises(access.AccessDenied):
            inbox.acknowledge(self.root,self.owner,alert["id"])

    def test_new_pilot_revision_requires_new_acknowledgement(self):
        old=next(a for a in self.notices(self.owner) if a["kind"]=="pilot_review_overdue")
        inbox.acknowledge(self.root,self.owner,old["id"])
        lifecycle.add_item(self.root,self.admin,self.pilot["id"],{"kind":"risk","severity":"low",
            "title":"New risk","action":"Review","owner":"owner","due":"2030-01-01"},2)
        new=next(a for a in self.notices(self.owner) if a["kind"]=="pilot_review_overdue")
        self.assertNotEqual(old["id"],new["id"])
        self.assertIsNone(new["acknowledged_at"])

    def test_regression_notices_and_report_revision(self):
        report={"families":{"family":{"metric":{"status":"investigate_increase"},"other":{"status":"insufficient_coverage"}}}}
        schedule={"id":"monitor-fixture","pilot_id":self.pilot["id"],"enabled":True,"next_due":0,
                  "interval_seconds":3600,"occurrences":[{"id":"occ-fixture","state":"completed",
                    "due":0,"skipped_intervals":2,"report":report,"report_digest":"a"*64}]}
        with patch.object(inbox.monitoring,"overview",return_value=[schedule]):
            rows=self.notices(self.owner)
            kinds={a["kind"] for a in rows}
            self.assertTrue({"monitoring_due","monitoring_skipped","monitoring_deterioration","monitoring_insufficient_coverage"}<=kinds)
            alert=next(a for a in rows if a["kind"]=="monitoring_deterioration")
            inbox.acknowledge(self.root,self.owner,alert["id"])
            schedule["occurrences"][0]["report_digest"]="b"*64
            changed=next(a for a in self.notices(self.owner) if a["kind"]=="monitoring_deterioration")
            self.assertNotEqual(changed["id"],alert["id"])
            self.assertIsNone(changed["acknowledged_at"])
            schedule["occurrences"][0].update(state="invalid",report=None)
            kinds={a["kind"] for a in self.notices(self.owner)}
            self.assertIn("monitoring_invalid",kinds)
            self.assertNotIn("monitoring_deterioration",kinds)

    def test_browser_role_and_csrf_boundaries(self):
        res=self.request("/escalation","owner")
        self.assertEqual(res["status"],"200 OK")
        self.assertIn("Acknowledge notice",res["body"])
        self.assertNotIn("Private incident text",res["body"])
        self.assertNotIn("Private action text",res["body"])
        self.assertEqual(self.request("/escalation","reviewer")["status"],"403 Forbidden")
        self.assertNotIn("Acknowledge notice",self.request("/escalation","observer")["body"])
        alert=self.notices(self.owner)[0]
        self.assertEqual(self.request("/escalation-acknowledge","owner","POST",{"id":alert["id"]})["status"],"403 Forbidden")
        self.assertIsNone(self.notices(self.owner)[0]["acknowledged_at"])


if __name__=="__main__":
    unittest.main()
