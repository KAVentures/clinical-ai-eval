"""Buyer coverage never substitutes experimental research for canonical evidence."""
import json
import tempfile
import unittest
from pathlib import Path

from caeval import (buyer_agent as buyer, buyer_plan, intake, institution_access as access,
                    institution_research as studio, research_buyer_coverage as bridge,
                    decision_assessment as decision)
from tests_unit.test_decision_assessment import fixture, bind_raw


class CoverageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/"workspace"
        buyer.init(self.root,"Synthetic hospital")
        buyer.add_vendor(self.root,"fixture","Synthetic vendor")
        for uid,role in (("admin","admin"),("owner","operator"),("other","operator"),
                         ("observer","observer"),("reviewer","reviewer")):
            access.provision(self.root,uid,role,"Synthetic coverage password!")
        self.actors={uid:access.session(self.root,access.login(self.root,uid,"Synthetic coverage password!"))
                     for uid in ("admin","owner","other","observer","reviewer")}
        self.spec=fixture()
        self.study=studio.create(self.root,self.actors["owner"],"decision",self.spec)
        self.plan=self.make_plan()

    def make_plan(self,version=None,profiles=None):
        target=self.spec["target"]
        return buyer_plan.preview(self.root,{"vendor_id":"fixture","target_name":target["name"],
            "target_version":version or target["version"],
            "profiles":profiles or ["clinician_decision_support"],
            "answers":{q:"Synthetic intended scope for "+q for q in intake.INTAKE_QUESTIONS}})

    def link(self,actor="owner",plan=None):
        return bridge.link(self.root,self.actors[actor],self.study,
                           (plan or self.plan)["plan_id"],confirmed=True)

    def coverage(self,actor="owner"):
        return bridge.coverage(self.root,self.actors[actor],self.plan["plan_id"])

    def analyze(self):
        plan=studio.download(self.root,self.actors["owner"],self.study,"plan")
        responses=decision.response_template(plan)
        for item,row in zip(plan["items"],responses["responses"]):
            ref=item["reference"]
            row.update(status="complete",verdict=ref["verification"]["verdict"],
                       certificate=ref["certificate"],requested_facts=ref["unknown_facts"])
            bind_raw(row)
        studio.collect(self.root,self.actors["owner"],self.study,plan["plan_hash"],responses,confirmed=True)
        studio.analyze(self.root,self.actors["admin"],self.study,confirmed=True)

    def test_prospective_association_and_canonical_gap_remains(self):
        baseline=self.coverage()
        self.assertEqual(baseline["studies"],[])
        digest=self.link()
        pending=self.coverage()
        self.assertEqual(pending["studies"][0]["link_digest"],digest)
        self.assertTrue(pending["studies"][0]["prospective_link"])
        self.assertEqual(pending["studies"][0]["evidence_state"],"not_analyzed")
        self.analyze()
        doc=self.coverage()
        study=doc["studies"][0]
        self.assertEqual(study["evidence_state"],"verified_experimental_report")
        self.assertTrue(study["synthetic"])
        self.assertEqual(study["summary"]["verdict_agreement"]["rate_on_assessed"],1)
        row=next(x for x in doc["suites"] if x["suite"]=="decision_certifiability")
        self.assertEqual(row["canonical_status"],"required_but_not_run")
        self.assertEqual(row["verified_experimental_reports"],1)
        self.assertEqual(row["non_synthetic_experimental_reports"],0)
        self.assertFalse(row["canonical_requirement_satisfied"])
        self.assertEqual(doc["required_but_not_run"],baseline["required_but_not_run"])
        self.assertFalse(doc["decision_grade"])
        self.assertEqual(self.coverage("admin")["studies"],doc["studies"])
        self.assertEqual(self.coverage("other")["studies"],[])

    def test_wrong_scope_version_and_roles_refused(self):
        for p in (self.make_plan(version="wrong"),self.make_plan(profiles=["medical_scribe"])):
            with self.assertRaises(access.AccessDenied):
                self.link(plan=p)
        with self.assertRaises(access.AccessDenied):
            self.link(actor="other")
        for uid in ("observer","reviewer"):
            with self.assertRaises(access.AccessDenied):
                self.coverage(uid)
        with self.assertRaises(access.AccessDenied):
            bridge.link(self.root,self.actors["owner"],self.study,self.plan["plan_id"])
        self.link()
        with self.assertRaises(access.AccessDenied):
            self.link()

    def test_retrospective_and_report_tampering(self):
        self.analyze()
        self.link()
        self.assertFalse(self.coverage()["studies"][0]["prospective_link"])
        path=self.root/"research-studies"/self.study/"report.json"
        report=json.loads(path.read_text())
        report["summary"]["completed"]=0
        path.write_text(json.dumps(report))
        doc=self.coverage()
        self.assertEqual(doc["unavailable_associations"],1)
        self.assertEqual(doc["studies"][0]["evidence_state"],"unavailable")
        self.assertTrue(all(row["verified_experimental_reports"]==0 for row in doc["suites"]))

    def test_association_integrity_and_scope_drift(self):
        self.link()
        with access.db(self.root) as conn:
            conn.execute("UPDATE buyer_research_links SET digest='bad'")
        self.assertEqual(self.coverage()["unavailable_associations"],1)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM buyer_research_links")
        self.link()
        # Changed buyer scope must not silently inherit old research evidence.
        state=buyer.load(self.root)
        state["intended_use"]="Changed intended use"
        buyer._write_json(self.root/buyer.STATE_FILE,state)
        with self.assertRaises(access.AccessDenied):
            self.coverage()


if __name__=="__main__":
    unittest.main()
