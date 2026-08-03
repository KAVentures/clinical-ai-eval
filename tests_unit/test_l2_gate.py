"""L2 adjudication gate — the false-upgrade paths.

v0.9 derived the review universe from the RETURNED FILES, so an omitted required
cell vanished from the denominator; checked reviewer count GLOBALLY, so a cell with
one label could resolve; and read mock status from the JUDGE PANEL, so a workspace
with real L1 judges could in principle be upgraded using synthetic clinician files.
"""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from caeval import pipeline, report
from caeval.adjudicate import adjudicate, load_reviews, mock_adjudicate
from caeval.subject import build_subject
from caeval.workspace import Workspace
from targets import demo_target

PANEL = {"min_distinct_providers": 2, "judges": [
    {"name": "mock_strict", "provider": "mock_a", "model": "s", "mock": True},
    {"name": "mock_lenient", "provider": "mock_b", "model": "l", "mock": True}]}


def _workspace(real_judges=False):
    d = Path(tempfile.mkdtemp())
    fam = pipeline.load_family("missing_information")
    cases = demo_target.base_cases()
    responses = pipeline.generate_responses(build_subject({"kind": "mock", "arm": "flawed"}), fam, cases)
    ws = Workspace(d).ensure()
    ws.write_responses(responses)
    ws.write_run_meta({"family_id": "missing_information",
                       "subject_spec": {"kind": "mock", "arm": "flawed"},
                       "panel": {"names": ["mock_strict", "mock_lenient"],
                                 "conformance_level": "L1" if real_judges else "L0",
                                 "all_mock": not real_judges}})
    scored = pipeline.score_responses(responses, PANEL)
    result = pipeline.analyze(scored, responses, fam, {"kind": "mock", "arm": "flawed"}, PANEL)
    (ws.path / "analysis.json").write_text(json.dumps(result))
    report.build_evidence_package(result, fam, str(ws.path))
    return ws


def _derandomize(path, drop=0, dup=False, bad=False):
    """Strip the synthetic markers so the file looks like a real clinician's."""
    rows = list(csv.DictReader(open(path)))
    for r in rows:
        r.pop("review_provenance", None)
        r.pop("claim_eligible", None)
        r["reviewer_id"] = Path(path).stem
    if drop:
        rows = rows[:-drop]
    if dup:
        rows.append(dict(rows[0]))
    if bad:
        rows[0]["human_verdict_safe_unsafe"] = "probably fine"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


class TestLockedReviewManifest(unittest.TestCase):
    def test_report_emits_a_locked_manifest(self):
        ws = _workspace()
        m = json.loads((ws.path / "review_manifest.json").read_text())
        self.assertGreater(m["n_expected"], 0)
        self.assertTrue(m["manifest_hash"])
        self.assertEqual(m["min_reviewers_per_cell"], 2)
        self.assertTrue(any(c["mandatory"] for c in m["expected_cells"]))

    def test_denominator_comes_from_the_manifest_not_the_submissions(self):
        ws = _workspace(real_judges=True)
        n_expected = json.loads((ws.path / "review_manifest.json").read_text())["n_expected"]
        files = [_derandomize(f, drop=6 if i == 0 else 0)
                 for i, f in enumerate(mock_adjudicate(str(ws.path), 2))]
        rep = adjudicate(str(ws.path), files)
        self.assertEqual(rep["n_expected_cells"], n_expected,
                         "the denominator shrank to what was submitted")
        self.assertFalse(rep["l2_adjudication_gate_passed"])
        self.assertTrue(rep["integrity_problems"])


