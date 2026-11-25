"""Synthetic aggregate monitoring and audit lifecycle regressions."""
import copy
import json
import unittest
from caeval import lifecycle_monitor as m, institution_lifecycle_monitor as service
from caeval import diagnostic_imaging as d, institution_access as access
from tests_unit.test_unified_assessment import UnifiedAssessmentTests


def spec(programme="programme"):
    return {"monitor_id":"monitor-a","programme_id":programme,"vendor_id":"a","intended_use_id":"fictional",
            "start_date":"2020-01-01","window_days":7,"windows":3,"authorization_digest":"a"*64,
            "external_authorization_attested":True,"metrics":[
                {"id":"adverse","direction":"higher_is_worse","threshold_bp":1000,"min_denominator":10,"definition_digest":"b"*64},
                {"id":"success","direction":"lower_is_worse","threshold_bp":9000,"min_denominator":10,"definition_digest":"c"*64}]}


def plan():
    programme=d._sealed({"programme_id":"programme","plans":[{"vendor_id":"a","target":{"name":"Fictional","version":"v1"}}]})
    return programme,m.plan(programme,spec())


def report(p,index=0,n=1,den=10):
    return {"plan_digest":p["digest"],"target":p["target"],"window_index":index,"source_digest":"d"*64,
            "complete_aggregate_attested":True,"metrics":[{"id":"adverse","numerator":n,"denominator":den},
                 {"id":"success","numerator":den-n,"denominator":den}]}


def incident(p,changed=False):
    return {"plan_digest":p["digest"],"incident_id":"fictional-incident","date":"2020-01-05",
            "severity":"serious","category":"version_change" if changed else "clinical_safety",
            "external_record_digest":"e"*64,"observed_version":"v2" if changed else "v1"}


def action(p,alert,kind):
    return {"plan_digest":p["digest"],"alert_id":alert,"action":kind,"external_record_digest":"f"*64}


class MonitorTests(unittest.TestCase):
    def test_exact_threshold_and_missing_windows(self):
        _,p=plan()
        r=m.report(p,report(p))
        s=m.assess(p,[r],[],[],"2020-01-15")
        self.assertEqual({a["id"] for a in s["alerts"]},{"threshold:0:adverse","threshold:0:success","missing_window:1"})
        self.assertFalse(s["schedule_ended"])
        self.assertFalse(s["clinical_safety_established"])
        self.assertFalse(s["deployment_authorized"])

    def test_low_volume_and_future_window(self):
        _,p=plan()
        r=m.report(p,report(p,n=0,den=0))
        s=m.assess(p,[r],[],[],"2020-01-08")
        self.assertEqual({a["kind"] for a in s["alerts"]},{"low_volume"})
        self.assertIsNone(s["windows"][0]["metrics"][0]["rate"])
        with self.assertRaises(ValueError):
            m.assess(p,[r],[],[],"2020-01-07")

    def test_version_change_and_resolution_does_not_clear_threshold(self):
        _,p=plan()
        r=m.report(p,report(p))
        i=m.incident(p,incident(p,True))
        a=m.action(p,action(p,"version_change:fictional-incident","document_external_resolution"),{"version_change:fictional-incident"})
        s=m.assess(p,[r],[i],[a],"2020-01-08")
        self.assertTrue(s["version_reassessment_required"])
        self.assertEqual(s["windows"][0]["state"],"reported_version_overlap")
        self.assertTrue(s["attention_required"])

    def test_wrong_version_invalid_counts_and_missing_metric(self):
        _,p=plan()
        for mutation in (lambda r:r["target"].update(version="v2"),
                         lambda r:r["metrics"][0].update(numerator=11),
                         lambda r:r["metrics"].pop(),
                         lambda r:r.update(complete_aggregate_attested=False)):
            r=copy.deepcopy(report(p))
            mutation(r)
            with self.assertRaises(ValueError):
                m.report(p,r)

    def test_replay_and_late_report_history(self):
        programme,p=plan()
        r=m.report(p,report(p))
        a=m.action(p,action(p,"missing_window:0","acknowledge"),{"missing_window:0"})
        doc=m.package(programme,p,[r],[],[a],"2020-01-08")
        self.assertTrue(m.verify(doc,doc["digest"])["expected_digest_matched"])
        self.assertFalse(m.verify(doc)["source_records_verified"])
        bad=copy.deepcopy(doc)
        bad["status"]["clinical_safety_established"]=True
        bad["digest"]=d._digest({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError):
            m.verify(bad)


class InstitutionMonitorTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    freeze=UnifiedAssessmentTests.freeze

    def test_browser_and_audit_lifecycle(self):
        pid=self.plan()
        programme=self.freeze({"programme_id":"programme","plans":[pid],"requirements":[
            {"id":"fhir-a","kind":"fhir","plan_id":pid,"family":None}]})
        admin=self.actors["admin"]
        s=spec(programme)
        p=service.preview(self.root,admin,s)
        f={"spec":json.dumps(s)}
        self.assertEqual(self.post("operator","/lifecycle-monitor-preview",f)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/lifecycle-monitor-preview",f)["status"],"200 OK")
        f.update(approval=p["digest"],confirm="yes")
        self.assertEqual(self.request("admin","/lifecycle-monitor-freeze","POST",f)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/lifecycle-monitor-freeze",f)["status"],"303 See Other")
        ident=s["monitor_id"]
        self.assertEqual(self.request("observer","/lifecycle-monitor-detail?id="+ident)["status"],"403 Forbidden")
        a=action(p,"missing_window:0","acknowledge")
        service.record_action(self.root,admin,ident,a,confirmed=True)
        r=report(p)
        fields={"id":ident,"document":json.dumps(r),"confirm":"yes"}
        self.assertEqual(self.post("operator","/lifecycle-monitor-report",fields)["status"],"303 See Other")
        self.assertEqual(self.post("operator","/lifecycle-monitor-report",fields)["status"],"303 See Other")
        changed=copy.deepcopy(r)
        changed["metrics"][0]["numerator"]=0
        self.assertEqual(self.post("operator","/lifecycle-monitor-report",{**fields,"document":json.dumps(changed)})["status"],"403 Forbidden")
        i=incident(p,True)
        self.assertEqual(self.post("operator","/lifecycle-monitor-incident",{"id":ident,"document":json.dumps(i),"confirm":"yes"})["status"],"303 See Other")
        self.assertEqual(self.post("operator","/lifecycle-monitor-report",{**fields,"document":json.dumps(report(p,1))})["status"],"403 Forbidden")
        self.assertTrue(service.detail(self.root,admin,ident)["status"]["version_reassessment_required"])
        pkg=service.export_preview(self.root,admin,ident)
        fields={"id":ident,"approval":pkg["digest"],"confirm":"yes"}
        self.assertEqual(self.post("operator","/lifecycle-monitor-export",fields)["status"],"403 Forbidden")
        out=self.post("admin","/lifecycle-monitor-export",fields)
        self.assertEqual(out["status"],"200 OK")
        self.assertTrue(m.verify(json.loads(out["body"]),pkg["digest"])["expected_digest_matched"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM lifecycle_monitor_events")
        with self.assertRaises(access.AccessDenied):
            service.detail(self.root,admin,ident)


if __name__=="__main__":
    unittest.main()
