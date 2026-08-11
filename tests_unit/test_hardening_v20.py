"""Findings from the v0.19 external review.

Grouped here because they share one shape: an integrity property that was
DESCRIBED (in a comment, a docstring, or a manifest field) but not ENFORCED.
"""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from caeval import lifecycle, manifest as man, review_packets as rp, unit_review  # noqa: E402
from caeval.cli import _safe_extract, cmd_adjudicate  # noqa: E402
from test_unit_review import _A, _patient_run, _reviews  # noqa: E402


class TestArchiveExtractionIsSafe(unittest.TestCase):
    """`verify-package` is pointed at archives from OTHER parties, so the archive
    is the untrusted input."""

    def _zip(self, tmp, name, data=b"x", mode=None):
        z = Path(tmp) / "a.zip"
        with zipfile.ZipFile(z, "w") as f:
            info = zipfile.ZipInfo(name)
            if mode:
                info.external_attr = mode << 16
            f.writestr(info, data)
        return z

    def test_parent_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = self._zip(tmp, "../escaped.txt")
            out = Path(tmp) / "out"
            out.mkdir()
            with zipfile.ZipFile(z) as f, self.assertRaises(SystemExit):
                _safe_extract(f, out)
            self.assertFalse((Path(tmp) / "escaped.txt").exists())

    def test_absolute_path_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = self._zip(tmp, "/tmp/abs.txt")
            out = Path(tmp) / "out"
            out.mkdir()
            with zipfile.ZipFile(z) as f, self.assertRaises(SystemExit):
                _safe_extract(f, out)

    def test_symlink_member_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = self._zip(tmp, "link", data=b"/etc/passwd", mode=0o120777)
            out = Path(tmp) / "out"
            out.mkdir()
            with zipfile.ZipFile(z) as f, self.assertRaises(SystemExit):
                _safe_extract(f, out)

    def test_a_benign_archive_still_extracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            z = self._zip(tmp, "dir/ok.txt", data=b"fine")
            out = Path(tmp) / "out"
            out.mkdir()
            with zipfile.ZipFile(z) as f:
                _safe_extract(f, out)
            self.assertEqual((out / "dir" / "ok.txt").read_bytes(), b"fine")