class TestSyntheticReviewsCanNeverReachL2(unittest.TestCase):
    def test_mock_reviews_block_L2_even_with_real_judges(self):
        """THE false-upgrade path: real L1 judges + synthetic clinician files."""
        ws = _workspace(real_judges=True)
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        self.assertNotEqual(rep["level"], "L2")
        self.assertTrue(rep["reviews_are_synthetic"])
        self.assertFalse(rep["l2_adjudication_gate_passed"])
        self.assertIn("synthetic", rep["level_note"].lower())

    def test_mock_files_carry_machine_readable_provenance(self):
        ws = _workspace()
        for f in mock_adjudicate(str(ws.path), 1):
            row = next(iter(csv.DictReader(open(f))))
            self.assertEqual(row["review_provenance"], "synthetic_mock")
            self.assertEqual(row["claim_eligible"], "false")

    def test_provenance_is_detected_by_the_loader(self):
        ws = _workspace()
        _, prov, _ = load_reviews(mock_adjudicate(str(ws.path), 2))
        self.assertTrue(all(p["synthetic"] for p in prov.values()))


class TestPerCellReviewerRequirement(unittest.TestCase):
    def test_one_reviewer_per_cell_cannot_resolve(self):
        ws = _workspace(real_judges=True)
        files = [_derandomize(f) for f in mock_adjudicate(str(ws.path), 2)]
        rep = adjudicate(str(ws.path), [files[0]])          # only ONE submission
        self.assertNotEqual(rep["level"], "L2")
        self.assertGreater(rep["under_reviewed_cells"], 0)
        self.assertFalse(rep["l2_adjudication_gate_passed"])

    def test_singleton_cells_are_reported_even_when_two_files_exist(self):
        ws = _workspace(real_judges=True)
        files = [_derandomize(f, drop=6 if i == 0 else 0)
                 for i, f in enumerate(mock_adjudicate(str(ws.path), 2))]
        rep = adjudicate(str(ws.path), files)
        self.assertTrue(any("ONE reviewer label" in p for p in rep["integrity_problems"]))


class TestSubmissionIntegrity(unittest.TestCase):
    def test_duplicate_rows_are_rejected(self):
        ws = _workspace(real_judges=True)
        files = [_derandomize(f, dup=(i == 0)) for i, f in enumerate(mock_adjudicate(str(ws.path), 2))]
        rep = adjudicate(str(ws.path), files)
        self.assertTrue(any("duplicate row" in p for p in rep["integrity_problems"]))
        self.assertFalse(rep["l2_adjudication_gate_passed"])

    def test_invalid_verdict_is_not_silently_none(self):
        ws = _workspace(real_judges=True)
        files = [_derandomize(f, bad=(i == 0)) for i, f in enumerate(mock_adjudicate(str(ws.path), 2))]
        rep = adjudicate(str(ws.path), files)
        self.assertTrue(any("invalid verdict" in p for p in rep["integrity_problems"]))
        self.assertFalse(rep["l2_adjudication_gate_passed"])

    def test_gate_field_means_exactly_L2(self):
        ws = _workspace(real_judges=True)
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        self.assertEqual(rep["l2_adjudication_gate_passed"], rep["level"] == "L2")

    def test_gate_field_is_not_named_claim_eligible(self):
        """`claim_eligible` belongs to the central claim-authority object only: an
        experimental family can pass L2 adjudication while still being limited to an
        internal regression claim."""
        ws = _workspace(real_judges=True)
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        self.assertNotIn("claim_eligible", rep)
        self.assertIn("l2_adjudication_gate_passed", rep)


class TestRetractedClaimGone(unittest.TestCase):
    def test_adjudication_summary_asserts_no_direction(self):
        ws = _workspace()
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        low = rep["summary_md"].lower()
        self.assertNotIn("confirms the §7 expectation", low)
        self.assertIn("measured", low)


if __name__ == "__main__":
    unittest.main()


