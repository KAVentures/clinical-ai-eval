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
        self.assertFalse(rep["claim_eligible"])
        self.assertTrue(rep["integrity_problems"])


class TestSyntheticReviewsCanNeverReachL2(unittest.TestCase):
    def test_mock_reviews_block_L2_even_with_real_judges(self):
        """THE false-upgrade path: real L1 judges + synthetic clinician files."""
        ws = _workspace(real_judges=True)
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        self.assertNotEqual(rep["level"], "L2")
        self.assertTrue(rep["reviews_are_synthetic"])
        self.assertFalse(rep["claim_eligible"])
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
        self.assertFalse(rep["claim_eligible"])

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
        self.assertFalse(rep["claim_eligible"])

    def test_invalid_verdict_is_not_silently_none(self):
        ws = _workspace(real_judges=True)
        files = [_derandomize(f, bad=(i == 0)) for i, f in enumerate(mock_adjudicate(str(ws.path), 2))]
        rep = adjudicate(str(ws.path), files)
        self.assertTrue(any("invalid verdict" in p for p in rep["integrity_problems"]))
        self.assertFalse(rep["claim_eligible"])

    def test_claim_eligible_means_exactly_L2(self):
        ws = _workspace(real_judges=True)
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        self.assertEqual(rep["claim_eligible"], rep["level"] == "L2")


class TestRetractedClaimGone(unittest.TestCase):
    def test_adjudication_summary_asserts_no_direction(self):
        ws = _workspace()
        rep = adjudicate(str(ws.path), mock_adjudicate(str(ws.path), 2))
        low = rep["summary_md"].lower()
        self.assertNotIn("confirms the §7 expectation", low)
        self.assertIn("measured", low)


if __name__ == "__main__":
    unittest.main()
