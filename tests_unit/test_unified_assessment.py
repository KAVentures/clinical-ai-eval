"""Synthetic-only cross-modality procurement orchestration acceptance tests."""
import copy
import json
import unittest
from pathlib import Path
from caeval import (unified_assessment as u, institution_access as access,
                    institution_committee as committee, institution_fhir as fhir_service,
                    fhir_readiness as fhir, institution_imaging as imaging_service,
                    diagnostic_imaging as imaging, buyer_agent as buyer, buyer_plan, intake,
                    institution_retention as retention)
from tests_unit import test_institution_research as browser
from tests_unit.test_fhir_readiness import exact
from tests_unit.test_diagnostic_imaging import spec as imaging_spec


class UnifiedAssessmentTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def plan(self,vid="a",version="v1",profiles=None):
        if vid not in {v["vendor_id"] for v in buyer.load(self.root)["vendors"]}:
            buyer.add_vendor(self.root,vid,"Synthetic "+vid)
        return buyer_plan.preview(self.root,{"vendor_id":vid,"target_name":"Synthetic "+vid,
            "target_version":version,"profiles":profiles or ["clinician_decision_support"],
            "answers":{q:"Fictional "+q for q in intake.INTAKE_QUESTIONS}})["plan_id"]

    def spec(self,pid):
        return {"programme_id":"synthetic-programme","plans":[pid],"requirements":[
            {"id":"fhir-a","kind":"fhir","plan_id":pid,"family":None},
            {"id":"imaging-a","kind":"imaging","plan_id":pid,"family":None}]}

    def freeze(self,s):
        admin=self.actors["admin"]
        p=u.preview(self.root,admin,s)
        return u.freeze(self.root,admin,s,p["digest"],confirmed=True)

    def fhir_source(self,pid):
        actor=self.actors["operator"]
        plan=fhir_service.prepare(self.root,actor,pid)["plan"]
        return fhir_service.submit(self.root,actor,pid,exact(plan),confirmed=True)

    def imaging_source(self):
        admin=self.actors["admin"]
        s=imaging_spec()
        p=imaging_service.preview(self.root,admin,s)
        ident=imaging_service.freeze(self.root,admin,s,p["approval_digest"],confirmed=True)
        template=imaging_service.download(self.root,self.actors["operator"],ident,"a","template")
        template["predictions"][0].update(status="scored",score_bp=7000)
        imaging_service.submit(self.root,self.actors["operator"],ident,template,confirmed=True)
        p=imaging_service.seal_preview(self.root,admin,ident)
        imaging_service.seal(self.root,admin,ident,p["approval_digest"],confirmed=True,allow_incomplete=True)
        return ident

    def lock(self,ident,rid,source):
        actor=self.actors["admin"]
        p=u.link_preview(self.root,actor,ident,rid,source)
        return u.lock(self.root,actor,ident,rid,source,p["approval_digest"],confirmed=True)

    def test_browser_full_multimodal_coverage_and_export(self):
        pid=self.plan()
        s=self.spec(pid)
        fields={"spec":json.dumps(s)}
        self.assertEqual(self.request("admin","/unified-assessment-preview","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/unified-assessment-preview",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/unified-assessment-preview",fields)["status"],"200 OK")
        p=u.preview(self.root,self.actors["admin"],s)
        fields.update(approval=p["digest"],confirm="yes")
        self.assertEqual(self.post("admin","/unified-assessment-freeze",{**fields,"approval":"bad"})["status"],"403 Forbidden")
        result=self.post("admin","/unified-assessment-freeze",fields)
        self.assertEqual(result["status"],"303 See Other")
        ident=s["programme_id"]
        self.assertEqual(self.request("observer",result["headers"]["Location"])["status"],"200 OK")
        self.assertEqual(self.request("doctor-a",result["headers"]["Location"])["status"],"403 Forbidden")
        status=u.status(self.root,self.actors["observer"],ident)
        self.assertFalse(status["all_planned_sources_available"])
        self.assertTrue(status["programme"]["unplanned_canonical_suites"])
        old=committee.freeze(self.root,self.actors["admin"])["snapshot_id"]
        fhir_id=self.fhir_source(pid)
        link={"id":ident,"requirement":"fhir-a","source":fhir_id}
        self.assertEqual(self.post("admin","/unified-assessment-link-preview",link)["status"],"200 OK")
        lp=u.link_preview(self.root,self.actors["admin"],ident,"fhir-a",fhir_id)
        link.update(approval=lp["approval_digest"],confirm="yes")
        self.assertEqual(self.post("operator","/unified-assessment-lock",link)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/unified-assessment-lock",link)["status"],"303 See Other")
        self.assertEqual(self.post("admin","/unified-assessment-lock",link)["status"],"403 Forbidden")
        self.lock(ident,"imaging-a",self.imaging_source())
        status=u.status(self.root,self.actors["observer"],ident)
        self.assertTrue(status["all_planned_sources_available"])
        self.assertFalse(status["canonical_requirements_satisfied"])
        self.assertFalse(status["deployment_authorized"])
        self.assertTrue(status["programme"]["unplanned_canonical_suites"])
        imaging_row=next(r for r in status["requirements"] if r["requirement"]["kind"]=="imaging")
        self.assertFalse(imaging_row["locked_source"]["complete_roster"])
        self.assertEqual(imaging_row["locked_source"]["scope"],"vendor_id_and_version_only")
        self.assertFalse(committee.snapshot(self.root,self.actors["observer"],old)["current"])
        cov=committee.overview(self.root,self.actors["observer"])["lifecycle_inventory"]["unified_assessment_programmes"][0]
        self.assertEqual(cov,status)
        self.assertNotIn("private_nonce",json.dumps(cov))
        preview=u.export_preview(self.root,self.actors["admin"],ident)
        ex={"id":ident,"approval":preview["digest"],"confirm":"yes"}
        self.assertEqual(self.post("operator","/unified-assessment-export",ex)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/unified-assessment-export",{**ex,"approval":"bad"})["status"],"403 Forbidden")
        out=self.post("admin","/unified-assessment-export",ex)
        self.assertEqual(out["status"],"200 OK")
        package=json.loads(out["body"])
        receipt=u.verify_export(package,preview["digest"])
        self.assertFalse(receipt["source_records_reverified"])
        self.assertTrue(receipt["expected_digest_matched"])

    def test_version_scope_and_requirement_validation(self):
        p1=self.plan()
        p2=self.plan(version="v2")
        other=self.plan(vid="b")
        s=self.spec(p1)
        bad=copy.deepcopy(s)
        bad["plans"].append(p2)
        bad["requirements"].append({"id":"fhir-v2","kind":"fhir","plan_id":p2,"family":None})
        with self.assertRaises(access.AccessDenied):
            u.preview(self.root,self.actors["admin"],bad)
        bad=copy.deepcopy(s)
        bad["requirements"][0]["family"]="missing_information"
        with self.assertRaises(ValueError):
            u.preview(self.root,self.actors["admin"],bad)
        bad=copy.deepcopy(s)
        bad["requirements"].append({**bad["requirements"][0],"id":"duplicate"})
        with self.assertRaises(ValueError):
            u.preview(self.root,self.actors["admin"],bad)
        ident=self.freeze(s)
        for pid in (p2,other):
            source=self.fhir_source(pid)
            with self.assertRaises(access.AccessDenied):
                u.link_preview(self.root,self.actors["admin"],ident,"fhir-a",source)

    def test_retention_protection_and_tampered_source(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        source=self.fhir_source(pid)
        self.lock(ident,"fhir-a",source)
        with access.db(self.root) as conn:
            committee._schema(conn)
            self.assertIn(source,retention._protected(conn))
        with access.db(self.root) as conn:
            row=conn.execute("SELECT payload FROM synthetic_fhir_reports WHERE id=?",(source,)).fetchone()
            doc=json.loads(row["payload"])
            doc["package"]["report"]["summary"]["exact_match"]=0
            conn.execute("UPDATE synthetic_fhir_reports SET payload=? WHERE id=?",(json.dumps(doc),source))
        rows=u.status(self.root,self.actors["observer"],ident)["requirements"]
        self.assertEqual(next(r for r in rows if r["requirement"]["id"]=="fhir-a")["state"],"source_unavailable")

    def test_audit_deletion_and_export_tampering(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        source=self.fhir_source(pid)
        self.lock(ident,"fhir-a",source)
        ex=u.export_preview(self.root,self.actors["admin"],ident)
        bad=copy.deepcopy(ex)
        bad["coverage"]["all_planned_sources_available"]=True
        bad["digest"]=imaging._digest({k:v for k,v in bad.items() if k!="digest"})
        with self.assertRaises(ValueError):
            u.verify_export(bad)
        with self.assertRaises(ValueError):
            u.verify_export(ex,"0"*64)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM unified_assessment_links")
        with self.assertRaises(access.AccessDenied):
            u.status(self.root,self.actors["admin"],ident)

    def test_missing_imaging_candidate_version_refused(self):
        pid=self.plan(version="v2")
        ident=self.freeze(self.spec(pid))
        source=self.imaging_source()
        with self.assertRaises(access.AccessDenied):
            u.link_preview(self.root,self.actors["admin"],ident,"imaging-a",source)

    def test_explicit_canonical_suite_selection_and_gap(self):
        pid=self.plan()
        bp=buyer_plan.load(self.root,pid)
        family=next(f for f in bp["plan"]["selection"]["runnable_suites"] if f in ("missing_information","conflicting_evidence"))
        s={"programme_id":"canonical-inventory","plans":[pid],"requirements":[
            {"id":"text-a","kind":"text","plan_id":pid,"family":family}]}
        p=u.preview(self.root,self.actors["admin"],s)
        self.assertNotIn(family,[r["suite"] for r in p["unplanned_canonical_suites"]])
        ident=self.freeze(s)
        self.assertEqual(u.status(self.root,self.actors["operator"],ident)["requirements"][0]["state"],"unlinked")
        with self.assertRaises((ValueError,RuntimeError)):
            u.link_preview(self.root,self.actors["admin"],ident,"text-a","nonexistent-job")


    def test_guided_plan_selection_preserves_unavailable_suites(self):
        pid=self.plan()
        spec=u.guided_spec(self.root,self.actors["admin"],"guided-study",{pid:["imaging","fhir"]})
        p=u.preview(self.root,self.actors["admin"],spec)
        self.assertTrue(any(r["kind"]=="text" for r in p["requirements"]))
        self.assertTrue(any(r["kind"]=="imaging" and r["family"] is None for r in p["requirements"]))
        self.assertTrue(any(r["kind"]=="fhir" and r["family"] is None for r in p["requirements"]))
        form={"programme_id":"guided-study","plan_"+pid:"yes","kind_"+pid+"_imaging":"yes"}
        self.assertEqual(self.post("admin","/unified-assessment-guided-preview",form)["status"],"200 OK")
        self.assertEqual(self.post("operator","/unified-assessment-guided-preview",form)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/unified-assessment-guided-preview",{"programme_id":"guided-study","kind_"+pid+"_imaging":"yes"})["status"],"403 Forbidden")


class CanonicalUnifiedTests(unittest.TestCase):
    def test_real_synthetic_worker_and_locked_package_integrity(self):
        from tests_unit.test_buyer_assessment import AssessmentHandoffTests
        from caeval import buyer_assessment, buyer_jobs
        AssessmentHandoffTests.setUp(self)
        admin=access.provision(self.root,"admin","admin","Synthetic test password!")
        observer=access.provision(self.root,"observer","observer","Synthetic test password!")
        prepared=buyer_assessment.prepare(self.root,self.sub["submission_id"],"mock",self.gov)
        pid=prepared["plan_id"]
        bp=buyer_plan.load(self.root,pid)
        families=bp["plan"]["selection"]["runnable_suites"]
        spec={"programme_id":"worker-evidence","plans":[pid],"requirements":[
            {"id":"text-"+f,"kind":"text","plan_id":pid,"family":f} for f in families]}
        p=u.preview(self.root,admin,spec)
        ident=u.freeze(self.root,admin,spec,p["digest"],confirmed=True)
        job=buyer_jobs.enqueue(self.root,prepared["assessment_id"])
        with self.assertRaises(access.AccessDenied):
            u.link_preview(self.root,admin,ident,"text-"+families[0],job["id"])
        finished=buyer_jobs.execute(self.root,job["id"])
        self.assertEqual(finished["status"],"completed",finished)
        for family in families:
            rid="text-"+family
            link=u.link_preview(self.root,admin,ident,rid,job["id"])
            u.lock(self.root,admin,ident,rid,job["id"],link["approval_digest"],confirmed=True)
        doc=u.status(self.root,observer,ident)
        self.assertTrue(doc["all_planned_sources_available"])
        self.assertFalse(doc["canonical_requirements_satisfied"])
        self.assertTrue(all(r["locked_source"]["mode"]=="demonstration" for r in doc["requirements"]))
        ws=self.root/"evaluation-jobs"/job["id"]/"out"/("run_"+families[0])
        (ws/"final_report.md").write_text("modified")
        self.assertFalse(u.status(self.root,observer,ident)["all_planned_sources_available"])


if __name__=="__main__":
    unittest.main()
