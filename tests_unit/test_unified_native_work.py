"""Bounded work scan and actual native FHIR/imaging integration regressions."""
import json
import unittest
from caeval import unified_native_work as native, unified_work as work
from caeval import institution_access as access
from tests_unit.test_unified_assessment import UnifiedAssessmentTests


class NativeProjectionTests(unittest.TestCase):
    def test_scan_truncation_and_failures_are_visible(self):
        def read(ident):
            if ident=="broken":
                raise ValueError("invalid source")
            if ident=="other-scope":
                return None
            return native._record(ident,"collecting","experimental","vendor_id_and_version_only")
        result=native._scan(["good","broken","other-scope","not-scanned"],read,limit=3)
        self.assertEqual(result["state"],"partial")
        self.assertTrue(result["scan_truncated"])
        self.assertEqual(result["unverified_records"],1)
        self.assertEqual([r["id"] for r in result["records"]],["good"])
        self.assertFalse(result["records"][0]["association_authorized"])

    def test_empty_bounded_scan_is_not_authoritative(self):
        result=native._scan([],lambda x:x)
        self.assertEqual(result,{"records":[],"state":"bounded_scan","scan_truncated":False,"unverified_records":0})
        for limit in (0,201,True):
            with self.assertRaises(ValueError):
                native._scan([],lambda x:x,limit)

    def test_exact_candidate_version_and_vendor(self):
        plan={"vendor_id":"a","target":{"version":"v1"}}
        for candidates,expected in (([{"id":"a","version":"v1"}],True),
                                     ([{"id":"a","version":"v2"}],False),
                                     ([{"id":"b","version":"v1"}],False)):
            self.assertEqual(native._candidate_matches({"spec":{"candidates":candidates}},plan),expected)


class NativeWorkIntegrationTests(unittest.TestCase):
    setUp=UnifiedAssessmentTests.setUp
    request=UnifiedAssessmentTests.request
    plan=UnifiedAssessmentTests.plan
    spec=UnifiedAssessmentTests.spec
    freeze=UnifiedAssessmentTests.freeze
    fhir_source=UnifiedAssessmentTests.fhir_source
    imaging_source=UnifiedAssessmentTests.imaging_source

    def rows(self,ident):
        return {r["kind"]:r for r in work.work(self.root,self.actors["operator"],ident)["requirements"]}

    def test_native_fhir_and_imaging_scope_without_association(self):
        pid=self.plan()
        # Use a different vendor so creation cannot change the original vendor version.
        other=self.plan(vid="b",version="v2")
        ident=self.freeze(self.spec(pid))
        good=self.fhir_source(pid)
        wrong=self.fhir_source(other)
        study=self.imaging_source()
        rows=self.rows(ident)
        self.assertEqual([r["id"] for r in rows["fhir"]["native_work_records"]],[good])
        self.assertNotIn(wrong,json.dumps(rows))
        self.assertEqual(rows["fhir"]["native_work_records"][0]["scope"],"exact_buyer_plan")
        imaging=rows["imaging"]["native_work_records"][0]
        self.assertEqual(imaging["id"],study)
        self.assertEqual(imaging["scope"],"vendor_id_and_version_only")
        self.assertFalse(imaging["association_authorized"])
        self.assertEqual(rows["imaging"]["coverage_state"],"unlinked")
        self.assertEqual(self.request("operator","/unified-assessment-work?id="+ident)["status"],"200 OK")
        self.assertEqual(self.request("observer","/unified-assessment-work?id="+ident)["status"],"403 Forbidden")

    def test_corrupt_native_report_is_explicit_partial_inventory(self):
        pid=self.plan()
        ident=self.freeze(self.spec(pid))
        good=self.fhir_source(pid)
        with access.db(self.root) as conn:
            conn.execute("UPDATE synthetic_fhir_reports SET payload='{}' WHERE id=?",(good,))
        row=self.rows(ident)["fhir"]
        self.assertEqual(row["native_work_records"],[])
        self.assertEqual(row["work_inventory_state"],"partial")
        self.assertEqual(row["unverified_records"],1)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM synthetic_fhir_reports WHERE id=?",(good,))
        row=self.rows(ident)["fhir"]
        self.assertEqual(row["work_inventory_state"],"unavailable")
        self.assertIsNone(row["unverified_records"])


if __name__=="__main__":
    unittest.main()
