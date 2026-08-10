"""L2 adjudication for the patient and RAG backends, and verified procurement
ingestion.

Before v0.19 neither existed: L2 was structurally unreachable for those executors
(declared as a ceiling), and procurement accepted raw cells that could be edited
between the run and the comparison.
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from caeval import lifecycle, manifest as manifest_mod, unit_review  # noqa: E402
from caeval.cli import _run_project_bound, cmd_adjudicate  # noqa: E402
from test_integration_e2e import _Args, _project  # noqa: E402


def _patient_run(tmp, arm="mock_defective", conditions_hash=""):
    import yaml
    d = _project(tmp, "patient_triage_chatbot", "builtin:public_smoke",
                 {"kind": "mock", "arm": arm, "modality": "conversation"})
    if conditions_hash:
        f = d / "project.yaml"
        data = yaml.safe_load(f.read_text())
        data["procurement"] = {"conditions_hash": conditions_hash}
        f.write_text(yaml.safe_dump(data, sort_keys=False))
    ws = Path(tmp) / "ws"
    _run_project_bound(_Args(d, ws))
    return ws / "run_patient_red_flag"


def _reviews(run, names, verdict="unsafe", disagree_on=()):
    mf = json.loads((run / "review_manifest.json").read_text())
    files = []
    for i, name in enumerate(names):
        f = run / f"{name}.csv"
        with open(f, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["unit_id", "reviewer_verdict", "reviewer_notes"])
            for u in mf["expected_units"]:
                v = verdict
                if u["unit_id"] in disagree_on and i == 1:
                    v = "safe" if verdict == "unsafe" else "unsafe"
                w.writerow([u["unit_id"], v, ""])
        files.append(str(f))
    return files, mf


class _A:
    def __init__(self, ws, reviews=None, mock=False):
        self.workspace, self.reviews, self.mock, self.reviewers = str(ws), reviews, mock, 2


class TestReviewQueueIsLockedBeforeReviewsExist(unittest.TestCase):
    def test_manifest_is_written_at_run_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            mf = unit_review.load_review_manifest(run)
            self.assertIsNotNone(mf)
            self.assertTrue(mf["expected_units"])
            self.assertEqual(mf["min_reviewers_per_unit"], 2)

    def test_editing_the_locked_manifest_is_detected(self):
        """A 'locked' file nobody re-hashes is not locked."""
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            mf = json.loads((run / "review_manifest.json").read_text())
            mf["min_reviewers_per_unit"] = 1          # the classic loosening
            (run / "review_manifest.json").write_text(json.dumps(mf))
            with self.assertRaises(unit_review.ReviewIntegrityError):
                unit_review.verify_review_manifest(
                    unit_review.load_review_manifest(run))

    def test_mandatory_units_are_fixed_at_lock_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            mf = unit_review.load_review_manifest(run)
            self.assertTrue(any(u["mandatory"] for u in mf["expected_units"]),
                            "a defective target must produce mandatory review units")


class TestGateArithmetic(unittest.TestCase):
    def test_a_tie_is_contested_never_safe(self):
        self.assertEqual(unit_review.resolve(["safe", "unsafe"]), "contested")

    def test_one_reviewer_is_insufficient(self):
        self.assertEqual(unit_review.resolve(["safe"]), "insufficient")

    def test_majority_resolves(self):
        self.assertEqual(unit_review.resolve(["unsafe", "unsafe", "safe"]), "unsafe")
        self.assertEqual(unit_review.resolve(["safe", "safe", "unsafe"]), "safe")

    def test_unrecognised_verdict_raises_rather_than_defaulting_to_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "r.csv"
            f.write_text("unit_id,reviewer_verdict\nu1,probably fine\n")
            with self.assertRaises(unit_review.ReviewIntegrityError):
                unit_review.load_reviews([str(f)])


class TestL2GateOnAPatientRun(unittest.TestCase):
    def test_synthetic_reviews_can_never_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            rep = cmd_adjudicate(_A(run, mock=True))
            self.assertFalse(rep["l2_adjudication_gate_passed"])
            self.assertTrue(any("SYNTHETIC" in p for p in rep["integrity_problems"]))

    def test_two_agreeing_reviewers_pass_the_gate(self):
        """The gate must be able to PASS, or it is not evidence of anything."""
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            files, _ = _reviews(run, ["dr_a", "dr_b"])
            rep = cmd_adjudicate(_A(run, reviews=files))
            self.assertTrue(rep["l2_adjudication_gate_passed"], rep["integrity_problems"])
            self.assertEqual(rep["gate_outcome"], "PASSED")

    def test_one_reviewer_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            files, _ = _reviews(run, ["dr_a"])
            rep = cmd_adjudicate(_A(run, reviews=files))
            self.assertFalse(rep["l2_adjudication_gate_passed"])

    def test_contested_mandatory_unit_blocks_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            mf = unit_review.load_review_manifest(run)
            mandatory = next(u["unit_id"] for u in mf["expected_units"] if u["mandatory"])
            files, _ = _reviews(run, ["dr_a", "dr_b"], disagree_on={mandatory})
            rep = cmd_adjudicate(_A(run, reviews=files))
            self.assertFalse(rep["l2_adjudication_gate_passed"])
            self.assertIn(mandatory, rep["unresolved_mandatory_units"])

    def test_adjudication_does_not_report_a_conformance_level(self):
        """Two sources of truth for conformance is the defect that let an all-mock
        panel buy L1. The gate reports a GATE; the lifecycle derives the level."""
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            files, _ = _reviews(run, ["dr_a", "dr_b"])
            rep = cmd_adjudicate(_A(run, reviews=files))
            self.assertNotIn("level", rep)
            self.assertEqual(rep["confers"], "l2_if_the_run_is_already_L1")

    def test_passing_the_gate_does_not_upgrade_a_run_that_is_not_L1(self):
        """Human review cannot substitute for a conformant judge panel."""
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            files, _ = _reviews(run, ["dr_a", "dr_b"])
            cmd_adjudicate(_A(run, reviews=files))
            a = json.loads((run / "analysis.json").read_text())
            self.assertEqual(a["conformance_level"], "L0")   # mock panel
            self.assertEqual(a["claim_authority"]["permitted_claims"], [])

    def test_L2_is_reachable_when_the_run_is_L1_and_the_gate_passes(self):
        """The ceiling is removed: with two distinct REAL blinded providers and a
        passed gate, the shared lifecycle yields L2."""
        real = {"judges": [{"name": "a", "provider": "p1", "mock": False},
                           {"name": "b", "provider": "p2", "mock": False}]}
        cells = [{"panel_scored": True, "panel_labels": {"a": {}, "b": {}}}]
        self.assertEqual(lifecycle.conformance_from(real, cells, False), "L1")
        self.assertEqual(lifecycle.conformance_from(real, cells, True), "L2")

    def test_package_stays_verifiable_after_adjudication(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            files, _ = _reviews(run, ["dr_a", "dr_b"])
            cmd_adjudicate(_A(run, reviews=files))
            self.assertEqual(manifest_mod.verify_manifest(run)["verdict"], "VALID")


class TestProcurementIngestsVerifiedPackagesOnly(unittest.TestCase):
    HAZ = [{"hazard_id": "H", "description": "under-triage", "severity": "high",
            "tests": ["ALL"],
            "acceptance_criterion": {"metric": "under_triage", "operator": "==",
                                     "threshold": 0.0}}]

    def _setup(self, tmp):
        from caeval import procurement_workflow as pw
        proc = Path(tmp) / "proc"
        proc.mkdir()
        pw.init(proc, "p", ["patient_red_flag"], {"pack_id": "x"}, "cfg", self.HAZ)
        pw.add_vendor(proc, "v1", {"kind": "mock", "api_key": "sk-SECRET"})
        return pw, proc

    def test_a_run_from_other_conditions_is_refused(self):
        """A vendor run produced under different frozen conditions cannot be
        compared with one produced under these."""
        with tempfile.TemporaryDirectory() as tmp:
            pw, proc = self._setup(tmp)
            run = _patient_run(tmp, conditions_hash="someone_elses_procurement")
            with self.assertRaises(pw.ProcurementError):
                pw.ingest_package(proc, "v1", run)

    def test_a_run_declaring_no_conditions_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pw, proc = self._setup(tmp)
            run = _patient_run(tmp)
            with self.assertRaises(pw.ProcurementError):
                pw.ingest_package(proc, "v1", run)

    def test_a_modified_package_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pw, proc = self._setup(tmp)
            run = _patient_run(tmp)
            rows = (run / "results.jsonl").read_text().splitlines()
            rows[0] = rows[0].replace('"under_triage": 1', '"under_triage": 0')
            (run / "results.jsonl").write_text("\n".join(rows))
            with self.assertRaises(pw.ProcurementError) as ctx:
                pw.ingest_package(proc, "v1", run)
            self.assertIn("INVALID", str(ctx.exception))

    def test_a_forged_claim_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pw, proc = self._setup(tmp)
            run = _patient_run(tmp)
            a = json.loads((run / "analysis.json").read_text())
            a["claim_authority"]["effective_claim"] = "procurement_comparison"
            (run / "analysis.json").write_text(json.dumps(a, indent=2))
            manifest_mod.build_manifest(run)      # re-seal so it verifies VALID
            with self.assertRaises(pw.ProcurementError) as ctx:
                pw.ingest_package(proc, "v1", run)
            self.assertIn("axes imply", str(ctx.exception))

    def test_a_clean_package_is_ingested_with_its_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            pw, proc = self._setup(tmp)
            ch = pw.load(proc)["conditions_hash"]
            run = _patient_run(tmp, conditions_hash=ch)
            state = pw.ingest_package(proc, "v1", run)
            rec = state["results"]["v1"]["patient_red_flag"]
            self.assertEqual(rec["ingestion"], "verified_package")
            self.assertTrue(rec["package"]["package_digest"])
            self.assertEqual(rec["package"]["verify_verdict"], "VALID")
            self.assertTrue(rec["cells"])

    def test_credentials_never_enter_procurement_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            pw, proc = self._setup(tmp)
            ch = pw.load(proc)["conditions_hash"]
            run = _patient_run(tmp, conditions_hash=ch)
            pw.ingest_package(proc, "v1", run)
            self.assertNotIn("sk-SECRET", json.dumps(pw.load(proc)))


if __name__ == "__main__":
    unittest.main()
