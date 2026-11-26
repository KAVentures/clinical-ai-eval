"""Synthetic end-to-end browser and audit tests for longitudinal gap review."""
import copy
import json
import unittest
from caeval import institution_access as access, institution_gap_handoff as native
from caeval import institution_gap_review as review, unified_gap_handoff as h
from tests_unit.test_unified_assessment import UnifiedAssessmentTests


class GapReviewTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    spec=UnifiedAssessmentTests.spec
    freeze=UnifiedAssessmentTests.freeze
    fhir_source=UnifiedAssessmentTests.fhir_source
    lock=UnifiedAssessmentTests.lock

    def setup_review(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        admin=self.actors["admin"]
        preview=native.preview(self.root,admin,ident)
        baseline=native.export(self.root,admin,ident,preview["digest"],confirmed=True)
        fields={"id":ident,"baseline":json.dumps(baseline),"baseline_digest":baseline["digest"]}
        return pid,ident,baseline,fields

    def audit_count(self,event):
        with access.db(self.root) as conn:
            return conn.execute("SELECT count(*) FROM audit WHERE action=? AND outcome='ok'",(event,)).fetchone()[0]

    def test_browser_complete_workflow_and_chained_handoff(self):
        pid,ident,baseline,fields=self.setup_review()
        admin=self.actors["admin"]
        gap=self.request("admin","/unified-assessment-gaps?id="+ident)
        self.assertEqual(gap["status"],"200 OK")
        self.assertIn(b"/unified-gap-review?id=",gap["body"])
        self.assertEqual(self.request("admin","/unified-gap-review?id="+ident)["status"],"200 OK")
        source=self.fhir_source(pid)
        self.lock(ident,"fhir-a",source)
        page=self.post("admin","/unified-gap-review-preview",fields)
        self.assertEqual(page["status"],"200 OK")
        self.assertIn(b"association_added",page["body"])
        self.assertIn(b"Canonical suite inventory",page["body"])
        bundle=review.preview(self.root,admin,ident,baseline,baseline["digest"])
        self.assertEqual(self.audit_count(review.EVENT),0)
        self.assertEqual(self.audit_count(native.EVENT),1)
        out=self.post("admin","/unified-gap-review-export",{**fields,"approval":bundle["digest"],"confirm":"yes"})
        self.assertEqual(out["status"],"200 OK")
        self.assertEqual(json.loads(out["body"]),bundle)
        self.assertEqual(self.audit_count(review.EVENT),1)
        self.assertEqual(self.audit_count(native.EVENT),2)
        verified=review.verify(bundle,baseline_digest=baseline["digest"],
            followup_digest=bundle["followup"]["digest"],expected_digest=bundle["digest"])
        self.assertEqual(verified["status"],"INSTITUTIONAL_GAP_REVIEW_REPLAYED")
        self.assertFalse(verified["local_export_audit_reverified"])
        self.assertFalse(verified["decision_grade"])
        next_baseline=bundle["followup"]
        again=review.preview(self.root,admin,ident,next_baseline,next_baseline["digest"])
        self.assertTrue(again["comparison"]["same_snapshot"])

    def test_access_csrf_and_exact_approval(self):
        _,ident,baseline,fields=self.setup_review()
        admin=self.actors["admin"]
        bundle=review.preview(self.root,admin,ident,baseline,baseline["digest"])
        approved={**fields,"approval":bundle["digest"],"confirm":"yes"}
        for role in ("operator","observer","doctor-a"):
            self.assertEqual(self.request(role,"/unified-gap-review?id="+ident)["status"],"403 Forbidden")
            self.assertEqual(self.post(role,"/unified-gap-review-preview",fields)["status"],"403 Forbidden")
            self.assertEqual(self.post(role,"/unified-gap-review-export",approved)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/unified-gap-review-export","POST",approved)["status"],"403 Forbidden")
        for bad in ({**approved,"approval":"0"*64},{**approved,"confirm":"no"},
                    {**approved,"baseline_digest":"0"*64},{**approved,"extra":"x"}):
            self.assertEqual(self.post("admin","/unified-gap-review-export",bad)["status"],"403 Forbidden")
        self.assertEqual(self.audit_count(review.EVENT),0)
        self.assertEqual(self.audit_count(native.EVENT),1)

    def test_unexported_baseline_stale_preview_and_wrong_programme(self):
        pid,ident,baseline,fields=self.setup_review()
        admin=self.actors["admin"]
        preview=review.preview(self.root,admin,ident,baseline,baseline["digest"])
        source=self.fhir_source(pid)
        self.lock(ident,"fhir-a",source)
        self.assertEqual(self.post("admin","/unified-gap-review-export",
            {**fields,"approval":preview["digest"],"confirm":"yes"})["status"],"403 Forbidden")
        unexported=native.preview(self.root,admin,ident)
        with self.assertRaises(access.AccessDenied):
            review.preview(self.root,admin,ident,unexported,unexported["digest"])
        other=self.freeze({**self.spec(pid),"programme_id":"another-programme"})
        with self.assertRaises(access.AccessDenied):
            review.preview(self.root,admin,other,baseline,baseline["digest"])
        self.assertEqual(self.audit_count(review.EVENT),0)
        self.assertEqual(self.audit_count(native.EVENT),1)

    def test_bundle_tamper_and_strict_parser(self):
        _,ident,baseline,_=self.setup_review()
        bundle=review.preview(self.root,self.actors["admin"],ident,baseline,baseline["digest"])
        kw={"baseline_digest":baseline["digest"],"followup_digest":bundle["followup"]["digest"]}
        for field,value in (("deployment_authorized",True),("decision_grade",0),("chronology_verified",True)):
            bad=copy.deepcopy(bundle)
            bad[field]=value
            bad=h.seal({k:v for k,v in bad.items() if k!="digest"})
            with self.assertRaises(ValueError):
                review.verify(bad,expected_digest=bad["digest"],**kw)
        with self.assertRaises(ValueError):
            review.verify(bundle,expected_digest="0"*64,**kw)
        for raw in ('{"x":1,"x":2}','{"x":NaN}',' '*(review.MAX_BASELINE_BYTES+1)):
            with self.assertRaises(access.AccessDenied):review.parse_baseline(raw)


if __name__=="__main__":unittest.main()
