"""Browser authorization and deterministic evidence inventory for multimodal readiness."""
import json
import unittest
from caeval import (image_text_readiness as image,institution_image_text as service,
                    institution_committee as committee,institution_access as access)
from tests_unit.test_fhir_readiness import FhirBrowserTests


class ImageBrowserTests(unittest.TestCase):
    setUp=FhirBrowserTests.setUp
    request=FhirBrowserTests.request
    post=FhirBrowserTests.post

    def test_scoped_browser_replay_and_inventory(self):
        FhirBrowserTests.fixture(self)
        p=service.prepare(self.root,self.actors["operator"],self.pid)["plan"]
        url="/image-text?plan_id="+self.pid
        self.assertEqual(self.request("operator",url)["status"],"200 OK")
        self.assertEqual(self.request("observer",url)["status"],"403 Forbidden")
        template=image.template(p)
        for row,expected in zip(template["responses"],p["rows"]):
            row.update(status=expected["expected_status"],value=expected["expected_value"])
        fields={"plan_id":self.pid,"responses":json.dumps(template),"confirm":"yes"}
        self.assertEqual(self.request("operator","/image-text-submit","POST",fields)["status"],"403 Forbidden")
        result=self.post("operator","/image-text-submit",fields)
        self.assertEqual(result["status"],"303 See Other")
        ident=result["headers"]["Location"].split("id=")[1]
        self.assertEqual(self.request("operator","/image-text-report?id="+ident)["status"],"200 OK")
        pkg=json.loads(self.request("admin","/image-text-evidence?id="+ident)["body"])
        self.assertEqual(image.verify(pkg)["summary"]["exact_match"],12)
        rows=committee.overview(self.root,self.actors["observer"])["lifecycle_inventory"]["synthetic_image_text_assessments"]
        self.assertEqual(rows[0]["summary"]["exact_match"],12)
        self.assertFalse(rows[0]["decision_grade"])
        with access.db(self.root) as conn:
            conn.execute("DELETE FROM synthetic_image_text_reports")
        with self.assertRaises(access.AccessDenied):
            service.inventory(self.root,self.actors["observer"])
