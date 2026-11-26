
"""Fixed synthetic HL7 v2 exact extraction and institutional evidence."""
import copy
import json
import unittest
from caeval import hl7_readiness as hl7, buyer_agent as buyer, institution_hl7 as service
from caeval import institution_access as access, institution_committee as committee
from tests_unit.test_fhir_readiness import FhirBrowserTests


def exact(plan):
    doc=hl7.template(plan)
    for row,ref in zip(doc["responses"],plan["rows"]):
        row.update(status=ref["expected_status"],facts=copy.deepcopy(ref["expected_facts"]))
    return doc


class HL7Tests(unittest.TestCase):
    def test_exact_replay_and_hidden_reference(self):
        p=hl7.build({"name":"Synthetic target","version":"1"})
        ex=hl7.exchange(p)
        self.assertEqual(p["hl7_version"],"2.5.1")
        for row in ex["probes"]:
            self.assertEqual(set(row),{"slot","patient_id","messages"})
            for message in row["messages"]:
                self.assertTrue(message.startswith("MSH|"))
                self.assertTrue(message.endswith("\r"))
                for seg in message.split("\r"):
                    if seg.startswith("OBX|"):
                        self.assertIn(seg.split("|")[11],("F","P","D"))
        evidence=hl7.package(p,exact(p))
        self.assertEqual(hl7.verify(evidence,evidence["digest"])["summary"],
                         {"planned":10,"exact_match":10,"mismatch":0,"unavailable":0})
        evidence["report"]["summary"]["exact_match"]=0
        with self.assertRaises(buyer.BuyerError):
            hl7.verify(evidence)

    def test_missing_zero_units_patient_and_unavailable(self):
        p=hl7.build({"name":"Synthetic target","version":"1"})
        d=exact(p)
        by={r["probe"]:v for r,v in zip(p["rows"],d["responses"])}
        by["patient_isolation"]["facts"].append(hl7._fact("other-order",value=99))
        by["zero_not_missing"].update(status="no_eligible_observations",facts=[])
        by["missing_value"].update(status="complete",facts=[hl7._fact(value=0)])
        by["unit_preservation"]["facts"][0]["unit_code"]="mg/L"
        by["preliminary_excluded"].update(status="unavailable",facts=[])
        report=hl7.assess(p,d)
        self.assertEqual(report["summary"],{"planned":10,"exact_match":5,"mismatch":4,"unavailable":1})
        self.assertFalse(report["decision_grade"])


class HL7BrowserTests(unittest.TestCase):
    setUp=FhirBrowserTests.setUp
    request=FhirBrowserTests.request
    post=FhirBrowserTests.post

    def test_browser_submission_and_audited_inventory(self):
        FhirBrowserTests.fixture(self)
        p=service.prepare(self.root,self.actors["operator"],self.pid)["plan"]
        page="/hl7?plan_id="+self.pid
        self.assertEqual(self.request("operator",page)["status"],"200 OK")
        self.assertEqual(self.request("observer",page)["status"],"403 Forbidden")
        fields={"plan_id":self.pid,"responses":json.dumps(exact(p)),"confirm":"yes"}
        self.assertEqual(self.request("operator","/hl7-submit","POST",fields)["status"],"403 Forbidden")
        response=self.post("operator","/hl7-submit",fields)
        self.assertEqual(response["status"],"303 See Other")
        ident=response["headers"]["Location"].split("id=")[1]
        self.assertEqual(self.request("operator","/hl7-report?id="+ident)["status"],"200 OK")
        package=json.loads(self.request("admin","/hl7-evidence?id="+ident)["body"])
        self.assertEqual(hl7.verify(package)["summary"]["exact_match"],10)
        rows=committee.overview(self.root,self.actors["observer"])["lifecycle_inventory"]["synthetic_hl7_assessments"]
        self.assertEqual(rows[0]["summary"]["exact_match"],10)
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM synthetic_hl7_reports")
        with self.assertRaises(access.AccessDenied):
            service.inventory(self.root,self.actors["observer"])
