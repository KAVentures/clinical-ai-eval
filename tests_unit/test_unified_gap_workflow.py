"""Synthetic browser integration for the evidence-gap workflow."""
import unittest
from caeval import unified_work as work
from tests_unit.test_unified_assessment import UnifiedAssessmentTests


class GapWorkflowTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    plan=UnifiedAssessmentTests.plan
    spec=UnifiedAssessmentTests.spec
    freeze=UnifiedAssessmentTests.freeze
    fhir_source=UnifiedAssessmentTests.fhir_source
    lock=UnifiedAssessmentTests.lock

    def test_read_only_candidate_to_associated_workflow(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        admin=self.actors["admin"]
        operator=self.actors["operator"]
        path="/unified-assessment-gaps?id="+ident
        source=self.fhir_source(pid)
        before=work.gaps(self.root,admin,ident)
        row=next(r for r in before["requirements"] if r["kind"]=="fhir")
        self.assertEqual(row["condition"],"not_associated")
        self.assertEqual(row["potential_ready_records"],1)
        self.assertEqual(row["tasks"][0]["code"],"verify_candidate_association")
        admin_page=self.request("admin",path)
        operator_page=self.request("operator",path)
        self.assertEqual(admin_page["status"],"200 OK")
        self.assertEqual(operator_page["status"],"200 OK")
        self.assertIn(b"/unified-assessment-discover?",admin_page["body"])
        self.assertNotIn(b"/unified-assessment-discover?",operator_page["body"])
        for uid in ("observer","doctor-a"):
            self.assertEqual(self.request(uid,path)["status"],"403 Forbidden")
        self.assertEqual(work.gaps(self.root,operator,ident),before)
        self.lock(ident,"fhir-a",source)
        after=work.gaps(self.root,admin,ident)
        row=next(r for r in after["requirements"] if r["kind"]=="fhir")
        self.assertEqual(row["condition"],"noncanonical_source_available")
        self.assertFalse(row["tasks"])
        self.assertNotEqual(before["digest"],after["digest"])
        self.assertFalse(after["canonical_requirements_satisfied"])
        self.assertFalse(after["deployment_authorized"])


if __name__=="__main__": unittest.main()
