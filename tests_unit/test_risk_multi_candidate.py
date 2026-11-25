"""Multi-candidate risk conditions and verified institutional evidence handoff."""
import json
import unittest

from caeval import (buyer_agent as buyer, buyer_plan, buyer_risk_activation as activation,
                    buyer_risk_handoff as handoff, buyer_assessment as assessment,
                    buyer_jobs as jobs, buyer_manual as manual, institution_access as access,
                    institution_committee as committee, procurement_workflow as pw)
from tests_unit import test_buyer_risk_activation as fixture


class MultiCandidateTests(unittest.TestCase):
    setUp=fixture.RiskActivationTests.setUp
    staged=fixture.RiskActivationTests.staged
    prepare_exchange=fixture.RiskActivationTests.prepare_exchange
    fill=fixture.RiskActivationTests.fill
    submit=fixture.RiskActivationTests.submit

    def second(self,*,changed=False,duplicate=False):
        buyer.add_vendor(self.root,"second","Second candidate")
        primary=self.plan["plan"]
        answers=dict(primary["intake"])
        if changed:
            answers["plausible_harm"]="Different harm scope"
        return buyer_plan.preview(self.root,{"vendor_id":"second",
            "target_name":primary["target"]["name"] if duplicate else "Other product",
            "target_version":primary["target"]["version"] if duplicate else "2",
            "profiles":primary["target_profile"]["types"],"answers":answers})["plan_id"]

    def activate_multi(self,other):
        args=(self.pid,"fixture","mock",self.mappings)
        preview=activation.preview(self.root,self.operator,*args,additional_plans=[other])
        return activation.activate(self.root,self.admin,*args,preview["digest"],
                                   additional_plans=[other],confirmed=True)

    def completed(self,plan_id,version,conditions_hash):
        doc=manual.prepare(self.root,plan_id,"fixture")
        doc=self.fill(doc)
        doc["submission"]["tested_product_version"]=version
        sub=self.submit(doc)
        bound=assessment.prepare(self.root,sub["submission_id"],"mock",self.gov,
                                 conditions_hash=conditions_hash)
        job=jobs.enqueue(self.root,bound["assessment_id"])
        result=jobs.execute(self.root,job["id"])
        self.assertEqual(result["status"],"completed",result)
        return result["id"]

    def test_scope_and_identity_mismatch(self):
        self.staged()
        for other in (self.second(changed=True),):
            with self.assertRaises(access.AccessDenied):
                self.activate_multi(other)
        primary=self.plan["plan"]
        duplicate=buyer_plan.preview(self.root,{"vendor_id":"second",
            "target_name":primary["target"]["name"],"target_version":primary["target"]["version"],
            "profiles":primary["target_profile"]["types"],"answers":primary["intake"]})["plan_id"]
        with self.assertRaises(access.AccessDenied):
            self.activate_multi(duplicate)
        self.assertFalse((self.root/"assessment"/pw.STATE_FILE).exists())

    def test_frozen_roster_and_approval_digest(self):
        self.staged()
        other=self.second()
        args=(self.pid,"fixture","mock",self.mappings)
        preview=activation.preview(self.root,self.operator,*args,additional_plans=[other])
        self.assertEqual(set(activation.candidates(preview)),{"vendor","second"})
        with self.assertRaises(access.AccessDenied):
            activation.activate(self.root,self.admin,*args,preview["digest"],confirmed=True)
        active=self.activate_multi(other)
        state=pw.load(self.root/"assessment")
        self.assertTrue(activation.roster_matches(state,active["binding"]))
        self.assertEqual(len(state["vendors"]),2)
        with self.assertRaises(pw.ProcurementError):
            pw.add_vendor(self.root/"assessment","third",{"name":"Third","version":"1"})

    def test_two_jobs_handoff_committee_and_missing_vendor(self):
        self.staged()
        other=self.second()
        active=self.activate_multi(other)
        condition=active["conditions_hash"]
        first=self.completed(self.pid,self.plan["plan"]["target"]["version"],condition)
        with self.assertRaises(access.AccessDenied):
            handoff.ingest(self.root,self.operator,first,condition,confirmed=True)
        with self.assertRaises(access.AccessDenied):
            handoff.ingest(self.root,self.admin,first,"wrong",confirmed=True)
        receipt=handoff.ingest(self.root,self.admin,first,condition,confirmed=True)
        self.assertEqual(receipt["status"],"recorded")
        self.assertEqual(handoff.ingest(self.root,self.admin,first,condition,confirmed=True)["status"],"already_recorded")
        overview=committee.overview(self.root,self.admin)
        families=overview["clinical_comparison"]["families"]
        self.assertTrue(families)
        for entry in families:
            self.assertEqual(entry["comparison"]["comparability"]["status"],"INCOMPARABLE")
            self.assertTrue(entry["comparison"]["vendors_without_results"])
            for hazard in entry["comparison"]["per_hazard"]:
                self.assertEqual(len(hazard["products"]),2)
                missing=[row for row in hazard["products"] if row["product_id"] in entry["comparison"]["vendors_without_results"]]
                self.assertEqual(len(missing),1)
                self.assertEqual(missing[0]["status"],"NO_EVIDENCE")
                self.assertEqual(missing[0]["n_total"],len(active["binding"]["cell_inventory"][entry["family_id"]]))
                self.assertEqual(missing[0]["n_missing"],missing[0]["n_total"])
                self.assertFalse(hazard["meets"])
                self.assertFalse(hazard["does_not_meet"])
            self.assertEqual(len(entry["verified_packages"]),1)
        second=self.completed(other,"2",condition)
        self.assertEqual(handoff.ingest(self.root,self.admin,second,condition,confirmed=True)["vendor_id"],"second")
        state=pw.load(self.root/"assessment")
        self.assertEqual(set(state["results"]),{"vendor","second"})
        for family in active["binding"]["families"]:
            comparison=pw.compare(self.root/"assessment",family)
            self.assertFalse(comparison["vendors_without_results"])
            self.assertFalse(comparison["claim_authority"]["decision_grade"])
        final=committee.overview(self.root,self.admin)
        self.assertTrue(all(len(e["verified_packages"])==2 for e in final["clinical_comparison"]["families"]))

    def test_evaluator_config_digest_distinguishes_same_named_panel(self):
        from caeval import pipeline
        import copy
        self.staged()
        _,panel=assessment._panel(self.root,"mock")
        changed=copy.deepcopy(panel)
        changed["judges"][0]["mode"]="rubric_aware"
        self.assertNotEqual(pipeline.execution_panel_digest(panel),pipeline.execution_panel_digest(changed))
        reordered={k:panel[k] for k in reversed(list(panel))}
        self.assertEqual(pipeline.execution_panel_digest(panel),pipeline.execution_panel_digest(reordered))

    def test_unbound_job_cannot_enter_study(self):
        self.staged()
        active=self.activate_multi(self.second())
        sub=self.submit(self.fill(self.prepare_exchange()))
        bound=assessment.prepare(self.root,sub["submission_id"],"mock",self.gov)
        job=jobs.enqueue(self.root,bound["assessment_id"])
        result=jobs.execute(self.root,job["id"])
        self.assertEqual(result["status"],"completed",result)
        with self.assertRaises(access.AccessDenied):
            handoff.ingest(self.root,self.admin,job["id"],active["conditions_hash"],confirmed=True)
        self.assertEqual(pw.load(self.root/"assessment")["results"],{})


if __name__=="__main__":
    unittest.main()
