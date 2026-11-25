"""Fixed synthetic FHIR R4 extraction, exact replay and institutional boundaries."""
import copy
import json
import unittest
from unittest.mock import patch

from caeval import (fhir_readiness as fhir, buyer_agent as buyer, buyer_plan, intake,
                    institution_fhir as service, institution_access as access,
                    institution_committee as committee)
from tests_unit import test_institution_research as browser


def exact(plan):
    doc=fhir.template(plan)
    for row,ref in zip(doc["responses"],plan["rows"]):
        row.update(status=ref["expected_status"],facts=copy.deepcopy(ref["expected_facts"]))
    return doc


class FhirTests(unittest.TestCase):
    def setUp(self):
        self.plan=fhir.build({"name":"Synthetic product","version":"1"})

    def test_synthetic_replay_and_order_independence(self):
        with patch("urllib.request.urlopen",side_effect=AssertionError("no network")):
            responses=exact(self.plan)
            responses["responses"].reverse()
            for row in responses["responses"]:
                row["facts"].reverse()
            package=fhir.package(self.plan,responses)
            receipt=fhir.verify(package,package["digest"])
        self.assertEqual(receipt["summary"],{"planned":9,"exact_match":9,"mismatch":0,"unavailable":0})
        self.assertFalse(receipt["decision_grade"])
        self.assertFalse(receipt["deployment_authorized"])
        for row in fhir.exchange(self.plan)["probes"]:
            self.assertEqual(row["bundle"]["type"],"collection")
            self.assertNotIn("expected_facts",row)

    def test_cross_subject_error_absent_zero_and_units(self):
        doc=exact(self.plan)
        rows={ref["probe"]:row for ref,row in zip(self.plan["rows"],doc["responses"])}
        rows["subject_isolation"]["facts"].append(fhir._fact("obs-other",99))
        rows["entered_in_error"]["facts"].append(fhir._fact("obs-error",99))
        rows["absent_value"].update(status="complete",facts=[fhir._fact("obs-missing",0)])
        rows["zero_not_missing"].update(status="no_eligible_observations",facts=[])
        rows["unit_preservation"]["facts"][0]["unit_code"]="mg/L"
        rows["preliminary_excluded"].update(status="unavailable",facts=[])
        report=fhir.assess(self.plan,doc)
        self.assertEqual(report["summary"],{"planned":9,"exact_match":3,"mismatch":5,"unavailable":1})
        byprobe={r["probe"]:r for r in report["rows"]}
        self.assertEqual(byprobe["subject_isolation"]["unexpected"],["obs-other"])
        self.assertEqual(byprobe["unit_preservation"]["changed"],["obs-one"])
        self.assertEqual(byprobe["zero_not_missing"]["missing"],["obs-zero"])
        self.assertIsNone(byprobe["preliminary_excluded"]["missing"])

    def test_structured_code_token_boundaries(self):
        # Valid UCUM-like fixture tokens must not be rejected by the allowlist.
        for unit in ("[IU]/mL","mg/L","{cells}/uL","10*3/uL"):
            self.assertIn("x",fhir._facts([fhir._fact("x",unit=unit)]))
        for unit in ("mg/L free text","mg/L\n","mg/L\\","<script>"):
            with self.assertRaises(buyer.BuyerError):
                fhir._facts([fhir._fact("x",unit=unit)])

    def test_wrong_target_duplicate_missing_nonfinite_and_modified_evidence(self):
        for change in ("target","duplicate","missing","bool","nonfinite","extra"):
            doc=exact(self.plan)
            if change=="target":
                doc["target"]["version"]="2"
            elif change=="duplicate":
                doc["responses"][1]["slot"]=doc["responses"][0]["slot"]
            elif change=="missing":
                doc["responses"].pop()
            elif change=="bool":
                doc["responses"][0]["facts"][0]["value"]=True
            elif change=="nonfinite":
                doc["responses"][0]["facts"][0]["value"]=float("nan")
            else:
                doc["responses"][0]["clinical_note"]="not allowed"
            with self.assertRaises(buyer.BuyerError,msg=change):
                fhir.assess(self.plan,doc)
        package=fhir.package(self.plan,exact(self.plan))
        with self.assertRaises(buyer.BuyerError):
            fhir.verify(package,"0"*64)
        package["report"]["summary"]["mismatch"]=1
        with self.assertRaises(buyer.BuyerError):
            fhir.verify(package)
        bad=copy.deepcopy(self.plan)
        bad["decision_grade"]=0
        with self.assertRaises(buyer.BuyerError):
            fhir.exchange(bad)


