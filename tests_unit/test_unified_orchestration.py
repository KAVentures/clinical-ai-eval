"""Synthetic institutional orchestration and programme lineage regressions."""
import json
import unittest
from caeval import (unified_assessment as u, unified_discovery as discovery,
                    unified_work as work, unified_successor as successor,
                    version_reassessment as version, institution_access as access,
                    institution_committee as committee, buyer_jobs, buyer_assessment)
from tests_unit import test_unified_assessment as fixtures


class OrchestrationTests(unittest.TestCase):
    setUp=fixtures.UnifiedAssessmentTests.setUp
    request=fixtures.UnifiedAssessmentTests.request
    post=fixtures.UnifiedAssessmentTests.post
    plan=fixtures.UnifiedAssessmentTests.plan
    spec=fixtures.UnifiedAssessmentTests.spec
    freeze=fixtures.UnifiedAssessmentTests.freeze
    fhir_source=fixtures.UnifiedAssessmentTests.fhir_source
    imaging_source=fixtures.UnifiedAssessmentTests.imaging_source
    lock=fixtures.UnifiedAssessmentTests.lock

    def test_discovery_excludes_wrong_version_and_locked_requirements(self):
        pid=self.plan()
        other=self.plan(version="v2")
        ident=self.freeze(self.spec(pid))
        good=self.fhir_source(pid)
        wrong=self.fhir_source(other)
        admin=self.actors["admin"]
        doc=discovery.discover(self.root,admin,ident,"fhir-a")
        self.assertEqual([p["binding"]["source"]["source_id"] for p in doc["matches"]],[good])
        self.assertNotIn(wrong,json.dumps(doc))
        self.assertFalse(doc["selection_authorized"])
        url="/unified-assessment-discover?id="+ident+"&requirement=fhir-a"
        self.assertEqual(self.request("admin",url)["status"],"200 OK")
        self.assertEqual(self.request("operator",url)["status"],"403 Forbidden")
        self.assertEqual(self.request("observer",url)["status"],"403 Forbidden")
        self.lock(ident,"fhir-a",good)
        with self.assertRaises(access.AccessDenied):
            discovery.discover(self.root,admin,ident,"fhir-a")

    def test_batch_preview_and_atomic_stale_rejection(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        admin=self.actors["admin"]
        source=self.fhir_source(pid)
        image=self.imaging_source()
        selections=[{"requirement_id":"fhir-a","source_id":source},
                    {"requirement_id":"imaging-a","source_id":image}]
        p=discovery.batch_preview(self.root,admin,ident,selections)
        self.assertEqual(p,discovery.batch_preview(self.root,admin,ident,list(reversed(selections))))
        fields={"id":ident,"selections":json.dumps(selections)}
        self.assertEqual(self.post("admin","/unified-assessment-batch-preview",fields)["status"],"200 OK")
        self.assertEqual(self.request("admin","/unified-assessment-batch-preview","POST",fields)["status"],"403 Forbidden")
        fields.update(approval=p["digest"],confirm="yes")
        self.assertEqual(self.post("operator","/unified-assessment-batch-lock",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/unified-assessment-batch-lock",{**fields,"approval":"bad"})["status"],"403 Forbidden")
        with access.db(self.root) as conn:
            row=conn.execute("SELECT payload FROM synthetic_fhir_reports WHERE id=?",(source,)).fetchone()
            original=row["payload"]
            conn.execute("UPDATE synthetic_fhir_reports SET payload='{}' WHERE id=?",(source,))
        self.assertEqual(self.post("admin","/unified-assessment-batch-lock",fields)["status"],"403 Forbidden")
        with access.db(self.root) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM unified_assessment_links").fetchone()[0],0)
            conn.execute("UPDATE synthetic_fhir_reports SET payload=? WHERE id=?",(original,source))
        self.assertEqual(self.post("admin","/unified-assessment-batch-lock",fields)["status"],"303 See Other")
        self.assertTrue(u.status(self.root,admin,ident)["all_planned_sources_available"])
        self.assertEqual(self.post("admin","/unified-assessment-batch-lock",fields)["status"],"403 Forbidden")

    def test_batch_duplicate_and_explicit_approval_required(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        source=self.fhir_source(pid)
        admin=self.actors["admin"]
        selection={"requirement_id":"fhir-a","source_id":source}
        with self.assertRaises(ValueError):
            discovery.batch_preview(self.root,admin,ident,[selection,selection])
        p=discovery.batch_preview(self.root,admin,ident,[selection])
        with self.assertRaises(access.AccessDenied):
            discovery.batch_lock(self.root,admin,ident,[selection],p["digest"])

    def native_successor(self,pid):
        admin=self.actors["admin"]
        p=version.preview(self.root,admin,pid,"v2")
        return version.freeze(self.root,admin,pid,"v2",p["digest"],confirmed=True)["successor_id"]

    def test_version_successor_never_copies_evidence(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        old_source=self.fhir_source(pid)
        self.lock(ident,"fhir-a",old_source)
        admin=self.actors["admin"]
        observer=self.actors["observer"]
        native=self.native_successor(pid)
        opts=successor.choices(self.root,admin,ident)
        self.assertEqual({o["successor_plan_id"] for o in opts[0]["options"]},{pid,native})
        mapping={pid:native}
        p=successor.preview(self.root,admin,ident,"version-two",mapping)
        form={"baseline":ident,"successor":"version-two","plan_"+pid:native}
        self.assertEqual(self.post("admin","/unified-assessment-successor-preview",form)["status"],"200 OK")
        self.assertEqual(self.post("operator","/unified-assessment-successor-preview",form)["status"],"403 Forbidden")
        snapshot=committee.freeze(self.root,admin)["snapshot_id"]
        fields={"baseline":ident,"successor":"version-two","mapping":json.dumps(mapping),
                "approval":p["lineage"]["digest"],"confirm":"yes"}
        self.assertEqual(self.post("admin","/unified-assessment-successor-freeze",fields)["status"],"303 See Other")
        state=u.status(self.root,observer,"version-two")
        self.assertTrue(all(r["state"]=="unlinked" for r in state["requirements"]))
        self.assertTrue(all(r["target"]["version"]=="v2" for r in state["requirements"]))
        with self.assertRaises(access.AccessDenied):
            u.link_preview(self.root,admin,"version-two","fhir-a",old_source)
        self.assertFalse(committee.snapshot(self.root,observer,snapshot)["current"])
        cmp=successor.compare(self.root,observer,"version-two")
        self.assertFalse(cmp["performance_comparison_authorized"])
        row=next(r for r in cmp["requirements"] if r["requirement_id"]=="fhir-a")
        self.assertEqual((row["baseline_state"],row["successor_state"]),("source_available","unlinked"))
        self.assertEqual(self.request("observer","/unified-assessment-compare?id=version-two")["status"],"200 OK")
        self.assertEqual(self.request("doctor-a","/unified-assessment-compare?id=version-two")["status"],"403 Forbidden")

    def test_native_reassessment_required_and_tampering_fails_closed(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        admin=self.actors["admin"]
        # A separately created plan is not an approved native reassessment.
        other=self.plan(version="v3")
        with self.assertRaises(access.AccessDenied):
            successor.preview(self.root,admin,ident,"bad-mapping",{pid:other})
        with self.assertRaises(ValueError):
            successor.preview(self.root,admin,ident,"unchanged",{pid:pid})
        native=self.native_successor(pid)
        p=successor.preview(self.root,admin,ident,"version-two",{pid:native})
        successor.freeze(self.root,admin,ident,"version-two",{pid:native},p["lineage"]["digest"],confirmed=True)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM version_reassessments")
        with self.assertRaises(access.AccessDenied):
            successor.inventory(self.root,admin)

    def test_work_roles_and_no_implicit_execution(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        self.assertEqual(self.request("operator","/unified-assessment-work?id="+ident)["status"],"200 OK")
        self.assertEqual(self.request("observer","/unified-assessment-work?id="+ident)["status"],"403 Forbidden")
        result=work.work(self.root,self.actors["operator"],ident)
        self.assertFalse(result["execution_authorized"])
        self.assertTrue(all(not r["native_work_records"] for r in result["requirements"]))


class NativeWorkTests(unittest.TestCase):
    def test_prepared_and_queued_text_work_stays_unlinked(self):
        from tests_unit.test_buyer_assessment import AssessmentHandoffTests
        from caeval import buyer_plan
        AssessmentHandoffTests.setUp(self)
        admin=access.provision(self.root,"admin","admin","Synthetic test password!")
        prepared=buyer_assessment.prepare(self.root,self.sub["submission_id"],"mock",self.gov)
        pid=prepared["plan_id"]
        families=buyer_plan.load(self.root,pid)["plan"]["selection"]["runnable_suites"]
        spec={"programme_id":"work-programme","plans":[pid],"requirements":[
            {"id":"text-"+f,"kind":"text","plan_id":pid,"family":f} for f in families]}
        p=u.preview(self.root,admin,spec)
        ident=u.freeze(self.root,admin,spec,p["digest"],confirmed=True)
        r=work.work(self.root,admin,ident)["requirements"][0]
        self.assertEqual(r["native_work_records"][0]["state"],"prepared_not_executed")
        job=buyer_jobs.enqueue(self.root,prepared["assessment_id"])
        r=work.work(self.root,admin,ident)["requirements"][0]
        self.assertEqual(r["native_work_records"][0]["id"],job["id"])
        self.assertEqual(r["native_work_records"][0]["state"],"queued")
        self.assertEqual(r["coverage_state"],"unlinked")
        self.assertFalse(r["execution_authorized"])
        buyer_jobs.cancel(self.root,job["id"])
        r=work.work(self.root,admin,ident)["requirements"][0]
        self.assertEqual(r["native_work_records"][0]["state"],"cancelled")


    def test_corrupt_prepared_text_record_is_visible_as_partial_work(self):
        fixtures.UnifiedAssessmentTests.setUp(self)
        pid=fixtures.UnifiedAssessmentTests.plan(self)
        spec={"programme_id":"text-inventory","plans":[pid],"requirements":[
            {"id":"text-a","kind":"text","plan_id":pid,"family":"missing_information"}]}
        ident=fixtures.UnifiedAssessmentTests.freeze(self,spec)
        actor=self.actors["operator"]
        before=work.work(self.root,actor,ident)["requirements"][0]
        self.assertEqual(before["work_inventory_state"],"bounded_scan")
        self.assertEqual(before["native_work_records"],[])
        base=self.root/"prepared-assessments"
        base.mkdir(exist_ok=True)
        (base/("a"*64)).mkdir()
        after=work.work(self.root,actor,ident)["requirements"][0]
        self.assertEqual(after["work_inventory_state"],"partial")
        self.assertEqual(after["unverified_records"],1)
        self.assertFalse(after["scan_truncated"])
        self.assertEqual(after["native_work_records"],[])
        page=fixtures.UnifiedAssessmentTests.request(self,"operator","/unified-assessment-work?id="+ident)
        self.assertEqual(page["status"],"200 OK")
        self.assertIn(b"Partial inventory",page["body"])
        report=work.gaps(self.root,actor,ident)
        self.assertTrue(report["partial_work_inventory"])
        self.assertFalse(report["decision_grade"])


if __name__=="__main__":
    unittest.main()