class TestVerificationIsReadOnly(unittest.TestCase):
    def test_verify_does_not_create_a_run_secret(self):
        """Minting a key during verification turns 'the key is missing' into 'the
        packets are forged', and writes to the package under audit."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            packet = {f: "x" for f in rp.PACKET_FIELDS}
            packet["signature"] = "deadbeef"
            problems = rp.verify_packet(ws, packet, "run", "mh", [])
            self.assertTrue(any("must not modify" in p for p in problems), problems)
            self.assertFalse((ws / rp.SECRET_FILE).exists())

    def test_read_run_secret_returns_none_rather_than_creating(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(rp.read_run_secret(Path(tmp)))
            self.assertFalse((Path(tmp) / rp.SECRET_FILE).exists())


class TestReviewerIdentityIsBound(unittest.TestCase):
    def test_a_packet_issued_to_another_reviewer_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            rp.ensure_run_secret(ws)
            packet = rp.issue_packet(ws, run_id="r1", manifest_hash="m1",
                                     reviewer_id="dr_alpha", reviewer_role="clinician",
                                     synthetic=False, rows=[{"cell_id": "c1"}])
            ok = rp.verify_packet(ws, packet, "r1", "m1", ["c1"],
                                  expected_reviewer_id="dr_alpha")
            self.assertEqual(ok, [])
            swapped = rp.verify_packet(ws, packet, "r1", "m1", ["c1"],
                                       expected_reviewer_id="dr_beta")
            self.assertTrue(any("issued to reviewer" in p for p in swapped), swapped)

    def test_reviewer_ids_cannot_traverse_the_filesystem(self):
        for bad in ("../../etc/passwd", "a/b", "", "..", ".hidden"):
            with self.assertRaises(ValueError):
                rp._safe_reviewer_id(bad)


class TestJudgeFlaggedCellsReachAHuman(unittest.TestCase):
    """The routing fail-open: queues were built from deterministic flags only, so a
    cell the PANEL called unsafe — or that split the panel — never reached review."""

    def test_panel_unsafe_creates_a_review_trigger(self):
        cell = {"item_id": "u1", "panel_scored": True,
                "panel_labels": {"a": {"under_triage": 1}, "b": {"under_triage": 0}}}
        strata = lifecycle.judge_triggered_strata(cell, ["under_triage"])
        self.assertIn("panel_any_under_triage", strata)
        self.assertIn("judge_disagreement", strata)

    def test_incomplete_quorum_and_errors_are_triggers(self):
        cell = {"item_id": "u1", "panel_scored": False,
                "panel_labels": {"a": {"under_triage": 0}},
                "panel_errors": ["b: timeout"]}
        strata = lifecycle.judge_triggered_strata(cell, ["under_triage"])
        self.assertIn("incomplete_quorum", strata)
        self.assertIn("judge_error", strata)

    def test_merge_is_a_union_not_a_replacement(self):
        det = [{"unit_id": "u1", "strata": ["under_triage"], "content": "t"}]
        cells = [{"item_id": "u1", "panel_scored": True,
                  "panel_labels": {"a": {"missed_red_flag": 1},
                                   "b": {"missed_red_flag": 1}}},
                 {"item_id": "u2", "panel_scored": True,
                  "panel_labels": {"a": {"missed_red_flag": 1},
                                   "b": {"missed_red_flag": 1}}}]
        merged = lifecycle.merge_review_queue(det, cells, ["missed_red_flag"])
        by = {m["unit_id"]: m for m in merged}
        self.assertIn("under_triage", by["u1"]["strata"])
        self.assertIn("panel_any_missed_red_flag", by["u1"]["strata"])
        self.assertIn("u2", by, "a judge-only trigger must add a unit to the queue")

    def test_judge_triggers_are_mandatory_to_resolve(self):
        m = lifecycle.mandatory_strata_for(["under_triage"])
        self.assertIn("panel_any_under_triage", m)
        self.assertIn("judge_disagreement", m)


class TestEmptySlicesReportNA(unittest.TestCase):
    """A zero failure rate over zero episodes is the absence of a result, not a
    clean one."""

    def test_patient_summary_of_nothing_is_NA(self):
        from caeval.patient.run import summarize
        s = summarize([])
        self.assertIsNone(s["any_safety_failure_rate"])
        self.assertIn("NA, not 0", s["note"])

    def test_rag_summary_of_nothing_is_NA(self):
        from caeval.rag.execute import summarize
        s = summarize([])
        self.assertIsNone(s["retrieval"]["supporting_document_retrieved_rate"])


class TestManifestCoversHumanInputsAndExecutorDefinitions(unittest.TestCase):
    def _adjudicated(self, tmp):
        run = _patient_run(tmp)
        files, _ = _reviews(run, ["dr_a", "dr_b"])
        cmd_adjudicate(_A(run, reviews=files))
        return run, files

    def test_submitted_reviews_are_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _ = self._adjudicated(tmp)
            mf = json.loads((run / man.MANIFEST_FILE).read_text())
            self.assertTrue(mf["review_inputs"])

    def test_editing_a_submitted_review_invalidates_the_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, files = self._adjudicated(tmp)
            self.assertEqual(man.verify_manifest(run)["verdict"], "VALID")
            f = Path(files[0])
            f.write_text(f.read_text().replace("unsafe", "safe"))
            self.assertEqual(man.verify_manifest(run)["verdict"], "INVALID")

    def test_removing_a_submitted_review_invalidates_the_package(self):
        """Dropping a dissenting reviewer changes the adjudication outcome."""
        with tempfile.TemporaryDirectory() as tmp:
            run, _files = self._adjudicated(tmp)
            adj = run / "adjudication"
            victim = next(p for p in adj.rglob("*.json"))
            victim.unlink()
            self.assertEqual(man.verify_manifest(run)["verdict"], "INVALID")

    def test_patient_definitions_are_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            defs = json.loads((run / man.MANIFEST_FILE).read_text())["definitions"]
            for rel in ("prompts/patient_judge_prompt.txt", "caeval/patient/scoring.py",
                        "caeval/patient/stress.py", "caeval/patient/world.py"):
                self.assertIn(rel, defs)
                self.assertTrue(defs[rel])


class TestFamilySpecificAdjudicationEndpoints(unittest.TestCase):
    def test_patient_endpoint_is_declared_not_borrowed(self):
        spec = unit_review.FAMILY_JUDGE_ENDPOINT["patient_red_flag"]
        self.assertNotIn("unsafe_overconfident", spec["components"])
        self.assertIn("under_triage", spec["components"])
        self.assertIn("missed_red_flag", spec["components"])

    def test_an_undeclared_family_refuses_to_substitute(self):
        out = unit_review.judge_vs_human({"u1": "unsafe"}, {"u1": 1}, "no_such_family")
        self.assertFalse(out["applicable"])
        self.assertIn("Refusing", out["note"])

    def test_calibration_is_computed_for_a_patient_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            files, _ = _reviews(run, ["dr_a", "dr_b"])
            rep = cmd_adjudicate(_A(run, reviews=files))
            cal = rep["judge_vs_human"]
            self.assertTrue(cal["applicable"])
            self.assertEqual(cal["endpoint"], "any_safety_flag")
            self.assertGreater(cal["n_compared"], 0)

    def test_zero_overlap_is_reported_as_no_calibration(self):
        out = unit_review.judge_vs_human({"u1": "contested"}, {"u1": 1},
                                         "patient_red_flag")
        self.assertEqual(out["n_compared"], 0)
        self.assertIn("NO calibration", out["note"])

    def test_unjudged_units_are_not_counted_as_automated_safe(self):
        out = unit_review.judge_vs_human({"u1": "unsafe"}, {"u1": None},
                                         "patient_red_flag")
        self.assertEqual(out["n_compared"], 0)


class TestRagReviewMaterialIsEvidentiary(unittest.TestCase):
    def test_rag_review_rows_carry_the_whole_context(self):
        import yaml

        from test_integration_e2e import _Args, _project
        from caeval.cli import _run_project_bound
        with tempfile.TemporaryDirectory() as tmp:
            d = _project(tmp, "clinician_rag", "builtin:demo_clinician",
                         {"kind": "mock", "arm": "flawed"})
            f = d / "project.yaml"
            data = yaml.safe_load(f.read_text())
            data["case_pack"] = {
                "clinician_vignette": {"source": "builtin:demo_clinician"},
                "rag_corpus_bound": {"source": "builtin:demo_rag_corpus"}}
            f.write_text(yaml.safe_dump(data, sort_keys=False))
            ws = Path(tmp) / "ws"
            _run_project_bound(_Args(d, ws))
            csv_text = (ws / "run_retrieval_failure" / "human_review.csv").read_text()
            for needed in ("CLINICAL QUESTION", "RETRIEVED DOCUMENT IDs",
                           "RETRIEVED CONTEXT", "CITATIONS RESOLVED", "ROUTED BECAUSE"):
                self.assertIn(needed, csv_text,
                              f"a reviewer cannot judge support without {needed}")


if __name__ == "__main__":
    unittest.main()


class TestDocumentationHasNoCompetingFamilyTable(unittest.TestCase):
    """The drift guard checked only the generated block, so a second hand-written
    table in the same README contradicted it for several versions."""

    def test_readme_has_exactly_one_family_table(self):
        from caeval.util import repo_root
        readme = (repo_root() / "README.md").read_text()
        self.assertEqual(readme.count("| family |"), 1,
                         "a second family table has been reintroduced; it will drift")

    def test_readme_does_not_call_a_runnable_family_blocked(self):
        from caeval import capabilities
        from caeval.util import repo_root
        readme = (repo_root() / "README.md").read_text()
        for row in capabilities.table():
            if row.get("selectable"):
                self.assertNotIn(f"`{row['family_id']}` | experimental | **BLOCKED**",
                                 readme)

    def test_superseded_product_claims_are_marked(self):
        from caeval.util import repo_root
        t = (repo_root() / "PRODUCT_V1.md").read_text()
        for stale in ("- Patient-facing evaluation is not implemented and fails closed.",
                      "- Clinical review is CSV-based; there is no browser review UI."):
            self.assertNotIn(stale, t, "a superseded claim is still asserted as current")


class TestReviewedWorkspaceCannotBeSilentlyRelocked(unittest.TestCase):
    """Rebuilding a workspace whose packets are issued would replace the LOCKED
    manifest around a different queue, and submissions issued against the old one
    would then verify against a manifest their reviewers never saw."""

    def test_a_changed_queue_is_refused_once_reviews_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            files, _ = _reviews(run, ["dr_a", "dr_b"])
            cmd_adjudicate(_A(run, reviews=files))
            with self.assertRaises(lifecycle.WorkspaceLockedError):
                lifecycle._guard_locked_review(
                    run, [{"unit_id": "a_completely_different_unit", "strata": []}])

    def test_the_same_queue_is_allowed(self):
        """Re-emitting after adjudication must keep working."""
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            files, _ = _reviews(run, ["dr_a", "dr_b"])
            cmd_adjudicate(_A(run, reviews=files))
            mf = unit_review.load_review_manifest(run)
            same = [{"unit_id": u["unit_id"], "strata": u["strata"]}
                    for u in mf["expected_units"]]
            lifecycle._guard_locked_review(run, same)      # must not raise


class TestRejudgeCannotShrinkTheReviewQueue(unittest.TestCase):
    """A v0.20 regression, found while verifying v0.20: `judge` rebuilt the queue
    from judge-derived triggers alone and dropped 76 of 88 units — every
    deterministic mandatory one among them. Re-scoring may ADD review units; it can
    never remove them, because the deterministic triggers did not stop being true
    when a different panel was asked."""

    def test_rejudging_never_drops_a_routed_unit(self):
        from caeval.cli import cmd_judge

        class J:
            pass
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            before = {u["unit_id"] for u in
                      unit_review.load_review_manifest(run)["expected_units"]}
            j = J()
            j.workspace, j.panel = str(run), None
            cmd_judge(j)
            after = {u["unit_id"] for u in
                     unit_review.load_review_manifest(run)["expected_units"]}
            self.assertEqual(before - after, set(), "re-judging dropped review units")

    def test_prior_units_are_preserved_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = _patient_run(tmp)
            merged = lifecycle._preserve_prior_units(
                run, [{"unit_id": "brand_new", "strata": ["judge_disagreement"]}])
            ids = {m["unit_id"] for m in merged}
            self.assertIn("brand_new", ids)
            prior = {u["unit_id"] for u in
                     unit_review.load_review_manifest(run)["expected_units"]}
            self.assertTrue(prior <= ids)
