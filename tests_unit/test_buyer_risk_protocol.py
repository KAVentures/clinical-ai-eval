"""Buyer risk planning: immutable scope, explicit gaps, browser permissions."""
import json
import unittest

from caeval import (buyer_agent as buyer, buyer_plan, intake,
                    buyer_risk_protocol as risk, institution_access as access,
                    institution_committee as committee)
from tests_unit import test_institution_research as browser


class BuyerRiskTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def fixture(self):
        buyer.add_vendor(self.root,"fictional","Fictional candidate")
        self.plan=buyer_plan.preview(self.root,{"vendor_id":"fictional",
            "target_name":"Fictional CDS","target_version":"1",
            "profiles":["clinician_decision_support"],
            "answers":{q:"Fictional intake "+q for q in intake.INTAKE_QUESTIONS}})
        self.pid=self.plan["plan_id"]
        self.governance={k:"Private governance "+k for k in
            ("population","setting_boundaries","oversight","data_governance","jurisdiction_review","stop_rules")}
        self.suite=self.plan["plan"]["selection"]["required_suites"][0]["suite"]
        self.hazard={"hazard_id":"hazard-one","suite":self.suite,"description":"Private hazard",
            "severity":"critical","endpoint":"Fictional adverse outcome proportion",
            "denominator":"All eligible fictional cases; unknown remains explicit",
            "operator":"<=","threshold":0.05,"rationale":"Private threshold rationale",
            "mitigation":"Pause fictional pilot"}

    def freeze(self,**changes):
        return risk.freeze(self.root,self.actors["operator"],self.pid,
                           [{**self.hazard,**changes}],self.governance,confirmed=True)

    def fields(self):
        data={"plan_id":self.pid,"confirm":"yes",**self.governance}
        keys=("description","endpoint","denominator","rationale","mitigation","severity","operator","threshold")
        for row in self.plan["plan"]["selection"]["required_suites"]:
            sid=row["suite"]
            for key in keys:
                data[key+"_"+sid]=str(self.hazard[key]) if sid==self.suite else ""
        data["use_"+self.suite]="yes"
        return data

    def test_browser_freeze_download_and_committee_scope(self):
        self.fixture()
        before=committee.freeze(self.root,self.actors["admin"])["snapshot_id"]
        path="/buyer-risk?plan_id="+self.pid
        self.assertEqual(self.request("operator",path)["status"],"200 OK")
        self.assertIn(b"Threshold (proportion",self.request("operator",path)["body"])
        self.assertEqual(self.request("observer",path)["status"],"403 Forbidden")
        route="/buyer-risk-freeze"
        self.assertEqual(self.request("operator",route,"POST",self.fields())["status"],"403 Forbidden")
        self.assertEqual(self.post("observer",route,self.fields())["status"],"403 Forbidden")
        self.assertEqual(self.post("operator",route,self.fields())["status"],"303 See Other")
        self.assertEqual(self.post("admin",route,self.fields())["status"],"403 Forbidden")
        out=self.request("operator","/buyer-risk-download?plan_id="+self.pid)
        self.assertEqual(out["status"],"200 OK")
        doc=json.loads(out["body"])
        self.assertEqual(doc["id"],buyer_plan._digest(doc["protocol"]))
        self.assertTrue(doc["scope_current"])
        self.assertFalse(doc["protocol"]["execution_authorized"])
        self.assertFalse(doc["protocol"]["decision_grade"])
        gaps=[s for s in doc["protocol"]["coverage"] if not s["hazard_ids"]]
        self.assertTrue(gaps)
        self.assertTrue(all(not s["measurement_verified"] for s in doc["protocol"]["coverage"]))
        projection=committee.overview(self.root,self.actors["observer"])["lifecycle_inventory"]["buyer_risk_protocols"][0]
        self.assertEqual(projection["critical_hazards"],1)
        self.assertEqual(projection["uncovered_suites"],[s["suite"] for s in gaps])
        for text in ("Private governance","Private hazard","Private threshold rationale","operator"):
            self.assertNotIn(text,json.dumps(projection))
        self.assertFalse(committee.snapshot(self.root,self.actors["observer"],before)["current"])

    def test_invalid_criteria_and_unselected_suite(self):
        self.fixture()
        for change in ({"threshold":True},{"threshold":-1},{"threshold":1.1},
                       {"threshold":float("nan")},{"operator":"=="},{"suite":"unknown"},
                       {"rationale":""},{"denominator":""}):
            with self.assertRaises(access.AccessDenied):
                self.freeze(**change)
        self.freeze()

    def test_stale_scope_preserves_original_record(self):
        self.fixture()
        ident=self.freeze()
        state=buyer.load(self.root)
        state["intended_use"]="Changed fictional scope"
        buyer._write_json(self.root/buyer.STATE_FILE,state)
        doc=risk.view(self.root,self.actors["operator"],self.pid)
        self.assertFalse(doc["scope_current"])
        self.assertEqual(doc["id"],ident)
        self.assertEqual(risk.committee_inventory(self.root,self.actors["observer"])[0]["state"],"stale")

    def test_missing_audited_risk_protocol_is_not_silently_recreated(self):
        self.fixture()
        self.freeze()
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM buyer_risk_protocols")
        with self.assertRaises(access.AccessDenied):
            risk.view(self.root,self.actors["operator"],self.pid)
        with self.assertRaises(access.AccessDenied):
            self.freeze()
        with self.assertRaises(access.AccessDenied):
            risk.committee_inventory(self.root,self.actors["observer"])

    def test_audit_tampering_is_not_accepted(self):
        self.fixture()
        ident=self.freeze()
        with access.db(self.root) as conn:
            conn.execute("UPDATE audit SET actor='changed' WHERE action=? AND object_id=?",(risk.EVENT,ident))
        with self.assertRaises(access.AccessDenied):
            risk.view(self.root,self.actors["operator"],self.pid)
        self.assertEqual(risk.committee_inventory(self.root,self.actors["observer"])[0]["state"],"unavailable")


if __name__=="__main__":
    unittest.main()
