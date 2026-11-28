"""Exercise native institutional coverage verification, export and comparison."""
import unittest
from caeval import institution_gap_handoff as handoff, unified_gap_comparison as comparison
from tests_unit.test_unified_assessment import UnifiedAssessmentTests


class NativeGapComparisonTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    post=UnifiedAssessmentTests.post
    plan=UnifiedAssessmentTests.plan
    spec=UnifiedAssessmentTests.spec
    freeze=UnifiedAssessmentTests.freeze
    fhir_source=UnifiedAssessmentTests.fhir_source
    lock=UnifiedAssessmentTests.lock

    def test_native_export_before_after_association(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        admin=self.actors["admin"]
        initial=handoff.preview(self.root,admin,ident)
        baseline=handoff.export(self.root,admin,ident,initial["digest"],confirmed=True)
        source=self.fhir_source(pid)
        self.lock(ident,"fhir-a",source)
        later=handoff.preview(self.root,admin,ident)
        followup=handoff.export(self.root,admin,ident,later["digest"],confirmed=True)
        kw={"baseline_digest":initial["digest"],"followup_digest":later["digest"],
            "verify_handoff":handoff.verify}
        result=comparison.compare(baseline,followup,**kw)
        row=next(r for r in result["requirements"] if r["requirement_id"]=="fhir-a")
        self.assertEqual(row["association_change"],"association_added")
        self.assertEqual(row["baseline_coverage"],"unlinked")
        self.assertEqual(row["followup_coverage"],"source_available")
        self.assertEqual(row["followup_condition"],"noncanonical_source_available")
        self.assertFalse(result["decision_grade"])
        self.assertEqual(comparison.verify(result,baseline,followup,
            expected_digest=result["digest"],**kw)["status"],"ADMINISTRATIVE_COMPARISON_REPLAYED")


if __name__=="__main__": unittest.main()
