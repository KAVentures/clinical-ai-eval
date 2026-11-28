"""Hospital referral policy tests; no clinical claims or patient data."""
import copy
import json
import unittest
from caeval import (hospital_referral as h, institution_hospital_referral as service,
                    unified_assessment as u, diagnostic_imaging as d,
                    institution_access as access)
from tests_unit.test_unified_assessment import UnifiedAssessmentTests


def spec(programme_id="programme",vendor="a"):
    return {"policy_id":"hospital-policy","programme_id":programme_id,"vendor_id":vendor,
            "intended_use":"Fictional evaluation only","gates":[
                {"id":key,"reviewer":"doctor-a" if i%2==0 else "doctor-b",
                 "criterion":"Independent fictional evidence for "+key} for i,key in enumerate(h.MANDATORY)]}


def simulated():
    req={"id":"text-one","kind":"text","plan_id":"a"*64,"family":"missing_information"}
    programme=d._sealed({"programme_id":"programme","plans":[{"plan_id":"a"*64,"vendor_id":"a",
                      "target":{"name":"Fictional","version":"v1"}}],"requirements":[req],
                      "unplanned_canonical_suites":[]})
    status={"programme":programme,"state":"current","requirements":[{"requirement":req,"state":"source_available",
        "locked_source":{"mode":"internal_regression","claim":"internal_regression","scope":"exact_buyer_plan"}}]}
    p=h.policy(programme,spec())
    return p,status


def ratings(p,snap):
    return [h.rating(p,snap,g["reviewer"],{"gate_id":g["id"],"snapshot_digest":snap["digest"],
             "verdict":"attested_met","artifact_digest":"a"*64,"rationale":"Synthetic attestation"})
             for g in p["spec"]["gates"]]


class PolicyTests(unittest.TestCase):
    def test_mandatory_domains_and_distinct_reviewers(self):
        p,status=simulated()
        for change in ("missing","same","duplicate"):
            s=spec()
            if change=="missing":
                s["gates"].pop()
            elif change=="same":
                for gate in s["gates"]:
                    gate["reviewer"]="doctor-a"
            else:
                s["gates"].append(copy.deepcopy(s["gates"][0]))
            with self.assertRaises(ValueError):
                h.policy(status["programme"],s)

    def test_positive_referral_is_not_deployment_or_clinical_acceptance(self):
        p,status=simulated()
        snap=h.snapshot(p,status)
        r=ratings(p,snap)
        self.assertTrue(h.evaluate(p,snap,r)["referral_conditions_met"])
        result=h.disposition(p,snap,r,"refer_external_assessment","Fictional external review")
        self.assertFalse(result["deployment_authorized"])
        self.assertFalse(result["clinical_acceptance"])
        self.assertFalse(result["decision_grade"])
        with self.assertRaises(ValueError):
            h.disposition(p,snap,r[:-1],"refer_external_assessment","Incomplete reviews")

    def test_demo_stale_missing_and_gap_never_pass(self):
        p,status=simulated()
        for state,claim in (("source_available","demonstration"),("source_changed","internal_regression"),
                            ("unlinked","internal_regression")):
            changed=copy.deepcopy(status)
            changed["requirements"][0]["state"]=state
            changed["requirements"][0]["locked_source"]["claim"]=claim
            snap=h.snapshot(p,changed)
            self.assertFalse(h.evaluate(p,snap,ratings(p,snap))["referral_conditions_met"])
        changed=copy.deepcopy(status)
        changed["programme"]["unplanned_canonical_suites"]=[{"plan_id":"a"*64,"suite":"other","state":"required_but_not_run"}]
        p2=h.policy(changed["programme"],spec())
        snap=h.snapshot(p2,changed)
        self.assertFalse(h.evaluate(p2,snap,ratings(p2,snap))["referral_conditions_met"])

    def test_artifact_digest_and_assigned_reviewer_required(self):
        p,status=simulated()
        snap=h.snapshot(p,status)
        gate=p["spec"]["gates"][0]
        rating={"gate_id":gate["id"],"snapshot_digest":snap["digest"],"verdict":"attested_met",
                "artifact_digest":None,"rationale":"Synthetic"}
        with self.assertRaises(ValueError):
            h.rating(p,snap,gate["reviewer"],rating)
        rating["artifact_digest"]="a"*64
        with self.assertRaises(ValueError):
            h.rating(p,snap,"doctor-c",rating)
        r=h.rating(p,snap,gate["reviewer"],rating)
        self.assertFalse(r["external_artifact_verified"])


class InstitutionPolicyTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    freeze=UnifiedAssessmentTests.freeze
    fhir_source=UnifiedAssessmentTests.fhir_source
    lock=UnifiedAssessmentTests.lock

    def prepare(self):
        pid=self.plan()
        programme=self.freeze({"programme_id":"programme","plans":[pid],"requirements":[
            {"id":"fhir-a","kind":"fhir","plan_id":pid,"family":None}]})
        return pid,programme,spec(programme)

    def test_browser_review_hold_and_private_replay(self):
        pid,programme,s=self.prepare()
        admin=self.actors["admin"]
        fields={"spec":json.dumps(s)}
        self.assertEqual(self.post("operator","/hospital-referral-preview",fields)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin","/hospital-referral-preview","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("admin","/hospital-referral-preview",fields)["status"],"200 OK")
        p=service.preview(self.root,admin,s)
        fields.update(approval=p["digest"],confirm="yes")
        self.assertEqual(self.post("admin","/hospital-referral-freeze",fields)["status"],"303 See Other")
        ident=s["policy_id"]
        self.assertEqual(self.request("doctor-a","/hospital-referral-assigned?id="+ident)["status"],"403 Forbidden")
        preview=service.snapshot_preview(self.root,admin,ident)
        f={"id":ident,"approval":preview["approval"],"confirm":"yes"}
        self.assertEqual(self.post("admin","/hospital-referral-start",f)["status"],"303 See Other")
        self.assertEqual(self.request("doctor-c","/hospital-referral-assigned?id="+ident)["status"],"403 Forbidden")
        self.assertEqual(self.request("doctor-a","/hospital-referral-assigned?id="+ident)["status"],"200 OK")
        for gate in p["spec"]["gates"]:
            uid=gate["reviewer"]
            doc={"id":ident,"snapshot_digest":preview["payload"]["snapshot"]["digest"],
                 "gate_id":gate["id"],"verdict":"attested_met","artifact_digest":"a"*64,
                 "rationale":"Fictional reviewed artifact","confirm":"yes"}
            self.assertEqual(self.post("doctor-c","/hospital-referral-rate",doc)["status"],"403 Forbidden")
            self.assertEqual(self.post(uid,"/hospital-referral-rate",doc)["status"],"303 See Other")
            self.assertEqual(self.post(uid,"/hospital-referral-rate",doc)["status"],"403 Forbidden")
        with self.assertRaises(ValueError):
            service.decision_preview(self.root,admin,ident,"refer_external_assessment","Not eligible")
        p=service.decision_preview(self.root,admin,ident,"hold","Evidence incomplete")
        f={"id":ident,"kind":"hold","rationale":"Evidence incomplete","approval":p["digest"],"confirm":"yes"}
        self.assertEqual(self.post("admin","/hospital-referral-decide",f)["status"],"303 See Other")
        self.assertEqual(self.request("observer","/hospital-referral-detail?id="+ident)["status"],"403 Forbidden")
        f={"id":ident,"approval":p["digest"],"confirm":"yes"}
        out=self.post("admin","/hospital-referral-export",f)
        self.assertEqual(out["status"],"200 OK")
        package=json.loads(out["body"])
        self.assertTrue(h.verify(package,package["digest"])["expected_digest_matched"])
        self.assertFalse(h.verify(package)["external_artifacts_verified"])
        self.assertEqual(self.post("observer","/hospital-referral-export",f)["status"],"403 Forbidden")
        self.assertNotIn("rationale",json.dumps(service.overview(self.root,self.actors["observer"])))
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM hospital_referral_ratings")
        with self.assertRaises(access.AccessDenied):
            service.detail(self.root,admin,ident)

    def test_policy_frozen_before_links_and_snapshot_currentness(self):
        pid,programme,s=self.prepare()
        admin=self.actors["admin"]
        p=service.preview(self.root,admin,s)
        ident=service.freeze(self.root,admin,s,p["digest"],confirmed=True)
        with self.assertRaises(access.AccessDenied):
            service.preview(self.root,admin,{**s,"policy_id":"alternative"})
        p=service.snapshot_preview(self.root,admin,ident)
        service.start_review(self.root,admin,ident,p["approval"],confirmed=True)
        self.assertTrue(service.detail(self.root,admin,ident)["coverage"]["snapshot_current"])
        self.lock(programme,"fhir-a",self.fhir_source(pid))
        self.assertFalse(service.detail(self.root,admin,ident)["coverage"]["snapshot_current"])
        with self.assertRaises(access.AccessDenied):
            service.preview(self.root,admin,{**s,"policy_id":"late-policy"})
        with self.assertRaises(access.AccessDenied):
            service.decision_preview(self.root,admin,ident,"refer_external_assessment","Stale evidence")


if __name__=="__main__":
    unittest.main()
