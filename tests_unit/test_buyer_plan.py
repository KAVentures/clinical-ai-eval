"""Browser planning must remain a faithful offline preview of scientific rules."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_plan as planning, intake, selection


class BuyerPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "buyer"
        buyer.init(self.root, "Hospital evaluation", "CDS", "ED", "SE")
        buyer.add_vendor(self.root, "candidate", "Example vendor")
        self.spec = {
            "vendor_id": "candidate", "target_name": "Clinical assistant",
            "target_version": "1.2", "profiles": ["clinician_decision_support"],
            "answers": {q: "Explicit synthetic intake answer for " + q
                        for q in intake.INTAKE_QUESTIONS},
        }

    def test_exact_selection_parity_and_draft_authority(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            record = planning.preview(self.root, self.spec)
        self.assertEqual(record["plan"]["selection"],
                         selection.select_suites(self.spec["profiles"]))
        self.assertEqual(record["plan"]["intake"], self.spec["answers"])
        self.assertFalse(record["decision_grade"])
        self.assertFalse(record["execution_authorized"])
        self.assertEqual(record["network_calls"], 0)
        self.assertTrue(planning.load(self.root, record["plan_id"])["definitions_current"])
        self.assertEqual(record, planning.preview(self.root, self.spec))

    def test_missing_answers_fail_closed(self):
        for value in ("", "TODO", None, True):
            spec = {**self.spec, "answers": dict(self.spec["answers"])}
            spec["answers"]["plausible_harm"] = value
            with self.assertRaises(buyer.BuyerError):
                planning.preview(self.root, spec)
        self.assertFalse((self.root / "plans").exists())

    def test_unknown_vendor_and_profiles_refused(self):
        for fields in ({"vendor_id": "other"}, {"profiles": ["unknown"]},
                       {"profiles": []},
                       {"profiles": ["clinician_decision_support"] * 2}):
            with self.assertRaises(buyer.BuyerError):
                planning.preview(self.root, {**self.spec, **fields})

    def test_tampered_saved_snapshot_is_invalid(self):
        record = planning.preview(self.root, self.spec)
        path = self.root / "plans" / (record["plan_id"] + ".json")
        obj = json.loads(path.read_text())
        obj["execution_authorized"] = True
        path.write_text(json.dumps(obj))
        with self.assertRaises(buyer.BuyerError):
            planning.load(self.root, record["plan_id"])
        with self.assertRaises(buyer.BuyerError):
            planning.preview(self.root, self.spec)

    def test_definition_drift_is_visible(self):
        record = planning.preview(self.root, self.spec)
        with patch.object(planning, "_rules_hash", return_value="new-rules"):
            loaded = planning.load(self.root, record["plan_id"])
        self.assertFalse(loaded["definitions_current"])
        self.assertFalse(loaded["execution_authorized"])

    def test_plan_id_cannot_escape_workspace(self):
        with self.assertRaises(buyer.BuyerError):
            planning.load(self.root, "../buyer.json")

    def test_selection_keeps_blocked_families(self):
        spec = {**self.spec, "profiles": ["clinician_rag"]}
        record = planning.preview(self.root, spec)
        selected = record["plan"]["selection"]
        self.assertEqual(selected, selection.select_suites(["clinician_rag"]))
        self.assertTrue(selected["required_but_not_run"])
        self.assertFalse(record["decision_grade"])


if __name__ == "__main__":
    unittest.main()
