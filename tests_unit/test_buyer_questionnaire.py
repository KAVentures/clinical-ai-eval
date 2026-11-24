"""Offline disclosure exchange: no external calls, no implied clinical validity."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_questionnaire as q


class QuestionnaireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "buyer"
        buyer.init(self.root, "Example", "CDS", "ED", "SE")
        buyer.add_vendor(self.root, "a", "Vendor A")
        buyer.add_vendor(self.root, "b", "Competitor Confidential")

    def response(self):
        doc = q.template(self.root, "a")
        doc["submission"]["respondent"] = "Vendor representative"
        doc["submission"]["product_version"] = "1.2"
        for a in doc["submission"]["answers"]:
            a.update(status="not_available", answer="No evidence provided")
        doc["submission"]["answers"][0].update(status="provided", answer="Product 1.2",
                                                references=["vendor-file.pdf p. 2"])
        return doc

    @staticmethod
    def encode(doc):
        return json.dumps(doc).encode("utf-8")

    def test_template_excludes_competitors_paths_and_clinical_data(self):
        template = q.template(self.root, "a")
        raw = self.encode(template).decode()
        self.assertNotIn("Competitor Confidential", raw)
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn("threshold", raw)
        self.assertEqual(template, q.template(self.root, "a"))

    def test_roundtrip_does_not_satisfy_independent_evidence(self):
        doc = self.response()
        raw = self.encode(doc)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            result = q.import_response(self.root, "a", raw)
        self.assertFalse(result["decision_grade"])
        self.assertEqual(result["claim_authority"], "vendor_self_reported_unverified")
        self.assertEqual((self.root / "evidence" / result["sha256"]).read_bytes(), raw)
        vendor = buyer.status(self.root)["vendors"][0]
        self.assertEqual(set(vendor["missing_evidence_kinds"]), set(buyer.EVIDENCE_KINDS))
        self.assertEqual(vendor["questionnaires"][0]["schema_status"], "valid")
        self.assertEqual(vendor["questionnaires"][0]["counts"]["provided"], 1)
        self.assertIn("self-reported, unverified", buyer.render_dossier(self.root))

    def test_rejects_wrong_vendor_edited_scope_or_question(self):
        doc = self.response()
        with self.assertRaises(buyer.BuyerError):
            q.import_response(self.root, "b", self.encode(doc))
        for key in ("buyer_scope", "questions"):
            modified = copy.deepcopy(doc)
            if key == "buyer_scope":
                modified["request"][key]["setting"] = "ICU"
            else:
                modified["request"][key][0]["prompt"] = "Changed question"
            with self.assertRaises(buyer.BuyerError):
                q.import_response(self.root, "a", self.encode(modified))
        self.assertEqual(buyer.load(self.root)["evidence"], [])

    def test_rejects_missing_duplicate_unknown_and_blank_answers(self):
        for mode in ("missing", "duplicate", "unknown", "blank"):
            doc = self.response()
            answers = doc["submission"]["answers"]
            if mode == "missing":
                answers.pop()
            elif mode == "duplicate":
                answers[-1]["id"] = answers[0]["id"]
            elif mode == "unknown":
                answers[-1]["id"] = "unknown"
            else:
                answers[0]["answer"] = ""
            with self.assertRaises(buyer.BuyerError):
                q.validate(self.root, "a", self.encode(doc))
        self.assertEqual(buyer.load(self.root)["evidence"], [])

    def test_rejects_duplicate_json_keys_and_oversize(self):
        with self.assertRaises(buyer.BuyerError):
            q.validate(self.root, "a", b'{"request": {}, "request": {}}')
        with self.assertRaises(buyer.BuyerError):
            q.validate(self.root, "a", b"x" * (q.MAX_BYTES + 1))

    def test_generic_upload_cannot_claim_questionnaire_import(self):
        p = self.root / "sample.json"
        p.write_text("{}")
        with self.assertRaises(buyer.BuyerError):
            buyer.add_evidence(self.root, "a", q.KIND, str(p))

    def test_tampered_document_not_parsed_as_valid(self):
        result = q.import_response(self.root, "a", self.encode(self.response()))
        (self.root / "evidence" / result["sha256"]).write_text("{}")
        item = buyer.status(self.root)["vendors"][0]["questionnaires"][0]
        self.assertEqual(item["integrity"], "changed")
        self.assertNotEqual(item.get("schema_status"), "valid")


if __name__ == "__main__":
    unittest.main()
