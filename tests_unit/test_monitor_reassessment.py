"""Synthetic operational version-change handoff and retry regression tests."""
import copy
import json
import unittest
from caeval import (institution_access as access, institution_lifecycle_monitor as monitor,
                    monitor_reassessment as handoff, version_reassessment as version,
                    unified_successor as successor, unified_assessment as u,
                    monitor_ingestion as ingestion, institution_lifecycle_preflight as preflight)
from tests_unit.test_unified_assessment import UnifiedAssessmentTests
from tests_unit.test_lifecycle_monitor import spec, report, incident, action


class MonitorReassessmentTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    freeze=UnifiedAssessmentTests.freeze

    def prepare(self):
        self.admin=self.actors["admin"]
        self.operator=self.actors["operator"]
        self.pid=self.plan()
        self.baseline=self.freeze({"programme_id":"programme","plans":[self.pid],"requirements":[
            {"id":"fhir-a","kind":"fhir","plan_id":self.pid,"family":None}]})
        s=spec(self.baseline)
        self.p=monitor.preview(self.root,self.admin,s)
        self.ident=monitor.freeze(self.root,self.admin,s,self.p["digest"],confirmed=True)

    def native_successor(self,target_version,ident):
        p=version.preview(self.root,self.admin,self.pid,target_version)
        native=version.freeze(self.root,self.admin,self.pid,target_version,p["digest"],confirmed=True)["successor_id"]
        p=successor.preview(self.root,self.admin,self.baseline,ident,{self.pid:native})
        successor.freeze(self.root,self.admin,self.baseline,ident,{self.pid:native},
                         p["lineage"]["digest"],confirmed=True)
        return ident

    def test_exact_successor_and_browser_authorization(self):
        self.prepare()
        monitor.submit_incident(self.root,self.operator,self.ident,incident(self.p,True),confirmed=True)
        iid="fictional-incident"
        self.assertEqual(handoff.view(self.root,self.admin,self.ident)["incidents"][0]["eligible_successors"],[])
        wrong=self.native_successor("v3","version-three")
        good=self.native_successor("v2","version-two")
        item=handoff.view(self.root,self.admin,self.ident)["incidents"][0]
        self.assertEqual(item["eligible_successors"],[good])
        with self.assertRaises(access.AccessDenied):
            handoff.preview(self.root,self.admin,self.ident,iid,wrong)
        url="/monitor-reassessment?id="+self.ident
        self.assertEqual(self.request("admin",url)["status"],"200 OK")
        self.assertEqual(self.request("operator",url)["status"],"200 OK")
        self.assertEqual(self.request("observer",url)["status"],"403 Forbidden")
        self.assertEqual(self.request("doctor-a",url)["status"],"403 Forbidden")
        fields={"id":self.ident,"incident":iid,"successor":good}
        self.assertEqual(self.request("admin","/monitor-reassessment-preview","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/monitor-reassessment-preview",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/monitor-reassessment-preview",fields)["status"],"200 OK")
        p=handoff.preview(self.root,self.admin,self.ident,iid,good)
        fields.update(approval=p["digest"],confirm="yes")
        self.assertEqual(self.post("admin","/monitor-reassessment-lock",{**fields,"approval":"bad"})["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/monitor-reassessment-lock",fields)["status"],"303 See Other")
        self.assertEqual(self.post("admin","/monitor-reassessment-lock",fields)["status"],"403 Forbidden")
        item=handoff.view(self.root,self.operator,self.ident)["incidents"][0]
        self.assertEqual(item["handoff"],p)
        self.assertEqual(item["successor_state"],"current")
        self.assertFalse(p["alert_resolved"])
        self.assertFalse(p["deployment_authorized"])
        self.assertTrue(monitor.detail(self.root,self.admin,self.ident)["status"]["version_reassessment_required"])
        self.assertTrue(all(r["state"]=="unlinked" for r in u.status(self.root,self.admin,good)["requirements"]))
        rows=handoff.overview(self.root,self.actors["observer"])
        self.assertEqual(rows[0]["n_linked"],1)
        self.assertNotIn(iid,json.dumps(rows))
        self.assertNotIn("incident_digest",json.dumps(rows))
        from caeval import institution_lifecycle_cockpit as cockpit
        doc=cockpit.overview(self.root,self.admin)
        row=next(r for r in doc["vendors"] if r["programme_id"]==self.baseline)
        self.assertEqual(row["handoff_state"],"available")
        self.assertEqual(row["handoff"]["n_linked"],1)
        self.assertEqual(row["handoff"]["successor_programmes"],[good])
        self.assertTrue(row["monitor"]["version_reassessment_required"])
        self.assertEqual(self.request("admin","/lifecycle-cockpit?programme="+self.baseline)["status"],"200 OK")
        self.assertTrue(preflight.check(self.root,services_stopped=True)["ok"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM monitor_version_handoffs")
        with self.assertRaises(access.AccessDenied):
            handoff.view(self.root,self.admin,self.ident)

    def test_nonversion_incidents_not_linkable(self):
        self.prepare()
        monitor.submit_incident(self.root,self.operator,self.ident,incident(self.p),confirmed=True)
        self.assertEqual(handoff.view(self.root,self.admin,self.ident)["incidents"],[])
        good=self.native_successor("v2","version-two")
        with self.assertRaises(access.AccessDenied):
            handoff.preview(self.root,self.admin,self.ident,"fictional-incident",good)

    def test_exact_report_retry_after_version_change_only(self):
        self.prepare()
        r=report(self.p)
        initial=monitor.submit_report(self.root,self.operator,self.ident,r,confirmed=True)
        monitor.submit_incident(self.root,self.operator,self.ident,incident(self.p,True),confirmed=True)
        self.assertEqual(initial,monitor.submit_report(self.root,self.operator,self.ident,r,confirmed=True))
        wrong=copy.deepcopy(r)
        wrong["metrics"][0]["numerator"]=0
        with self.assertRaises(access.AccessDenied):
            monitor.submit_report(self.root,self.operator,self.ident,wrong,confirmed=True)
        with self.assertRaises(access.AccessDenied):
            monitor.submit_report(self.root,self.operator,self.ident,report(self.p,1),confirmed=True)
        grant=ingestion.issue(self.root,self.admin,{"monitor_id":self.ident,"owner":self.operator["user_id"],
            "scopes":["report"],"ttl_seconds":600,"max_uses":2},confirmed=True)
        batch={"schema":"lifecycle-ingestion/1","request_id":"retry","confirmed":True,
               "events":[{"kind":"report","document":r}]}
        receipt=ingestion.submit(self.root,grant["bearer_token"],batch)
        self.assertEqual(receipt["event_digests"],[initial])
        self.assertEqual(len(monitor.detail(self.root,self.admin,self.ident)["records"]["report"]),1)

    def test_historical_missing_window_action_retry(self):
        self.prepare()
        a=action(self.p,"missing_window:0","acknowledge")
        first=monitor.record_action(self.root,self.admin,self.ident,a,confirmed=True)
        monitor.submit_report(self.root,self.operator,self.ident,report(self.p),confirmed=True)
        self.assertEqual(first,monitor.record_action(self.root,self.admin,self.ident,a,confirmed=True))
        new=action(self.p,"missing_window:0","investigate")
        with self.assertRaises(ValueError):
            monitor.record_action(self.root,self.admin,self.ident,new,confirmed=True)
        self.assertEqual(len(monitor.detail(self.root,self.admin,self.ident)["records"]["action"]),1)


if __name__=="__main__":
    unittest.main()
