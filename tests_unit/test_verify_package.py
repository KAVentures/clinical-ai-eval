"""Independent verification of an evidence package.

A procurement team must be able to verify a package WITHOUT trusting the vendor or
the machine that produced it. Prior binding hashed pointers (case ids, cell ids), so
hidden manifests, expected behaviour and raw responses could change undetected.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from caeval import manifest as man
from caeval import pipeline, report
from caeval.subject import build_subject
from caeval.workspace import Workspace
from targets import demo_target

PANEL = {"min_distinct_providers": 2, "judges": [
    {"name": "mock_strict", "provider": "mock_a", "model": "s", "mock": True},
    {"name": "mock_lenient", "provider": "mock_b", "model": "l", "mock": True}]}


def _package():
    d = Path(tempfile.mkdtemp())
    fam = pipeline.load_family("missing_information")
    cases = demo_target.base_cases()
    responses = pipeline.generate_responses(build_subject({"kind": "mock", "arm": "flawed"}), fam, cases)
    ws = Workspace(d).ensure()
    ws.write_responses(responses)
    ws.write_run_meta({"family_id": "missing_information",
                       "subject_spec": {"kind": "mock", "arm": "flawed"},
                       "panel": {"names": ["mock_strict", "mock_lenient"],
                                 "conformance_level": "L0", "all_mock": True}})
    scored = pipeline.score_responses(responses, PANEL)
    result = pipeline.analyze(scored, responses, fam, {"kind": "mock", "arm": "flawed"}, PANEL)
    (ws.path / "analysis.json").write_text(json.dumps(result))
    report.build_evidence_package(result, fam, str(ws.path))
    return ws.path


class TestCleanPackageVerifies(unittest.TestCase):
    def test_untouched_package_is_valid(self):
        res = man.verify_manifest(_package())
        self.assertEqual(res["verdict"], man.VALID)
        self.assertEqual(res["n_failed"], 0)
        self.assertEqual(res["n_missing"], 0)

    def test_manifest_records_every_required_artifact(self):
        m = json.loads((_package() / man.MANIFEST_FILE).read_text())
        for key, (rel, required) in man.ARTIFACTS.items():
            if required:
                self.assertTrue(m["artifacts"][key]["present"], f"{rel} not recorded")

    def test_manifest_binds_hidden_content_not_just_facing_text(self):
        """The prior case hash covered only item_id + input_text."""
        m = json.loads((_package() / man.MANIFEST_FILE).read_text())
        cc = m["case_content"]
        self.assertIn("facing_input_hash", cc)
        self.assertIn("hidden_manifest_hash", cc)
        self.assertNotEqual(cc["facing_input_hash"], cc["hidden_manifest_hash"])


class TestTamperingIsDetected(unittest.TestCase):
    def test_edited_raw_response_is_detected(self):
        ws = _package()
        p = ws / "results.jsonl"
        rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        rows[0]["response_text"] = "Actually a very safe answer."
        p.write_text("\n".join(json.dumps(r) for r in rows))
        res = man.verify_manifest(ws)
        self.assertEqual(res["verdict"], man.INVALID)
        self.assertIn("results.jsonl", res["tampered"])

    def test_edited_report_is_detected(self):
        ws = _package()
        p = ws / "final_report.md"
        p.write_text(p.read_text().replace("NON_CONFORMANT", "FULLY CONFORMANT"))
        self.assertEqual(man.verify_manifest(ws)["verdict"], man.INVALID)

    def test_edited_scores_are_detected(self):
        ws = _package()
        p = ws / "analysis.json"
        a = json.loads(p.read_text())
        a["n_variants_auto_screened"] = 0
        p.write_text(json.dumps(a))
        self.assertEqual(man.verify_manifest(ws)["verdict"], man.INVALID)

    def test_missing_required_artifact_is_INCOMPLETE_not_VALID(self):
        ws = _package()
        (ws / "provenance.json").unlink()
        res = man.verify_manifest(ws)
        self.assertEqual(res["verdict"], man.INCOMPLETE)
        self.assertIn("provenance.json", res["missing"])

    def test_forging_the_manifest_to_match_an_edit_is_detected(self):
        """The hardest case: edit an artifact AND re-point its recorded hash."""
        ws = _package()
        p = ws / "final_report.md"
        p.write_text(p.read_text() + "\n\nEXTRA CLAIM\n")
        mp = ws / man.MANIFEST_FILE
        d = json.loads(mp.read_text())
        d["artifacts"]["final_report"]["sha256"] = man.hash_file(p)
        mp.write_text(json.dumps(d, indent=2))
        res = man.verify_manifest(ws)
        self.assertEqual(res["verdict"], man.INVALID)
        self.assertIn(man.MANIFEST_FILE, res["tampered"])

    def test_reformatting_json_does_not_false_positive(self):
        """Canonical hashing: pretty-printing must not look like tampering."""
        ws = _package()
        p = ws / "analysis.json"
        p.write_text(json.dumps(json.loads(p.read_text()), indent=4))
        self.assertEqual(man.verify_manifest(ws)["verdict"], man.VALID)

    def test_adding_an_artifact_that_was_absent_is_detected(self):
        ws = _package()
        (ws / "adjudication").mkdir(exist_ok=True)
        (ws / "adjudication" / "adjudication_report.json").write_text(
            json.dumps({"level": "L2", "summary_md": "forged"}))
        res = man.verify_manifest(ws)
        self.assertEqual(res["verdict"], man.INVALID)


class TestDefinitionDrift(unittest.TestCase):
    def test_definitions_the_run_depended_on_are_recorded(self):
        m = json.loads((_package() / man.MANIFEST_FILE).read_text())
        defs = m["definitions"]
        self.assertIn("selection_rules.yaml", defs)
        self.assertIn("prompts/judge_prompt.txt", defs)
        self.assertTrue(any("family.yaml" in k for k in defs))


if __name__ == "__main__":
    unittest.main()
