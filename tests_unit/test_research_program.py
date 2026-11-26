"""Frozen buyer-scoped programme and browser dossier acceptance."""
import json
import unittest

from caeval import (buyer_agent as buyer, buyer_plan, intake, institution_access as access,
                    institution_research as studio, research_program as programs,
                    research_buyer_coverage as bridge, decision_assessment as decision)
from tests_unit import test_institution_research as browser
from tests_unit.test_decision_assessment import fixture, bind_raw
from tests_unit.test_robustness_assessment import spec as robustness_spec


class ProgrammeTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def setup_programme(self):
        buyer.add_vendor(self.root,"fixture","Synthetic vendor")
        self.spec=fixture()
        self.plan=buyer_plan.preview(self.root,{"vendor_id":"fixture",
            "target_name":self.spec["target"]["name"],"target_version":self.spec["target"]["version"],
            "profiles":["clinician_decision_support"],
            "answers":{q:"Synthetic scope for "+q for q in intake.INTAKE_QUESTIONS}})
        self.study=studio.create(self.root,self.actors["operator"],"decision",self.spec)
        robust=robustness_spec()
        robust["target"]=self.spec["target"]
        self.robust=studio.create(self.root,self.actors["operator"],"robustness",robust)
        return self.plan["plan_id"]

    def freeze(self,studies=None):
        return programs.freeze(self.root,self.actors["operator"],self.plan["plan_id"],
                    studies or {"decision":self.study,"robustness":self.robust},confirmed=True)

    def finish(self):
        plan=studio.download(self.root,self.actors["operator"],self.study,"plan")
        responses=decision.response_template(plan)
        for item,row in zip(plan["items"],responses["responses"]):
            ref=item["reference"]
            row.update(status="complete",verdict=ref["verification"]["verdict"],
                       certificate=ref["certificate"],requested_facts=ref["unknown_facts"])
            bind_raw(row)
        studio.collect(self.root,self.actors["operator"],self.study,plan["plan_hash"],responses,confirmed=True)
        studio.analyze(self.root,self.actors["admin"],self.study,confirmed=True)

    def test_browser_programme_partial_and_replayed_summary(self):
        pid=self.setup_programme()
        overview="/research-programs?plan_id="+pid
        self.assertEqual(self.request("operator",overview)["status"],"200 OK")
        self.assertEqual(self.request("other",overview)["status"],"200 OK")
        self.assertNotIn(self.study.encode(),self.request("other",overview)["body"])
        self.assertEqual(self.request("doctor-a",overview)["status"],"403 Forbidden")
        fields={"plan_id":pid,"citation":"","scribe":"","decision":self.study,
                "robustness":self.robust,"confirm":"yes"}
        path="/research-program-create"
        self.assertEqual(self.request("operator",path,"POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("other",path,fields)["status"],"403 Forbidden")
        response=self.post("operator",path,fields)
        self.assertEqual(response["status"],"303 See Other")
        location=response["headers"]["Location"]
        ident=location.split("=")[1]
        self.assertEqual(self.request("operator",location)["status"],"200 OK")
        self.assertEqual(self.request("other",location)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator",path,fields)["status"],"403 Forbidden")
        self.finish()
        status=programs.status(self.root,self.actors["operator"],ident)
        self.assertFalse(status["all_planned_reports_available"])
        self.assertFalse(status["all_planned_reports_replayed"])
        export="/research-program-dossier"
        fields={"id":ident,"confirm":"yes"}
        self.assertEqual(self.post("operator",export,fields)["status"],"403 Forbidden")
        self.assertEqual(self.request("admin",export,"POST",fields)["status"],"403 Forbidden")
        result=self.post("admin",export,fields)
        self.assertEqual(result["status"],"200 OK")
        doc=json.loads(result["body"])
        decision_row=next(x for x in doc["members"] if x["kind"]=="decision")
        self.assertTrue(decision_row["replay_verified"])
        self.assertIn("package_digest",decision_row)
        self.assertFalse(doc["all_planned_reports_replayed"])
        self.assertFalse(doc["decision_grade"])
        self.assertTrue(all(not x["canonical_requirement_satisfied"] for x in doc["coverage"]))
        self.assertEqual(doc["digest"],buyer_plan._digest({k:v for k,v in doc.items() if k!="digest"}))

    def test_all_planned_complete_does_not_hide_unplanned(self):
        self.setup_programme()
        ident=self.freeze({"decision":self.study})
        self.finish()
        doc=programs.dossier(self.root,self.actors["admin"],ident,confirmed=True)
        self.assertTrue(doc["all_planned_reports_replayed"])
        self.assertEqual(doc["unplanned_related_modules"],["robustness"])
        self.assertFalse(doc["decision_grade"])
        with self.assertRaises(access.AccessDenied):
            programs.dossier(self.root,self.actors["admin"],ident)

    def test_atomic_failure_after_first_association(self):
        self.setup_programme()
        studio.download(self.root,self.actors["operator"],self.robust,"exchange")
        with self.assertRaises(access.AccessDenied):
            self.freeze()
        self.assertEqual(bridge.coverage(self.root,self.actors["operator"],self.plan["plan_id"])["studies"],[])
        self.assertEqual(programs.inventory(self.root,self.actors["operator"],self.plan["plan_id"])["programmes"],[])
        self.freeze({"decision":self.study})

    def test_tampered_report_and_membership_are_not_accepted(self):
        self.setup_programme()
        ident=self.freeze({"decision":self.study})
        self.finish()
        p=self.root/"research-studies"/self.study/"report.json"
        report=json.loads(p.read_text())
        report["summary"]["completed"]=0
        p.write_text(json.dumps(report))
        doc=programs.dossier(self.root,self.actors["admin"],ident,confirmed=True)
        self.assertEqual(doc["members"][0]["state"],"unavailable")
        self.assertFalse(doc["all_planned_reports_replayed"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM research_program_members WHERE program_id=?",(ident,))
        with self.assertRaises(access.AccessDenied):
            programs.status(self.root,self.actors["operator"],ident)

    def test_existing_matching_link_reused_and_drift_refused(self):
        self.setup_programme()
        digest=bridge.link(self.root,self.actors["operator"],self.study,self.plan["plan_id"],confirmed=True)
        ident=self.freeze({"decision":self.study})
        with access.db(self.root) as conn:
            row=conn.execute("SELECT payload FROM research_programs WHERE id=?",(ident,)).fetchone()
            self.assertEqual(json.loads(row["payload"])["members"]["decision"]["link_digest"],digest)
        state=buyer.load(self.root)
        state["intended_use"]="Changed intended use"
        buyer._write_json(self.root/buyer.STATE_FILE,state)
        with self.assertRaises(access.AccessDenied):
            programs.status(self.root,self.actors["operator"],ident)


if __name__=="__main__":
    unittest.main()
