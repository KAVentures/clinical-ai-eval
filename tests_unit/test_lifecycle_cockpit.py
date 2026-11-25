"""Synthetic lifecycle cockpit projection and guided setup acceptance tests."""
import copy
import json
import unittest
from unittest.mock import patch
from caeval import (institution_lifecycle_cockpit as cockpit,
                    institution_hospital_referral as referral,
                    institution_lifecycle_monitor as monitor,
                    hospital_referral as h,lifecycle_monitor as m,
                    unified_assessment as u,institution_access as access)
from tests_unit.test_unified_assessment import UnifiedAssessmentTests
from tests_unit.test_hospital_referral import spec as policy_spec
from tests_unit.test_lifecycle_monitor import spec as monitor_spec,incident


def sample():
    target={"name":"Fictional product","version":"v1"}
    req={"id":"fhir-a","kind":"fhir","family":None,"plan_id":"a"*64}
    status={"programme":{"programme_id":"programme","plans":[
            {"plan_id":"a"*64,"vendor_id":"a","target":target}],"requirements":[req],
            "unplanned_canonical_suites":[{"plan_id":"a"*64,"suite":"missing_information",
                                          "state":"not_planned_in_this_programme"}]},
            "state":"current","requirements":[{"requirement":req,"vendor_id":"a","target":target,
            "state":"unlinked","locked_source":None}]}
    return {name:{"state":"available","records":([status] if name=="programmes" else [])}
            for name in cockpit.SECTIONS}


class ProjectionTests(unittest.TestCase):
    def test_partial_inventory_never_becomes_empty_success(self):
        records=sample()
        records["policies"]={"state":"unavailable","records":[]}
        doc=cockpit.project(records)
        self.assertTrue(doc["partial"])
        self.assertFalse(doc["decision_grade"])
        self.assertFalse(doc["deployment_authorized"])
        row=doc["vendors"][0]
        tasks={r["code"] for r in row["tasks"]}
        self.assertIn("policy_inventory_unavailable",tasks)
        self.assertNotIn("consider_local_referral_policy",tasks)
        self.assertIn("canonical_suite_gaps",tasks)

    def test_scope_is_exact_and_unmatched_is_explicit(self):
        records=sample()
        status=records["programmes"]["records"][0]
        target=status["programme"]["plans"][0]["target"]
        records["policies"]["records"]=[{"programme_id":"other","vendor_id":"a","target":target}]
        doc=cockpit.project(records)
        self.assertTrue(doc["partial"])
        self.assertEqual(doc["unmatched_record_counts"]["policies"],1)
        self.assertIsNone(doc["vendors"][0]["policy"])
        records["policies"]["records"][0]["programme_id"]="programme"
        records["policies"]["records"][0]["target"]={"name":"Fictional product","version":"v2"}
        with self.assertRaises(ValueError):
            cockpit.project(records)

    def test_source_association_closes_policy_registration_route(self):
        records=sample()
        row=records["programmes"]["records"][0]["requirements"][0]
        row.update(state="source_available",locked_source={"mode":"demonstration","claim":"demonstration"})
        out=cockpit.project(records)["vendors"][0]
        self.assertNotIn("consider_local_referral_policy",{t["code"] for t in out["tasks"]})
        self.assertEqual(out["source_counts"]["source_available"],1)
        self.assertEqual(out["requirements"][0]["claim"],"demonstration")

    def test_stable_critical_first_and_version_lineage(self):
        records=sample()
        target=records["programmes"]["records"][0]["programme"]["plans"][0]["target"]
        records["monitors"]["records"]=[{"programme_id":"programme","vendor_id":"a","target":target,
            "id":"monitor","version_reassessment_required":True,"attention_required":True,
            "n_missing_windows":2,"schedule_ended":False}]
        records["successors"]["records"]=[{"baseline_id":"programme","successor_id":"successor","digest":"b"*64}]
        doc=cockpit.project(records)
        row=doc["vendors"][0]
        self.assertEqual(row["tasks"][0]["code"],"reported_version_change")
        self.assertEqual(row["programme_lineage"][0]["successor_id"],"successor")
        self.assertEqual(doc,cockpit.project(copy.deepcopy(records)))
        self.assertFalse(row["deployment_authorized"])


class BrowserCockpitTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    freeze=UnifiedAssessmentTests.freeze
    fhir_source=UnifiedAssessmentTests.fhir_source
    lock=UnifiedAssessmentTests.lock

    def prepare(self):
        pid=self.plan()
        ident=self.freeze({"programme_id":"programme","plans":[pid],"requirements":[
            {"id":"fhir-a","kind":"fhir","plan_id":pid,"family":None}]})
        return pid,ident

    def test_role_filters_and_partial_native_failure(self):
        _,ident=self.prepare()
        url="/lifecycle-cockpit?programme="+ident+"&vendor=a&attention=all"
        for role in ("admin","operator","observer"):
            self.assertEqual(self.request(role,url)["status"],"200 OK")
        self.assertEqual(self.request("doctor-a",url)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/lifecycle-cockpit?programme=&vendor=&attention=all")["status"],"200 OK")
        self.assertEqual(self.request("admin","/lifecycle-cockpit?vendor=unknown")["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/lifecycle-cockpit?unexpected=yes")["status"],"403 Forbidden")
        with patch.object(referral,"overview",side_effect=access.AccessDenied("synthetic unavailable")):
            doc=cockpit.overview(self.root,self.actors["admin"])
            self.assertTrue(doc["partial"])
            self.assertEqual(doc["source_availability"]["policies"],"unavailable")
            self.assertIsNone(doc["vendors"][0]["policy"])
            self.assertEqual(self.request("admin",url)["status"],"200 OK")

    def test_guided_policy_and_monitor_lifecycle(self):
        pid,ident=self.prepare()
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        query="?programme="+ident+"&vendor=a"
        for path in ("/cockpit-policy-setup","/cockpit-monitor-setup"):
            self.assertEqual(self.request("admin",path+query)["status"],"200 OK")
            self.assertEqual(self.request("observer",path+query)["status"],"403 Forbidden")
        s=policy_spec(ident)
        fields={k:s[k] for k in ("policy_id","programme_id","vendor_id","intended_use")}
        for gate in s["gates"]:
            fields["reviewer_"+gate["id"]]=gate["reviewer"]
            fields["criterion_"+gate["id"]]=gate["criterion"]
        self.assertEqual(self.request("admin","/cockpit-policy-preview","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/cockpit-policy-preview",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/cockpit-policy-preview",fields)["status"],"200 OK")
        p=referral.preview(self.root,admin,s)
        referral.freeze(self.root,admin,s,p["digest"],confirmed=True)
        self.assertEqual(self.request("admin","/cockpit-policy-setup"+query)["status"],"403 Forbidden")
        ms=monitor_spec(ident)
        fields={k:str(ms[k]) for k in ("monitor_id","programme_id","vendor_id","intended_use_id","start_date",
                                       "window_days","windows","authorization_digest")}
        fields["authorized"]="yes"
        for i in range(m.MAX_METRICS):
            for k in ("id","direction","threshold_bp","min_denominator","definition_digest"):
                fields["metric_"+str(i)+"_"+k]=str(ms["metrics"][i][k]) if i<len(ms["metrics"]) else ""
        self.assertEqual(self.post("admin","/cockpit-monitor-preview",{**fields,"authorized":"no"})["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/cockpit-monitor-preview",fields)["status"],"200 OK")
        mp=monitor.preview(self.root,admin,ms)
        monitor_id=monitor.freeze(self.root,admin,ms,mp["digest"],confirmed=True)
        self.lock(ident,"fhir-a",self.fhir_source(pid))
        monitor.submit_incident(self.root,operator,monitor_id,incident(mp,True),confirmed=True)
        result=cockpit.overview(self.root,admin)
        row=result["vendors"][0]
        self.assertEqual(row["source_counts"]["source_available"],1)
        self.assertEqual(row["policy"]["id"],s["policy_id"])
        self.assertTrue(row["monitor"]["version_reassessment_required"])
        self.assertEqual(row["tasks"][0]["code"],"reported_version_change")
        self.assertEqual(self.request("admin","/lifecycle-cockpit?attention=required")["status"],"200 OK")
        self.assertNotIn("external_record_digest",json.dumps(result))
        self.assertFalse(result["deployment_authorized"])


if __name__=="__main__":
    unittest.main()
