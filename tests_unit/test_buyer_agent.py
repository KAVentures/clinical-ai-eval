"""Local-first buyer workflow regression tests: no remote APIs or patient data."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer
from caeval import procurement_workflow as pw
from caeval.cli import main


class BuyerWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        buyer.init(self.root, "Hospital trial", "patient triage",
                   "outpatient", "SE")

    def test_local_create_and_integrity_check(self):
        buyer.add_vendor(self.root, "supplier_a", "Supplier A")
        source = Path(self.tmp.name) / "clinical-validation.txt"
        source.write_text("synthetic sample; no patient information", encoding="utf-8")
        record = buyer.add_evidence(self.root, "supplier_a", "clinical_validation", str(source))
        self.assertEqual(record["integrity"], "intact")
        self.assertTrue((self.root / "evidence" / record["sha256"]).exists())
        state = buyer.status(self.root)
        self.assertEqual(state["network_calls"], 0)
        self.assertEqual(state["vendors"][0]["documents"][0]["integrity"], "intact")
        self.assertEqual(state["vendors"][0]["documents"][0]["review_status"], "not_reviewed")
        self.assertIn("regulatory_status", state["vendors"][0]["missing_evidence_kinds"])
        self.assertFalse(state["decision_grade"])
        raw = (self.root / "buyer.json").read_text()
        self.assertNotIn(str(source), raw, "Never leak personal source directory in exported state")

    def test_tamper_detection(self):
        buyer.add_vendor(self.root, "a", "Vendor A")
        doc = Path(self.tmp.name) / "test.txt"
        doc.write_text("initial", encoding="utf-8")
        rec = buyer.add_evidence(self.root, "a", "security", str(doc))
        (self.root / "evidence" / rec["sha256"]).write_text("malicious edit")
        self.assertEqual(buyer.status(self.root)["vendors"][0]["documents"][0]["integrity"], "changed")

    def test_missing_document_not_counted(self):
        buyer.add_vendor(self.root, "a", "A")
        doc = Path(self.tmp.name) / "test.txt"
        doc.write_text("anything")
        rec = buyer.add_evidence(self.root, "a", "security", str(doc))
        (self.root / "evidence" / rec["sha256"]).unlink()
        v = buyer.status(self.root)["vendors"][0]
        self.assertIn("security", v["missing_evidence_kinds"])
        self.assertFalse(v["all_documents_intact"])

    def test_guards_against_invalid_ids_and_duplicate_vendors(self):
        with self.assertRaises(buyer.BuyerError):
            buyer.add_vendor(self.root, "../outside", "invalid")
        buyer.add_vendor(self.root, "one", "A")
        with self.assertRaises(buyer.BuyerError):
            buyer.add_vendor(self.root, "one", "B")
        with self.assertRaises(buyer.BuyerError):
            buyer.add_evidence(self.root, "unknown", "security", "/missing")
        with self.assertRaises(buyer.BuyerError):
            buyer.init(self.root, "Must not overwrite")

    def test_symlink_source_rejected(self):
        buyer.add_vendor(self.root, "a", "A")
        doc = Path(self.tmp.name) / "test.txt"
        doc.write_text("synthetic")
        link = Path(self.tmp.name) / "link.txt"
        try:
            link.symlink_to(doc)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable on this platform")
        with self.assertRaises(buyer.BuyerError):
            buyer.add_evidence(self.root, "a", "security", str(link))

    def test_offline_dossier_never_recommends_or_overwrites(self):
        buyer.add_vendor(self.root, "a", "Vendor A")
        outfile = self.root / "register.md"
        result = buyer.dossier(self.root, str(outfile))
        self.assertEqual(result["network_calls"], 0)
        report = outfile.read_text()
        self.assertIn("Administrative inventory only", report)
        self.assertIn("Missing evidence types", report)
        with self.assertRaises(buyer.BuyerError):
            buyer.dossier(self.root, str(outfile))

    def test_detect_existing_frozen_assessment(self):
        assessment = self.root / "assessment"
        pw.init(assessment, "buyer-frozen", ["missing_information"],
                {"pack_id": "synthetic-v1", "content_hash": "fixed-hash"},
                "configs/judge_panel.toml",
                [{"hazard_id": "missing_information_hazard",
                  "acceptance_criterion": {"metric": "unsafe_overconfident",
                                           "operator": "<=", "threshold": 0.1}}])
        a = buyer.status(self.root)["assessment"]
        self.assertEqual(a["status"], "configured")
        self.assertTrue(a["case_pack_hash_locked"])
        self.assertTrue(a["conditions_hash"])

    def test_combined_dossier_excludes_missing_and_unverified_results(self):
        buyer.add_vendor(self.root, "a", "Vendor A")
        buyer.add_vendor(self.root, "b", "Vendor B")
        assessment = self.root / "assessment"
        st = pw.init(assessment, "test", ["missing_information"],
                     {"pack_id": "demo", "content_hash": "demo-hash"},
                     "panel.toml", [{"hazard_id": "h",
                     "acceptance_criterion": {"metric": "unsafe_overconfident",
                                              "operator": "<=", "threshold": 0.1}}])
        pw.add_vendor(assessment, "a", {"kind": "mock"}, "Product A")
        pw.record_result(assessment, "a", "missing_information", [],
                         {"conditions_hash": st["conditions_hash"],
                          "case_pack_hash": "demo-hash"})
        before = (assessment / "procurement.json").read_bytes()
        report = buyer.render_dossier(self.root)
        self.assertIn("a / missing_information: no verified package recorded", report)
        self.assertIn("Candidate b: not registered", report)
        self.assertIn("NOT MEASURED", report)
        self.assertEqual((assessment / "procurement.json").read_bytes(), before)

    def test_combined_dossier_reconstructs_not_cached_cells(self):
        from caeval import claim, manifest
        buyer.add_vendor(self.root, "a", "Vendor A")
        assessment = self.root / "assessment"
        st = pw.init(assessment, "test", ["missing_information"],
                     {"pack_id": "demo", "content_hash": "demo-hash"},
                     "panel.toml", [{"hazard_id": "h",
                     "acceptance_criterion": {"metric": "unsafe_overconfident",
                                              "operator": "<=", "threshold": 0.1}}])
        pw.add_vendor(assessment, "a", {"kind": "mock"}, "Product A")
        ws = self.root / "run"
        ws.mkdir()
        authority = claim.compute("procurement_comparison", "L0", "experimental",
                                  "demonstration_fixture", "mock").as_dict()
        files = {
            "analysis.json": {"family_id": "missing_information",
                              "claim_authority": authority,
                              "case_pack": {"content_hash": "demo-hash"}},
            "run_meta.json": {"conditions_hash": st["conditions_hash"]},
            "assessment_manifest.json": {"family_id": "missing_information",
                    "executor": "generic_paired_text", "manifest_hash": "fixture-digest",
                    "definitions": {"tests/missing_information/family.yaml": "family-hash",
                                    "selection_rules.yaml": "rules-hash"}},
            "provenance.json": {"eval_standard": "fixture", "judge_prompt_sha256": "prompt-hash",
                                "panel": {"judges": [{"name": "fixture"}]}},
        }
        for filename, payload in files.items():
            (ws / filename).write_text(json.dumps(payload))
        (ws / "results.jsonl").write_text(json.dumps({
            "cell_id": "c1", "case_id": "s1", "unsafe_overconfident": 0}) + "\n")
        # This fixture mocks only the manifest verifier, not the reconstruction.
        with patch.object(manifest, "verify_manifest", return_value={"verdict": "VALID"}) as verify:
            pw.ingest_package(assessment, "a", ws)
            first = buyer.render_dossier(self.root)
            self.assertIn("package integrity reverified", first)
            self.assertGreaterEqual(verify.call_count, 2)
            saved = pw.load(assessment)
            saved["results"]["a"]["missing_information"]["cells"][0]["unsafe_overconfident"] = 1
            pw.save(assessment, saved)
            self.assertEqual(first, buyer.render_dossier(self.root))
            files["assessment_manifest.json"]["manifest_hash"] = "replacement"
            (ws / "assessment_manifest.json").write_text(json.dumps(files["assessment_manifest.json"]))
            second = buyer.render_dossier(self.root)
            self.assertIn("package missing, changed or invalid; excluded", second)
            self.assertNotIn("package integrity reverified", second)
            self.assertIn("NOT MEASURED", second)

    def test_agent_cli_json_contract_and_read_only_status(self):
        for argv in (["buyer", "contract"], ["buyer", "status", str(self.root)]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                main(argv)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["network_calls"], 0)
        data = json.loads((self.root / "buyer.json").read_text())
        self.assertEqual(data["project_name"], "Hospital trial")

    def test_agent_contract_declares_required_arguments(self):
        contract = buyer.contract()
        self.assertEqual(contract["transport"], "local_subprocess_json")
        self.assertFalse(contract["action_schemas"]["status"]["writes"])
        self.assertIn("--vendor-id", contract["action_schemas"]["evidence-add"]["required"])

    def test_cli_missing_path_is_structured_failure(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as exc:
            main(["buyer", "status"])
        self.assertEqual(exc.exception.code, 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_no_unintended_network_calls(self):
        buyer.add_vendor(self.root, "a", "A")
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            buyer.status(self.root)
            buyer.dossier(self.root, str(self.root / "offline.md"))


if __name__ == "__main__":
    unittest.main()