class FhirBrowserTests(unittest.TestCase):
    setUp=browser.StudioTests.setUp
    request=browser.StudioTests.request
    post=browser.StudioTests.post

    def fixture(self):
        buyer.add_vendor(self.root,"candidate","Synthetic candidate")
        self.pid=buyer_plan.preview(self.root,{"vendor_id":"candidate","target_name":"Synthetic candidate",
            "target_version":"1","profiles":["clinician_decision_support"],
            "answers":{q:"Fictional "+q for q in intake.INTAKE_QUESTIONS}})["plan_id"]
        self.plan=service.prepare(self.root,self.actors["operator"],self.pid)["plan"]

    def test_browser_roles_snapshot_and_offline_package(self):
        self.fixture()
        old=committee.freeze(self.root,self.actors["admin"])["snapshot_id"]
        page="/fhir?plan_id="+self.pid
        self.assertEqual(self.request("operator",page)["status"],"200 OK")
        self.assertEqual(self.request("observer",page)["status"],"403 Forbidden")
        raw=self.request("operator","/fhir-download?plan_id="+self.pid+"&kind=exchange")
        self.assertEqual(raw["status"],"200 OK")
        self.assertEqual(json.loads(raw["body"]),fhir.exchange(self.plan))
        fields={"plan_id":self.pid,"responses":json.dumps(exact(self.plan)),"confirm":"yes"}
        self.assertEqual(self.request("operator","/fhir-submit","POST",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("doctor-a","/fhir-submit",fields)["status"],"403 Forbidden")
        self.assertEqual(self.post("operator","/fhir-submit",{**fields,"confirm":"no"})["status"],"403 Forbidden")
        posted=self.post("operator","/fhir-submit",fields)
        self.assertEqual(posted["status"],"303 See Other")
        ident=posted["headers"]["Location"].split("id=")[1]
        self.assertEqual(self.request("operator","/fhir-report?id="+ident)["status"],"200 OK")
        package=json.loads(self.request("admin","/fhir-evidence?id="+ident)["body"])
        self.assertEqual(fhir.verify(package)["summary"]["exact_match"],9)
        self.assertEqual(self.request("observer","/fhir-evidence?id="+ident)["status"],"403 Forbidden")
        rows=committee.overview(self.root,self.actors["observer"])["lifecycle_inventory"]["synthetic_fhir_assessments"]
        self.assertEqual(rows[0]["summary"]["exact_match"],9)
        self.assertNotIn("responses",rows[0])
        self.assertFalse(committee.snapshot(self.root,self.actors["observer"],old)["current"])

    def test_missing_audited_report_is_not_silently_recreated(self):
        self.fixture()
        service.submit(self.root,self.actors["operator"],self.pid,exact(self.plan),confirmed=True)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM synthetic_fhir_reports")
        with self.assertRaises(access.AccessDenied):
            service.inventory(self.root,self.actors["observer"])
        with self.assertRaises(access.AccessDenied):
            service.submit(self.root,self.actors["operator"],self.pid,exact(self.plan),confirmed=True)

    def test_audit_tampering_refused(self):
        self.fixture()
        ident=service.submit(self.root,self.actors["operator"],self.pid,exact(self.plan),confirmed=True)
        with access.db(self.root) as conn:
            conn.execute("UPDATE audit SET actor='changed' WHERE action=? AND object_id=?",(service.EVENT,ident))
        self.assertEqual(service.inventory(self.root,self.actors["observer"])[0]["state"],"unavailable")
        with self.assertRaises(access.AccessDenied):
            service.detail(self.root,self.actors["operator"],ident)


if __name__=="__main__":
    unittest.main()
