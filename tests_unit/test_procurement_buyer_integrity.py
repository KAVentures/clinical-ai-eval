"""Adversarial buyer-workflow tests: verification must precede comparison."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import claim, cli, manifest, procurement, procurement_workflow as pw
from caeval.util import stable_hash_text


class BuyerIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.buyer = self.root / "buyer"
        self.hazards = [{
            "hazard_id": "missed_defect",
            "family": "missing_information",
            "acceptance_criterion": {
                "metric": "unsafe_overconfident", "operator": "<=", "threshold": 0.2,
            },
        }]
        self.state = pw.init(
            self.buyer, "example", ["missing_information"],
            {"pack_id": "frozen-pack", "content_hash": "pack-content-sha"},
            "judge_panel.toml", self.hazards)
        pw.add_vendor(self.buyer, "vendor1", {"kind": "mock"}, "Product A")

    def test_editing_frozen_threshold_blocks_entire_buyer_workflow(self):
        p = self.buyer / "procurement.json"
        state = json.loads(p.read_text())
        state["conditions"]["hazards"][0]["acceptance_criterion"]["threshold"] = 0.99
        p.write_text(json.dumps(state))
        with self.assertRaisesRegex(pw.ProcurementError, "conditions were edited"):
            pw.load(self.buyer)

    def test_cli_exposes_real_verified_ingestion(self):
        with patch.object(pw, "ingest_package", return_value={
            "results": {"vendor1": {"missing_information": {}}}
        }) as ingest:
            cli.main(["procurement", "ingest-package", str(self.buyer),
                      "--vendor", "vendor1", "--workspace", str(self.root / "run")])
            ingest.assert_called_once_with(str(self.buyer), "vendor1",
                                           str(self.root / "run"))

    def test_package_fields_are_real_hashes_not_unrelated_placeholders(self):
        ws = self.root / "run"
        ws.mkdir()
        authority = claim.compute(
            "procurement_comparison", "L0", "experimental",
            "demonstration_fixture", "mock").as_dict()
        (ws / "analysis.json").write_text(json.dumps({
            "family_id": "missing_information", "claim_authority": authority,
            "case_pack": {"content_hash": "pack-content-sha", "version": "never-the-family-version"},
            "executor": "generic_paired_text",
        }))
        (ws / "run_meta.json").write_text(json.dumps({
            "conditions_hash": self.state["conditions_hash"]
        }))
        (ws / "results.jsonl").write_text(
            json.dumps({"cell_id": "c1", "case_id": "s1", "unsafe_overconfident": 0}) + "\n")
        (ws / "assessment_manifest.json").write_text(json.dumps({
            "family_id": "missing_information",
            "executor": "generic_paired_text",
            "manifest_hash": "verified-digest",
            "definitions": {
                "tests/missing_information/family.yaml": "family-hash",
                "selection_rules.yaml": "rules-hash",
            },
        }))
        judges = [{"name": "test", "provider": "synthetic", "model": "mock", "mock": True}]
        (ws / "provenance.json").write_text(json.dumps({
            "eval_standard": "v1.2", "judge_prompt_sha256": "real-prompt-hash",
            "panel": {"judges": judges}
        }))
        with patch.object(manifest, "verify_manifest", return_value={"verdict": "VALID"}):
            pw.ingest_package(self.buyer, "vendor1", ws)
        recorded = pw.load(self.buyer)["results"]["vendor1"]["missing_information"]
        env = recorded["environment"]
        self.assertEqual(env["family_version"], "sha256:family-hash")
        self.assertEqual(env["judge_prompt_hash"], "real-prompt-hash")
        self.assertEqual(env["selection_rules_hash"], "rules-hash")
        self.assertEqual(env["eval_standard_version"], "v1.2")
        self.assertEqual(env["case_pack_hash"], "pack-content-sha")
        self.assertEqual(env["package_digest"], "verified-digest")
        self.assertEqual(env["judge_panel_hash"],
                         stable_hash_text(json.dumps(judges, sort_keys=True)))
        self.assertEqual(recorded["ingestion"], "verified_package")
        with patch.object(manifest, "verify_manifest", return_value={"verdict": "VALID"}):
            with self.assertRaisesRegex(pw.ProcurementError, "already has a verified run"):
                pw.ingest_package(self.buyer, "vendor1", ws)
        with self.assertRaisesRegex(pw.ProcurementError, "already has a verified run"):
            pw.record_result(
                self.buyer, "vendor1", "missing_information", [],
                {"conditions_hash": self.state["conditions_hash"]})
        result = pw.compare(self.buyer, "missing_information")
        self.assertEqual(result["comparability"]["status"], procurement.COMPARABLE)
        self.assertEqual(result["unverified_input_products"], [])

    def test_out_of_scope_family_cannot_be_added_or_compared(self):
        with self.assertRaisesRegex(pw.ProcurementError, "outside the frozen procurement"):
            pw.record_result(
                self.buyer, "vendor1", "patient_red_flag", [],
                {"conditions_hash": self.state["conditions_hash"]})
        with self.assertRaisesRegex(pw.ProcurementError, "not in the frozen scope"):
            pw.compare(self.buyer, "patient_red_flag")

    def test_changed_casepack_refused_even_under_matching_procurement_conditions(self):
        with self.assertRaisesRegex(pw.ProcurementError, "Refusing a changed case set"):
            pw.record_result(self.buyer, "vendor1", "missing_information", [],
                {"conditions_hash": self.state["conditions_hash"],
                 "case_pack_hash": "other-casepack"})

    def test_unverified_caller_values_cannot_pass_as_buyer_comparison(self):
        pw.record_result(self.buyer, "vendor1", "missing_information", [
            {"cell_id": "c1", "case_id": "s1", "unsafe_overconfident": 0}
        ], {
            "conditions_hash": self.state["conditions_hash"],
            **{field: ("pack-content-sha" if field == "case_pack_hash"
                       else "self-asserted")
               for field in procurement.COMPARABILITY_FIELDS
               if field != "conditions_hash"},
        })
        result = pw.compare(self.buyer, "missing_information")
        self.assertEqual(result["comparability"]["status"], procurement.INCOMPARABLE)
        self.assertEqual(result["unverified_input_products"], ["Product A"])
        self.assertFalse(result["claim_authority"]["decision_grade"])


if __name__ == "__main__":
    unittest.main()
