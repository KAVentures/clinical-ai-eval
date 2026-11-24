"""Research browser uses canonical verification and never exposes raw responses."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from caeval import buyer_agent as buyer, buyer_research as research, capabilities, claim, manifest


class ResearchBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "buyer"
        buyer.init(self.root, "Example", "CDS", "ED", "SE")
        self.ws = self.root / "research-runs" / "run_a"
        self.ws.mkdir(parents=True)
        authority = claim.compute("procurement_comparison", "L0", "experimental",
                                  "demonstration_fixture", "mock").as_dict()
        for name, obj in {
            "run_meta.json": {"family_id": "missing_information",
                              "executor": "generic_paired_text"},
            "analysis.json": {"family_id": "missing_information",
                              "claim_authority": authority},
            "provenance.json": {"eval_standard": "test"},
        }.items():
            (self.ws / name).write_text(json.dumps(obj))
        (self.ws / "responses.jsonl").write_text(json.dumps({
            "cell_id": "c1", "input_text": "PRIVATE_SYNTHETIC_MARKER"
        }) + "\n")
        (self.ws / "results.jsonl").write_text("{}\n")
        (self.ws / "final_report.md").write_text("test")
        (self.ws / "limitations.md").write_text("experimental")
        self.man = manifest.build_manifest(self.ws)

    def test_real_manifest_and_registry_parity(self):
        before = (self.ws / manifest.MANIFEST_FILE).read_bytes()
        data = research.overview(self.root)
        self.assertEqual(data["capabilities"], capabilities.table())
        self.assertEqual(data["runs"][0]["verdict"], "VALID")
        self.assertFalse(data["runs"][0]["decision_grade"])
        self.assertEqual(data["runs"][0]["manifest_hash"], self.man["manifest_hash"])
        self.assertNotIn("PRIVATE_SYNTHETIC_MARKER", json.dumps(data))
        self.assertNotIn(str(self.root), json.dumps(data))
        self.assertEqual((self.ws / manifest.MANIFEST_FILE).read_bytes(), before)

    def test_verified_read_preserves_canonical_manifest_hash_contract(self):
        metadata = research._preflight(self.ws)
        original = json.loads((self.ws / "analysis.json").read_text())
        (self.ws / "analysis.json").write_text(json.dumps(original, indent=4, sort_keys=True))
        self.assertEqual(json.loads(research.verified_bytes(self.ws, metadata, "analysis")), original)
        self.assertEqual(research.inspect_run(self.root, "run_a")["verdict"], "VALID")
        original["family_id"] = "conflicting_evidence"
        (self.ws / "analysis.json").write_text(json.dumps(original))
        with self.assertRaises(buyer.BuyerError):
            research.verified_bytes(self.ws, metadata, "analysis")

    def test_tampered_run_invalid(self):
        (self.ws / "responses.jsonl").write_text("{}\n")
        self.assertEqual(research.inspect_run(self.root, "run_a")["verdict"], "INVALID")

    def test_self_rehashed_manifest_cannot_redirect_reads(self):
        self.man["artifacts"]["responses"]["path"] = "../../buyer.json"
        self.man["manifest_hash"] = manifest.fingerprint(self.man)
        (self.ws / manifest.MANIFEST_FILE).write_text(json.dumps(self.man))
        with patch.object(manifest, "verify_manifest", side_effect=AssertionError("unsafe read")):
            self.assertEqual(research.inspect_run(self.root, "run_a")["verdict"], "INVALID")

    def test_missing_core_artifact_entry_invalid(self):
        del self.man["artifacts"]["responses"]
        self.man["manifest_hash"] = manifest.fingerprint(self.man)
        (self.ws / manifest.MANIFEST_FILE).write_text(json.dumps(self.man))
        self.assertEqual(research.inspect_run(self.root, "run_a")["verdict"], "INVALID")

    def test_unknown_definition_path_rejected_before_verification(self):
        self.man["definitions"]["../../buyer.json"] = "arbitrary"
        self.man["manifest_hash"] = manifest.fingerprint(self.man)
        (self.ws / manifest.MANIFEST_FILE).write_text(json.dumps(self.man))
        with patch.object(manifest, "verify_manifest", side_effect=AssertionError("unsafe read")):
            self.assertEqual(research.inspect_run(self.root, "run_a")["verdict"], "INVALID")

    def test_removed_previously_present_optional_artifact_invalid(self):
        (self.ws / "human_review.csv").write_text("cell_id,review\nc1,unsafe\n")
        manifest.build_manifest(self.ws)
        (self.ws / "human_review.csv").unlink()
        self.assertEqual(research.inspect_run(self.root, "run_a")["verdict"], "INVALID")

    def test_symlink_rejected(self):
        try:
            (self.ws / "escape").symlink_to(self.root / "buyer.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.assertEqual(research.inspect_run(self.root, "run_a")["verdict"], "INVALID")

    def test_run_id_cannot_escape(self):
        with self.assertRaises(buyer.BuyerError):
            research.inspect_run(self.root, "../buyer")


if __name__ == "__main__":
    unittest.main()
