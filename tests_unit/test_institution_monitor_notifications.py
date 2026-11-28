"""Institutional notification inbox integration tests (synthetic only)."""
import json
import sqlite3
import unittest
from caeval import (institution_access as access, institution_lifecycle_monitor as monitor,
                    institution_monitor_notifications as notifications, monitor_alert_relay as relay)
from tests_unit.test_unified_assessment import UnifiedAssessmentTests
from tests_unit.test_lifecycle_monitor import spec
from tests_unit.test_monitor_alert_relay import envelope


class InstitutionNotificationTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    freeze=UnifiedAssessmentTests.freeze

    def prepare(self,provision=True):
        pid=self.plan()
        programme=self.freeze({"programme_id":"programme","plans":[pid],"requirements":[
            {"id":"fhir-a","kind":"fhir","plan_id":pid,"family":None}]})
        self.admin=self.actors["admin"]
        self.operator=self.actors["operator"]
        s=spec(programme)
        self.p=monitor.preview(self.root,self.admin,s)
        self.ident=monitor.freeze(self.root,self.admin,s,self.p["digest"],confirmed=True)
        self.path=notifications._path(self.root,self.ident)
        self.doc={**envelope(),"monitor_id":self.ident,"plan_digest":self.p["digest"]}
        self.attempt=self.doc["attempt_digest"]
        self.fields={"id":self.ident,"attempt":self.attempt,"payload_digest":relay.digest(self.doc),
                     "external_record_digest":"e"*64,"confirm":"yes"}
        if provision:
            self.path.parent.mkdir(mode=0o700)
            relay.initialize(self.path,self.ident,self.p["digest"])
            relay.accept(self.path,self.doc,self.attempt)

    def test_browser_review_roles_csrf_and_immutable_audit(self):
        self.prepare()
        url="/monitor-notifications?id="+self.ident
        detail="/monitor-notification-detail?id="+self.ident+"&attempt="+self.attempt
        for uid in ("admin","operator"):
            self.assertEqual(self.request(uid,url)["status"],"200 OK")
            self.assertEqual(self.request(uid,detail)["status"],"200 OK")
        for uid in ("observer","doctor-a"):
            self.assertEqual(self.request(uid,url)["status"],"403 Forbidden")
            self.assertEqual(self.post(uid,"/monitor-notification-review",self.fields)["status"],"403 Forbidden")
        self.assertEqual(self.request("operator","/monitor-notification-review","POST",self.fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/monitor-notification-review",{**self.fields,"payload_digest":"0"*64})["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/monitor-notification-review",self.fields)["status"],"303 See Other")
        self.assertEqual(self.post("operator","/monitor-notification-review",self.fields)["status"],"303 See Other")
        self.assertEqual(self.post("admin","/monitor-notification-review",self.fields)["status"],"403 Forbidden")
        result=notifications.detail(self.root,self.operator,self.ident,self.attempt)["review"]
        self.assertEqual(result["actor"],"operator")
        self.assertFalse(result["record"]["alert_resolved"])
        self.assertFalse(result["record"]["human_delivery_verified"])
        self.assertEqual(notifications.inbox(self.root,self.admin,self.ident)["snapshot"]["pending_count"],0)
        self.assertEqual(relay.inbox(self.path)["pending_count"],1)
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM monitor_notification_reviews").fetchone()[0],1)
            conn.execute("DELETE FROM monitor_notification_reviews")
        with self.assertRaises(access.AccessDenied):
            notifications.inbox(self.root,self.admin,self.ident)

    def test_absence_and_history_mismatch_are_not_empty_success(self):
        self.prepare(provision=False)
        row=notifications.inbox(self.root,self.admin,self.ident)
        self.assertEqual(row["relay_state"],"unavailable")
        self.assertIsNone(row["snapshot"])
        self.assertFalse(self.path.exists())
        self.path.parent.mkdir(mode=0o700)
        relay.initialize(self.path,self.ident,self.p["digest"])
        relay.accept(self.path,self.doc,self.attempt)
        self.assertEqual(self.post("operator","/monitor-notification-review",self.fields)["status"],"303 See Other")
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM batches")
        row=notifications.inbox(self.root,self.admin,self.ident)
        self.assertEqual(row["relay_state"],"history_mismatch")
        self.assertEqual(row["snapshot"]["missing_reviewed_count"],1)
        other={**self.doc,"attempt_digest":"d"*64,"sequence":2}
        relay.accept(self.path,other,other["attempt_digest"])
        self.assertEqual(self.post("operator","/monitor-notification-review",{**self.fields,
            "attempt":other["attempt_digest"],"payload_digest":relay.digest(other)})["status"],"403 Forbidden")
        self.path.unlink()
        row=notifications.inbox(self.root,self.admin,self.ident)
        self.assertEqual(row["reviewed_count"],1)
        self.assertEqual(row["recent_reviews"][0]["attempt"],self.attempt)
        self.assertIsNotNone(notifications.detail(self.root,self.admin,self.ident,self.attempt)["review"])


if __name__=="__main__":
    unittest.main()
