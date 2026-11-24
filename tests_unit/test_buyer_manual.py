"""Manual candidate exchange: exact canonical prompts, fail-closed coverage."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_manual as manual, buyer_plan, intake, pipeline, selection
from targets import demo_target


class ManualExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "buyer"
        buyer.init(self.root, "Test", "Decision support", "ED", "SE")
        buyer.add_vendor(self.root, "vendor", "Candidate")
        self.plan = buyer_plan.preview(self.root, {
            "vendor_id": "vendor", "target_name": "Example product", "target_version": "1.2",
            "profiles": ["clinician_decision_support"],
            "answers": {q: "Synthetic answer " + q for q in intake.INTAKE_QUESTIONS},
        })
        self.pack = self.root / "approved-packs" / "fixture"
        self.pack.mkdir(parents=True)
        self.cases = demo_target.base_cases()
        (self.pack / "cases.json").write_text(json.dumps(self.cases), encoding="utf-8")

    def prepare(self):
        return manual.prepare(self.root, self.plan["plan_id"], "fixture")

    def fill(self, template):
        template["submission"]["operator"] = "Synthetic fixture operator"
        template["submission"]["tested_product_version"] = "1.2"
        for row in template["submission"]["answers"]:
            row.update(status="provided", answer="Synthetic answer")
        return template

    def submit(self, doc):
        return manual.import_response(self.root, json.dumps(doc).encode("utf-8"))

    def test_canonical_prompt_and_response_parity(self):
        self.assertEqual(manual.packs(self.root), ["fixture"])
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            template = self.prepare()
            expected = {}
            def capture(row):
                expected[row["cell_id"]] = row["input_text"]
                return "Synthetic answer"
            for family in self.plan["plan"]["selection"]["runnable_suites"]:
                pipeline.generate_responses(capture, pipeline.load_family(family), self.cases)
            prompts = template["request"]["prompts"]
            self.assertEqual(sorted(p["prompt"] for p in prompts), sorted(expected.values()))
            self.assertTrue(all(set(p) == {"slot", "prompt"} for p in prompts))
            self.assertTrue(all(len(p["slot"]) == 64 for p in prompts))
            self.assertEqual(template, self.prepare())
            result = self.submit(self.fill(template))
            self.assertEqual(result["status"], "complete_unscored")
            self.assertFalse(result["decision_grade"])
            self.assertEqual(manual.responses(self.root, result["submission_id"]),
                             {cell: "Synthetic answer" for cell in expected})
            self.assertEqual(result, manual.submission_status(self.root, result["submission_id"]))

    def test_incomplete_response_record_cannot_enter_engine(self):
        doc = self.fill(self.prepare())
        doc["submission"]["answers"][0].update(status="not_available", answer="")
        result = self.submit(doc)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["n_expected"] - result["n_provided"], 1)
        with self.assertRaisesRegex(buyer.BuyerError, "incomplete"):
            manual.responses(self.root, result["submission_id"])

    def test_modified_prompt_or_wrong_version_rejected(self):
        doc = self.fill(self.prepare())
        doc["request"]["prompts"][0]["prompt"] += " modified"
        with self.assertRaises(buyer.BuyerError):
            self.submit(doc)
        doc = self.fill(self.prepare())
        doc["submission"]["tested_product_version"] = "2.0"
        with self.assertRaises(buyer.BuyerError):
            self.submit(doc)

    def test_duplicate_missing_and_blank_answers_rejected(self):
        for kind in ("duplicate", "missing", "blank"):
            doc = self.fill(self.prepare())
            rows = doc["submission"]["answers"]
            if kind == "duplicate":
                rows[-1]["slot"] = rows[0]["slot"]
            elif kind == "missing":
                rows.pop()
            else:
                rows[0]["answer"] = " "
            with self.assertRaises(buyer.BuyerError):
                self.submit(doc)

    def test_pack_drift_and_snapshot_tampering_block_handoff(self):
        result = self.submit(self.fill(self.prepare()))
        self.cases[0]["input_text"] += " Changed fixture."
        (self.pack / "cases.json").write_text(json.dumps(self.cases), encoding="utf-8")
        with self.assertRaises(buyer.BuyerError):
            manual.responses(self.root, result["submission_id"])

    def test_no_arbitrary_paths_or_other_executors(self):
        with self.assertRaises(buyer.BuyerError):
            manual.prepare(self.root, self.plan["plan_id"], "../fixture")
        other = buyer_plan.preview(self.root, {
            "vendor_id": "vendor", "target_name": "Example product", "target_version": "1.2",
            "profiles": ["patient_triage_chatbot"],
            "answers": {q: "Synthetic answer " + q for q in intake.INTAKE_QUESTIONS},
        })
        with self.assertRaises(buyer.BuyerError):
            manual.prepare(self.root, other["plan_id"], "fixture")

    def test_rehashed_mapping_cannot_disagree_with_original_answers(self):
        result = self.submit(self.fill(self.prepare()))
        path = self.root / "manual-submissions" / (result["submission_id"] + ".json")
        record = json.loads(path.read_text())
        record["mapping"][next(iter(record["mapping"]))] = "Different answer"
        ident = buyer_plan._digest({k:v for k,v in record.items() if k!="submission_id"})
        record["submission_id"] = ident
        (path.parent / (ident + ".json")).write_text(json.dumps(record))
        with self.assertRaises(buyer.BuyerError):
            manual.responses(self.root, ident)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(buyer.BuyerError):
            manual.import_response(self.root, b'{"request":{},"request":{}}')


if __name__ == "__main__":
    unittest.main()
