"""End-to-end integration: the NORMAL user journey must reach every capability
the repository says is implemented.

The v0.15 defect this file exists to prevent: patient and RAG evaluation were
implemented, declared implemented, and unreachable from `run --project`. Unit
tests on the subsystems all passed. Nothing tested the journey, so nothing failed.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from caeval import executors, packsource, project as project_mod, selection
from caeval.cli import _run_project_bound


def _project(tmp, ptype, pack_source, subject):
    d = Path(tmp) / "proj"
    project_mod.write_template(d, "e2e")
    f = d / "project.yaml"
    data = yaml.safe_load(f.read_text())
    data["project"]["mode"] = "demonstration"
    data["target"] = {"name": "e2e-target", "version": "1", "vendor": "test"}
    data["target_profile"]["types"] = [ptype]
    data["intake"] = {k: "answered for the integration test" for k in data["intake"]}
    data["governance"] = {k: "no" for k in data["governance"]}
    data["subject"] = subject
    data["case_pack"] = {"source": pack_source, "pack_id": "e2e", "version": "1"}
    f.write_text(yaml.safe_dump(data, sort_keys=False))
    return d


class _Args:
    def __init__(self, project, workspace):
        self.project, self.workspace = str(project), str(workspace)
        self.family = "missing_information"
        self.subject = self.cases = self.panel = None
        self.arm = "flawed"


class TestExecutorRegistry(unittest.TestCase):
    def test_every_selectable_family_has_an_executor(self):
        """A family that can be planned but not executed crashes the run — or
        worse, is run by a backend that mis-scores it."""
        rules = selection.load_rules()
        for name, meta in rules["suites"].items():
            if meta.get("implemented"):
                self.assertTrue(executors.has_executor(name),
                                f"{name} is selectable but has no executor")

    def test_every_registered_executor_family_is_declared(self):
        rules = selection.load_rules()["suites"]
        for row in executors.inventory():
            self.assertIn(row["family_id"], rules,
                          f"{row['family_id']} has an executor but is not in selection_rules")

    def test_resolve_refuses_to_default(self):
        with self.assertRaises(executors.ExecutorError):
            executors.resolve("not_a_family")

    def test_pack_kind_mismatch_is_refused(self):
        with self.assertRaises(executors.ExecutorError):
            executors.check_pack_compatibility("patient_red_flag", "clinician_vignette")
        with self.assertRaises(executors.ExecutorError):
            executors.check_pack_compatibility("missing_information", "patient_worlds")

    def test_patient_family_requires_a_conversational_subject(self):
        with self.assertRaises(executors.ExecutorError):
            executors.check_subject_compatibility("patient_red_flag", "single_turn")


class TestPackBinding(unittest.TestCase):
    def test_project_without_a_case_pack_is_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _project(tmp, "patient_triage_chatbot", "builtin:public_smoke",
                         {"kind": "mock", "arm": "mock_repaired"})
            data = yaml.safe_load((d / "project.yaml").read_text())
            data.pop("case_pack")
            (d / "project.yaml").write_text(yaml.safe_dump(data))
            proj = project_mod.load(d)
            problems = proj.validate()
            problems = problems if isinstance(problems, list) else problems.get("problems", [])
            self.assertTrue(any("case_pack" in str(p) for p in problems), problems)

    def test_wrong_pack_kind_stops_the_run(self):
        with self.assertRaises(packsource.PackSourceError):
            packsource.resolve({"source": "builtin:demo_clinician"}, "patient_worlds")

    def test_builtin_packs_are_marked_demonstration_only(self):
        for src, kind in (("builtin:demo_clinician", "clinician_vignette"),
                          ("builtin:public_smoke", "patient_worlds")):
            _cases, desc = packsource.resolve({"source": src}, kind)
            self.assertTrue(desc["demonstration_only"])
            self.assertFalse(desc["clinician_reviewed"])

    def test_pack_is_content_addressed(self):
        cases, desc = packsource.resolve({"source": "builtin:public_smoke"}, "patient_worlds")
        self.assertTrue(desc["content_hash"])
        self.assertEqual(desc["content_hash"],
                         packsource.content_hash(cases, "patient_worlds"))


class TestPatientJourney(unittest.TestCase):
    """project init -> validate -> plan -> run, for a PATIENT product."""

    def test_plan_selects_the_patient_family(self):
        sel = selection.select_suites(["patient_triage_chatbot"])
        self.assertEqual(sel["runnable_suites"], ["patient_red_flag"])

    def test_run_project_produces_episodes_and_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _project(tmp, "patient_triage_chatbot", "builtin:public_smoke",
                         {"kind": "mock", "arm": "mock_repaired", "modality": "conversation"})
            ws = Path(tmp) / "ws"
            _run_project_bound(_Args(d, ws))
            run_dir = ws / "run_patient_red_flag"
            for f in ("episodes.jsonl", "results.jsonl", "analysis.json",
                      "final_report.md", "human_review.csv", "case_pack.json",
                      "plan_binding.json"):
                self.assertTrue((run_dir / f).exists(), f"missing {f}")

            episodes = [json.loads(l) for l in
                        (run_dir / "episodes.jsonl").read_text().splitlines() if l.strip()]
            self.assertTrue(episodes)
            self.assertIn("trace_hash", episodes[0])

            records = [json.loads(l) for l in
                       (run_dir / "results.jsonl").read_text().splitlines() if l.strip()]
            self.assertTrue(all(r["response_text"].strip() for r in records),
                            "a judge must never be handed a blank response")
            self.assertTrue(all(r["judge_contract"] == "patient_multiturn_v1" for r in records))

    def test_patient_run_reports_coverage_and_caps_the_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _project(tmp, "patient_triage_chatbot", "builtin:public_smoke",
                         {"kind": "mock", "arm": "mock_defective", "modality": "conversation"})
            ws = Path(tmp) / "ws"
            _run_project_bound(_Args(d, ws))
            analysis = json.loads((ws / "run_patient_red_flag" / "analysis.json").read_text())
            self.assertIn("coverage", analysis)
            self.assertGreater(analysis["coverage"]["stress_cells_skipped"], 0)
            self.assertFalse(analysis["claim_inputs"]["may_support_a_claim"])
            report = (ws / "run_patient_red_flag" / "final_report.md").read_text()
            self.assertIn("mock", report.lower())
            self.assertIn("separately", report.lower())

    def test_patient_report_never_combines_safety_and_usefulness(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _project(tmp, "patient_triage_chatbot", "builtin:public_smoke",
                         {"kind": "mock", "arm": "mock_over_conservative",
                          "modality": "conversation"})
            ws = Path(tmp) / "ws"
            _run_project_bound(_Args(d, ws))
            report = (ws / "run_patient_red_flag" / "final_report.md").read_text().lower()
            for banned in ("overall score", "combined score", "safety score", "recommendation"):
                self.assertNotIn(banned, report)


class TestRagJourney(unittest.TestCase):
    def test_plan_selects_the_rag_families(self):
        runnable = selection.select_suites(["clinician_rag"])["runnable_suites"]
        self.assertIn("retrieval_failure", runnable)
        self.assertIn("citation_verification", runnable)

    def test_rag_run_produces_traces_with_retrieval_and_generation_separated(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _project(tmp, "clinician_rag", "builtin:demo_clinician",
                         {"kind": "mock", "arm": "flawed"})
            # a clinician-RAG product needs BOTH pack kinds
            f = d / "project.yaml"
            data = yaml.safe_load(f.read_text())
            data["case_pack"] = {
                "clinician_vignette": {"source": "builtin:demo_clinician"},
                "rag_corpus_bound": {"source": "builtin:demo_rag_corpus"}}
            f.write_text(yaml.safe_dump(data, sort_keys=False))
            ws = Path(tmp) / "ws"
            _run_project_bound(_Args(d, ws))
            run_dir = ws / "run_retrieval_failure"
            self.assertTrue((run_dir / "rag_traces.jsonl").exists())
            traces = [json.loads(l) for l in
                      (run_dir / "rag_traces.jsonl").read_text().splitlines() if l.strip()]
            self.assertTrue(traces)
            for key in ("query", "corpus_hash", "retrieved_document_ids", "retrieved_chunks",
                        "ranking", "final_answer", "citations"):
                self.assertIn(key, traces[0])
            analysis = json.loads((run_dir / "analysis.json").read_text())
            self.assertIn("retrieval", analysis["summary"])
            self.assertIn("generation", analysis["summary"])


class TestClinicianJourneyStillWorks(unittest.TestCase):
    def test_generic_families_run_through_the_project_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = _project(tmp, "clinician_decision_support", "builtin:demo_clinician",
                         {"kind": "mock", "arm": "flawed"})
            ws = Path(tmp) / "ws"
            _run_project_bound(_Args(d, ws))
            self.assertTrue((ws / "run_missing_information" / "final_report.md").exists())


if __name__ == "__main__":
    unittest.main()


class TestDocumentationMatchesTheCode(unittest.TestCase):
    """Documentation drift was a real defect: the README described two implemented
    families while five shipped, and PRODUCT_V1.md said patient evaluation did not
    exist after it did. A user could not tell what was supported."""

    def test_registries_agree(self):
        from caeval import capabilities
        problems = capabilities.check_consistency()
        self.assertEqual(problems, [], "registries disagree:\n" + "\n".join(problems))

    def test_readme_capability_table_is_current(self):
        from caeval import capabilities
        from caeval.util import repo_root
        readme = (repo_root() / "README.md").read_text()
        self.assertIn("<!-- BEGIN GENERATED CAPABILITIES -->", readme)
        generated = capabilities.render_markdown()
        start = readme.index("<!-- BEGIN GENERATED CAPABILITIES -->") + len(
            "<!-- BEGIN GENERATED CAPABILITIES -->")
        end = readme.index("<!-- END GENERATED CAPABILITIES -->")
        self.assertEqual(readme[start:end].strip(), generated.strip(),
                         "README capability table has drifted; regenerate it")

    def test_every_runnable_family_appears_as_runnable(self):
        from caeval import capabilities
        rows = {r["family_id"]: r for r in capabilities.table()}
        for fid in ("patient_red_flag", "retrieval_failure", "citation_verification"):
            self.assertTrue(rows[fid]["selectable"], f"{fid} regressed to not-runnable")
            self.assertTrue(rows[fid]["executor"])

    def test_stale_product_doc_is_flagged(self):
        from caeval.util import repo_root
        t = (repo_root() / "PRODUCT_V1.md").read_text()
        self.assertIn("STATUS NOTE", t.upper().replace("STATUS NOTE (V0.16)", "STATUS NOTE"))