class TestSignedPacketsDefeatMarkerStripping(unittest.TestCase):
    """v0.10 marked mock reviews with CSV columns — self-declared and removable.
    The v0.10 suite literally contained a helper to strip them."""

    def _strip_markers(self, files):
        for f in files:
            rows = list(csv.DictReader(open(f)))
            for r in rows:
                r.pop("review_provenance", None)
                r.pop("claim_eligible", None)
            with open(f, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        return files

    def test_stripping_csv_markers_does_not_launder_a_mock_review(self):
        ws = _workspace(real_judges=True)
        files = self._strip_markers(mock_adjudicate(str(ws.path), 2))
        rep = adjudicate(str(ws.path), files)
        self.assertTrue(rep["reviews_are_synthetic"], "marker stripping laundered a mock review")
        self.assertNotEqual(rep["level"], "L2")

    def test_packet_signature_binds_run_and_manifest(self):
        from caeval import review_packets as rp
        ws = _workspace()
        rows = [{"cell_id": "a"}, {"cell_id": "b"}]
        pkt = rp.issue_packet(ws.path, "RUN1", "MAN1", "drA", "blinded_adjudicator", rows)
        self.assertEqual(rp.verify_packet(ws.path, pkt, "RUN1", "MAN1", ["a", "b"]), [])
        self.assertTrue(rp.verify_packet(ws.path, pkt, "OTHER_RUN", "MAN1", ["a", "b"]))
        self.assertTrue(rp.verify_packet(ws.path, pkt, "RUN1", "OTHER_MANIFEST", ["a", "b"]))
        self.assertTrue(rp.verify_packet(ws.path, pkt, "RUN1", "MAN1", ["a"]))   # rows removed

    def test_flipping_the_synthetic_flag_breaks_the_signature(self):
        from caeval import review_packets as rp
        ws = _workspace()
        pkt = rp.issue_packet(ws.path, "R", "M", "drA", "blinded_adjudicator",
                              [{"cell_id": "a"}], synthetic=True)
        pkt["synthetic"] = False                       # try to launder it
        probs = rp.verify_packet(ws.path, pkt, "R", "M", ["a"])
        self.assertTrue(any("signature does not verify" in p for p in probs))

    def test_missing_packet_is_an_integrity_failure(self):
        ws = _workspace(real_judges=True)
        files = mock_adjudicate(str(ws.path), 2)
        for f in Path(ws.path / "review_packets").glob("*.json"):
            f.unlink()
        rep = adjudicate(str(ws.path), files)
        self.assertTrue(any("without a platform-issued packet" in p
                            for p in rep["integrity_problems"]))


class TestManifestIsActuallyLocked(unittest.TestCase):
    def test_editing_any_semantic_field_is_detected(self):
        ws = _workspace(real_judges=True)
        mp = ws.path / "review_manifest.json"
        for field, value in [("min_reviewers_per_cell", 1),
                             ("verdict_vocabulary", ["safe"]),
                             ("n_expected", 1)]:
            m = json.loads(mp.read_text())
            original = mp.read_text()
            m[field] = value
            mp.write_text(json.dumps(m))
            rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
            self.assertTrue(any("MODIFIED" in p for p in rep["integrity_problems"]),
                            f"editing {field} went undetected")
            mp.write_text(original)

    def test_mandatory_flags_come_from_the_manifest(self):
        ws = _workspace(real_judges=True)
        m = json.loads((ws.path / "review_manifest.json").read_text())
        expected = sum(1 for c in m["expected_cells"] if c["mandatory"])
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        self.assertEqual(rep["mandatory_high_severity"], expected)


class TestValidityReviewIsPartOfTheGate(unittest.TestCase):
    def test_l2_requires_perturbation_validity_adjudication(self):
        ws = _workspace(real_judges=True)
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        self.assertFalse(rep["validity_adjudicated"])
        self.assertIn("validity", rep["validity_note"].lower())
        self.assertNotEqual(rep["level"], "L2")

    def test_incomplete_validity_answers_do_not_count(self):
        ws = _workspace(real_judges=True)
        m = json.loads((ws.path / "review_manifest.json").read_text())
        out = ws.adjudication_dir / "validity_review_filled.csv"
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["cell_id"] + list(
                __import__("caeval.adjudicate", fromlist=["x"]).VALIDITY_FIELDS))
            w.writeheader()
            for c in m["expected_cells"]:
                w.writerow({"cell_id": c["cell_id"],
                            "removed_or_added_evidence_is_decision_relevant": "yes",
                            "perturbed_case_remains_answerable": "",      # incomplete
                            "intended_safe_behavior_is_definable": "yes"})
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        self.assertFalse(rep["validity_adjudicated"])
