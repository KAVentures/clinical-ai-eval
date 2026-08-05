"""clinical-ai-eval CLI (EVAL_STANDARD.md §10). Stages hand off through a disk
WORKSPACE so judging is separable from generation:

  init      scaffold a target descriptor + keys template + a run workspace
  plan      intake -> eval_plan.yaml + rule-based suite selection (§2, §4)
  inspect   show profile, selection, and judge-panel status
  run       generate subject responses + score + evidence package (writes workspace)
  judge     re-score EXISTING frozen responses with a (possibly different) panel
  report    re-emit the evidence package from a workspace
  adjudicate ingest filled human_review.csv -> L2 agreement + sensitivity (§1, §8)
  demo      zero-config offline run incl. mock L2 adjudication
  arms      harness self-validation across subject arms (§12)
"""
from __future__ import annotations

import argparse
import functools
import json
from pathlib import Path

import yaml

from . import adjudicate as adj
from . import intake as intake_mod
from . import pipeline, report, selection
from .subject import build_subject
from .util import repo_root, utc_now_iso
from .workspace import Workspace
from targets import demo_target

def _resolve_target_meta(args):
    """Intake comes from the USER's project when one is given; the demo constant is
    ONLY the fallback for the built-in demo commands. Planning against a demo
    profile while the user believes it describes their product is the core
    self-service hazard, so `--project` is always preferred and validated first."""
    from . import project as project_mod
    path = getattr(args, "project", None)
    if not path:
        return DEMO_TARGET_META, None
    proj = project_mod.load(path)
    proj.require_valid()                       # fail closed on an incomplete intake
    return proj.target_meta, proj


DEMO_TARGET_META = {
    "name": "demo_clinical_assistant", "version": "mock-0.1", "endpoint": "(local mock)",
    "profiles": ["clinician_decision_support", "medication_assistant"],
    "what_it_does": "Suggests diagnosis and initial management from a free-text clinical vignette.",
    "who_is_the_user": "clinician",
    "decisions_it_can_influence": "diagnosis, medication choice and dosing, disposition.",
    "information_normally_received": "HPI, exam, labs, imaging as free text.",
    "information_normally_unavailable": "anything not pasted into the prompt (e.g. omitted labs).",
    "expected_human_oversight": "clinician reviews every suggestion before acting.",
    "plausible_harm": "a confident wrong dose or diagnosis when decision-relevant data are missing.",
    "input_modalities": ["free_text"],
    "output_actions": ["diagnosis_suggestion", "management_suggestion", "dosing_suggestion"],
}


def _plan(target_meta: dict) -> dict:
    plan = intake_mod.build_eval_plan(target_meta)
    plan["selection"] = selection.select_suites(plan["target_profile"]["types"])
    return plan


def _panel_and_keys(panel_path=None):
    panel = pipeline.load_panel(panel_path) if panel_path else pipeline.load_panel()
    pipeline.assess_panel(panel)  # enforce >=2 DISTINCT providers BEFORE touching keys (§7, §11)
    keys = None
    if not all(j.get("mock") for j in panel["judges"]):
        from .providers import load_keys
        keys = load_keys()
    return panel, keys


def _score_and_report(ws: Workspace, panel, keys, subject_spec, family, project=None, binding=None):
    responses = ws.read_responses()
    scored = pipeline.score_responses(responses, panel, keys)
    result = pipeline.analyze(scored, responses, family, subject_spec, panel)
    if project is not None:
        from . import claim as claim_mod, maturity as maturity_mod
        authority = claim_mod.compute(project.mode,
                                      result["panel"]["conformance_level"],
                                      maturity_mod.family_maturity(family))
        result["claim_authority"] = authority.as_dict()
        result["plan_binding"] = binding or {}
    (ws.path / "analysis.json").write_text(json.dumps({k: v for k, v in result.items() if k != "_response_rows"}))
    pkg = report.build_evidence_package(result, family, str(ws.path))
    pkg["claim_label"] = (result.get("claim_authority") or {}).get("label", pkg["conformance_level"])
    return result, pkg


def _generate(ws: Workspace, subject_spec, family, cases, panel):
    pipeline.assess_panel(panel)  # enforce >=2 distinct providers up front
    subject_fn = build_subject(subject_spec)
    responses = pipeline.generate_responses(subject_fn, family, cases)
    ws.write_responses(responses)
    ws.write_run_meta({
        "generated_at": utc_now_iso(), "subject_spec": subject_spec, "family_id": family["family_id"],
        "panel": {"names": [j["name"] for j in panel["judges"]], "judges": panel["judges"],
                  "conformance_level": pipeline.assess_panel(panel)["conformance_level"],
                  "all_mock": pipeline.assess_panel(panel)["all_mock"]},
        "seeds": {"review_sampling_seed": 62}, "n_responses": len(responses),
    })
    return responses


# -------------------------------- commands --------------------------------
def cmd_plan(args):
    meta, proj = _resolve_target_meta(args)
    if proj is None:
        print("NOTE: no --project given; planning against the built-in DEMO target.\n"
              "      For your own product: clinical-ai-eval project init <dir>\n")
    plan = _plan(meta)
    if proj is not None:
        plan["run_mode"] = {"mode": proj.mode, "claim_label": proj.claim_label()}
    out = repo_root() / "out"; out.mkdir(exist_ok=True)
    intake_mod.write_eval_plan(plan, str(out / "eval_plan.yaml"))
    print(yaml.safe_dump(plan, sort_keys=False))
    print(f"wrote {out/'eval_plan.yaml'}")


def cmd_inspect(args):
    meta, proj = _resolve_target_meta(args)
    if proj is None:
        print("NOTE: no --project given; inspecting the built-in DEMO target.")
    plan = _plan(meta)
    panel = pipeline.load_panel()
    info = pipeline.assess_panel(panel)
    print("Target profiles:", plan["target_profile"]["types"], "| audience:", plan["target_profile"]["audience"])
    print("Matched selection rules:", plan["selection"]["matched_rules"])
    print("Runnable suites:", plan["selection"]["runnable_suites"])
    print("Required-but-not-run:", plan["selection"]["required_but_not_run"])
    print("Panel:", [j["name"] for j in panel["judges"]], "| distinct providers:", info["distinct_providers"],
          "| conformance ceiling:", info["conformance_level"])


def cmd_init(args):
    ws = Workspace(args.workspace or (repo_root() / "out" / "workspace")).ensure()
    (ws.path / "target.yaml").write_text(yaml.safe_dump(DEMO_TARGET_META, sort_keys=False))
    (ws.path / "API_KEYS.local.md").write_text(
        "# git-ignored. Fill for L1. Never commit.\nOPENAI_API_KEY = \nANTHROPIC_API_KEY = \nGOOGLE_API_KEY = \n")
    (repo_root() / "configs" / "judge_panel.toml")  # already exists
    print(f"Initialized workspace at {ws.path}")
    print("  - target.yaml (edit the intake), API_KEYS.local.md (fill for L1)")
    print("  - edit configs/judge_panel.toml to a real >=2-different-provider panel, then `run`.")


def cmd_run(args):
    # ---- PROJECT-BOUND EXECUTION (the workflow-binding fix) ----
    # When --project is given, every dimension of the run is DERIVED from the
    # validated project. CLI overrides are refused rather than silently honoured:
    # planning one assessment and executing another is the defect this prevents.
    if getattr(args, "project", None):
        return _run_project_bound(args)

    family = pipeline.load_family(args.family)
    cases = _load_cases(args)
    panel, keys = _panel_and_keys(args.panel)
    if args.subject:
        subject_spec = json.loads(Path(args.subject).read_text())
    else:
        subject_spec = {"kind": "mock", "arm": args.arm, "name": DEMO_TARGET_META["name"],
                        "version": DEMO_TARGET_META["version"], "mock": True}
    tag = subject_spec.get("arm", subject_spec.get("kind"))
    default_ws = repo_root() / "out" / (f"run_{args.family}_{tag}" if args.family != "missing_information" else f"run_{tag}")
    ws = Workspace(args.workspace or default_ws).ensure()
    _generate(ws, subject_spec, family, cases, panel)
    _, pkg = _score_and_report(ws, panel, keys, subject_spec, family)
    print(f"[{subject_spec.get('arm', subject_spec.get('kind'))}] conformance: {pkg['conformance_level']} -> {pkg['final_report_md']}")
    print(f"  review queue: {pkg['n_review_selected']} cells -> {pkg['human_review_csv']}")


OVERRIDABLE_BY_PROJECT = ("family", "subject", "cases", "panel", "arm")


def _run_project_bound(args):
    """Execute exactly the assessment the validated project describes."""
    from . import claim as claim_mod
    from . import project as project_mod
    from .study import hash_case_set

    proj = project_mod.load(args.project)
    proj.require_valid()                       # fail closed on an incomplete intake

    # Refuse overrides: they would decouple execution from the validated plan.
    supplied = [f for f in OVERRIDABLE_BY_PROJECT
                if getattr(args, f, None) not in (None, "flawed", "missing_information")]
    if supplied:
        raise SystemExit(
            f"--project binds the run; refusing these overrides: {supplied}. "
            f"Edit project.yaml instead — an evidence package must describe the "
            f"assessment that was planned and validated.")

    sel = selection.select_suites(proj.profiles)
    runnable = sel["runnable_suites"]
    if not runnable:
        raise SystemExit(
            f"no runnable suite for profiles {proj.profiles}: "
            + "; ".join(f"{b['suite']}: {b['blocked_reason'][:90]}" for b in sel["required_but_not_run"]))

    from . import executors, packsource

    panel, keys = _panel_and_keys(proj.data.get("panel", {}).get("config"))

    subject_spec = dict(proj.subject)
    subject_spec.setdefault("name", proj.target_meta["name"])
    subject_spec.setdefault("version", proj.target_meta["version"])
    subject_spec["audience"] = claim_mod._audience_for(proj)

    ws_root = Path(args.workspace) if args.workspace else (Path(proj.path) / "out")
    results = []
    for family_id in runnable:
        # Which backend runs this family, and therefore what shape of pack and
        # subject it needs. Raises rather than defaulting to the one-shot path:
        # running a multi-turn family through it produced a report that looked
        # complete and measured something else.
        ex = executors.resolve(family_id)
        cases, pack_desc = packsource.resolve(
            proj.data.get("case_pack", {}), ex.pack_kind)
        case_pack_hash = pack_desc["content_hash"]

        family = pipeline.load_family(family_id)
        binding = claim_mod.build_binding(proj, family_id, [j["name"] for j in panel["judges"]],
                                          case_pack_hash)
        ws = Workspace(ws_root / f"run_{family_id}").ensure()
        (ws.path / "plan_binding.json").write_text(json.dumps(binding, indent=2))
        (ws.path / "case_pack.json").write_text(json.dumps(pack_desc, indent=2))

        if ex.executor_id == executors.PATIENT_EPISODE:
            pkg = _run_patient_family(ws, proj, family_id, cases, pack_desc, subject_spec)
        elif ex.executor_id == executors.RAG_TRACE:
            pkg = _run_rag_family(ws, proj, family_id, cases, pack_desc, subject_spec)
        else:
            _generate(ws, subject_spec, family, cases, panel)
            actual = claim_mod.build_binding(proj, family_id,
                                             [j["name"] for j in panel["judges"]],
                                             packsource.content_hash(cases, ex.pack_kind))
            claim_mod.verify_binding(binding, actual)
            _rr, pkg = _score_and_report(ws, panel, keys, subject_spec, family,
                                         project=proj, binding=binding)
        results.append((family_id, pkg))

    print(f"project: {proj.name}   mode: {proj.mode}")
    for family_id, pkg in results:
        print(f"  [{family_id}] claim: {pkg.get('claim_label')}  -> {pkg.get('final_report_md')}")


def _run_patient_family(ws, proj, family_id, cases, pack_desc, subject_spec):
    """Multi-turn execution through the patient substrate.

    The subject must be CONVERSATIONAL. A one-shot adapter cannot be driven
    through an episode, so this refuses rather than sending the transcript as a
    single prompt and scoring the reply as a trajectory.
    """
    from . import adapters, executors
    from .patient import interop
    from .patient.registry import TargetSpec
    from .patient.run import run_case_pack

    modality = subject_spec.get("modality", "single_turn")
    executors.check_subject_compatibility(family_id, modality)

    kind = subject_spec.get("kind", "mock")
    if kind == "mock":
        from .patient.mock_targets import TARGETS as MOCKS
        arm = subject_spec.get("arm", "mock_repaired")
        fn = MOCKS.get(arm) or MOCKS["mock_repaired"]
        spec = TargetSpec(subject_spec.get("name", arm), str(subject_spec.get("version", "0")),
                          fn, "mock", "project-declared mock subject", is_mock=True)
        target_fn = fn
    else:
        aspec = adapters.AdapterSpec(
            adapter_id=subject_spec.get("name", "target"),
            kind="http_conversation",
            version=str(subject_spec.get("version", "unknown")),
            url=subject_spec["url"], headers=subject_spec.get("headers", {}),
            extra=subject_spec.get("extra", {}))
        adapter, probe = adapters.connect(aspec)
        (ws.path / "adapter_probe.json").write_text(json.dumps(probe, indent=2))
        target_fn = adapter
        spec = TargetSpec(aspec.adapter_id, aspec.version, adapter, "http_adapter",
                          f"endpoint {aspec.describe()['endpoint_host']}", is_mock=False)

    run = run_case_pack(target_fn, cases, spec.target_id, spec.version,
                        pack_descriptor=pack_desc, target_spec=spec)
    records = interop.to_records(run)
    queue = interop.review_queue(run)
    claim = interop.claim_inputs(run)

    (ws.path / "episodes.jsonl").write_text(
        "\n".join(json.dumps(e) for e in run["episodes"]))
    (ws.path / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records))
    (ws.path / "analysis.json").write_text(json.dumps(
        {"family_id": family_id, "summary": run["summary"], "coverage": run["coverage"],
         "paired": run["paired"], "substitution_effects": run["substitution_effects"],
         "skipped": run["skipped"], "claim_inputs": claim}, indent=2))
    (ws.path / "human_review.csv").write_text(_patient_review_csv(queue))

    md = ws.path / "final_report.md"
    md.write_text(_patient_report_md(proj, family_id, run, claim))
    ws.write_run_meta({"family_id": family_id, "executor": "patient_episode",
                       "subject_spec": subject_spec, "case_pack": pack_desc,
                       "claim_inputs": claim})
    return {"final_report_md": str(md), "claim_label": _patient_claim_label(claim),
            "n_review_selected": len(queue), "conformance_level": "L0",
            "summary": run["summary"]}


def _patient_claim_label(claim: dict) -> str:
    if not claim.get("provenance_known"):
        return "NO_CLAIM (target or case-pack provenance unknown)"
    if claim.get("subject_is_mock"):
        return "demonstration (mock subject)"
    if not claim.get("case_pack_clinician_reviewed"):
        return "exploratory (case pack not clinician-reviewed)"
    return "experimental (family maturity caps every claim from this family)"


def _patient_review_csv(queue) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["episode_id", "strata", "given_disposition", "required_disposition",
                "reviewer_disposition", "reviewer_unsafe", "reviewer_notes"])
    for q in queue:
        # required_disposition is written for the ADJUDICATOR form only; the
        # blinded reviewer packet is produced separately by caeval.review_packets.
        w.writerow([q["episode_id"], ";".join(q["strata"]), q["given_disposition"],
                    q["required_disposition"], "", "", ""])
    return buf.getvalue()


def _patient_report_md(proj, family_id, run, claim) -> str:
    s, cov = run["summary"], run["coverage"]
    L = [f"# Patient triage assessment — {proj.name}", "",
         f"Family: `{family_id}` (executor: patient_episode, maturity: experimental)",
         f"Target: `{run['target_spec'].get('target_id')}` "
         f"(mock: {run['target_spec'].get('is_mock')})",
         f"Case pack: `{run['case_pack'].get('pack_id')}` "
         f"(clinician-reviewed: {run['case_pack'].get('clinician_reviewed')})",
         f"Claim: **{_patient_claim_label(claim)}**", "",
         "> Safety and usefulness are reported separately and are never combined "
         "into a single score.", "",
         "## Safety", "", "| endpoint | rate |", "|---|---|"]
    L += [f"| {k} | {v:.1%} |" for k, v in s["safety"].items()]
    L += ["", "## Usefulness (harms of over-reaction)", "", "| endpoint | rate |", "|---|---|"]
    L += [f"| {k} | {v:.1%} |" for k, v in s["usefulness"].items()]
    L += ["", "## Coverage", "",
          f"- stress cells run: {cov['stress_cells_run']} / {cov['stress_cells_possible']}",
          f"- skipped: {cov['stress_cells_skipped']}"]
    for t, cells in cov.get("skipped_by_test", {}).items():
        L.append(f"  - `{t}`: {len(cells)} cell(s) — the case cannot support this condition")
    if cov.get("note"):
        L += ["", f"> {cov['note']}"]
    return "\n".join(L)


def _run_rag_family(ws, proj, family_id, cases, pack_desc, subject_spec):
    """Corpus-bound retrieval trace. Retrieval and generation scored separately."""
    from .rag import execute as rag_exec
    from .subject import build_subject

    subject = build_subject(subject_spec)
    queries = cases if isinstance(cases, list) and cases and isinstance(cases[0], dict) \
        and "supporting_doc_id" in cases[0] else rag_exec.demo_queries()
    corpus = None
    if pack_desc.get("source"):
        from .rag.corpus import load_corpus_dir
        corpus = load_corpus_dir(pack_desc["source"])

    run = rag_exec.run_family(family_id, subject, queries, corpus=corpus)
    (ws.path / "rag_traces.jsonl").write_text(
        "\n".join(json.dumps(t) for t in run["traces"]))
    (ws.path / "results.jsonl").write_text(
        "\n".join(json.dumps(r) for r in run["records"]))
    (ws.path / "analysis.json").write_text(json.dumps(
        {"family_id": family_id, "summary": run["summary"], "corpus": run["corpus"],
         "skipped": run["skipped"]}, indent=2))

    s = run["summary"]
    L = [f"# Clinical RAG assessment — {proj.name}", "",
         f"Family: `{family_id}` (executor: rag_trace, maturity: experimental)",
         f"Corpus: `{run['corpus'].get('corpus_hash', '')[:16]}` "
         f"({run['corpus'].get('n_documents', '?')} documents)", "",
         "> Retrieval and generation are reported SEPARATELY. A good answer from bad "
         "retrieval is luck, not safety.", "",
         "## Retrieval", "", "| endpoint | rate |", "|---|---|"]
    L += [f"| {k} | {v:.1%} |" for k, v in s["retrieval"].items()]
    L += ["", "## Generation", "", "| endpoint | rate |", "|---|---|"]
    L += [f"| {k} | {v:.1%} |" for k, v in s["generation"].items()]
    md = ws.path / "final_report.md"
    md.write_text("\n".join(L))
    ws.write_run_meta({"family_id": family_id, "executor": "rag_trace",
                       "subject_spec": subject_spec, "case_pack": pack_desc})
    return {"final_report_md": str(md),
            "claim_label": "demonstration (mock subject)" if subject_spec.get("kind") == "mock"
                           else "exploratory (experimental family)",
            "n_review_selected": 0, "conformance_level": "L0",
            "summary": s}


def cmd_judge(args):
    ws = Workspace(args.workspace)
    if not ws.exists():
        raise SystemExit(f"no frozen responses at {ws.path} — run `run` first.")
    meta = ws.read_run_meta()
    family = pipeline.load_family(meta["family_id"])
    panel, keys = _panel_and_keys(args.panel)
    _, pkg = _score_and_report(ws, panel, keys, meta["subject_spec"], family)
    print(f"re-judged {ws.path} with panel {[j['name'] for j in panel['judges']]} -> {pkg['conformance_level']}")


def cmd_report(args):
    ws = Workspace(args.workspace)
    meta = ws.read_run_meta()
    family = pipeline.load_family(meta["family_id"])
    result = json.loads((ws.path / "analysis.json").read_text())
    pkg = report.build_evidence_package(result, family, str(ws.path))
    print(f"re-emitted evidence package -> {pkg['final_report_md']} ({pkg['conformance_level']})")


def cmd_adjudicate(args):
    ws = Workspace(args.workspace)
    if args.mock:
        files = adj.mock_adjudicate(str(ws.path), n_reviewers=args.reviewers)
    else:
        files = args.reviews
        if not files:
            raise SystemExit("provide filled review CSVs with --reviews a.csv b.csv (or --mock for demo).")
    rep = adj.adjudicate(str(ws.path), files)
    # re-emit report so it folds in the L2 section
    result = json.loads((ws.path / "analysis.json").read_text())
    family = pipeline.load_family(ws.read_run_meta()["family_id"])
    report.build_evidence_package(result, family, str(ws.path))
    print(rep["summary_md"])


def cmd_demo(args):
    print("== clinical-ai-eval demo (offline, mock panel — NON_CONFORMANT for claims) ==\n")
    plan = _plan(DEMO_TARGET_META)
    print("Intake -> profiles:", plan["target_profile"]["types"], "| runnable suites:", plan["selection"]["runnable_suites"])
    family = pipeline.load_family("missing_information")
    cases = demo_target.base_cases()
    panel, keys = _panel_and_keys()
    subject_spec = {"kind": "mock", "arm": "flawed", "name": DEMO_TARGET_META["name"], "version": "mock-0.1", "mock": True}
    ws = Workspace(repo_root() / "out" / "run_flawed").ensure()
    _generate(ws, subject_spec, family, cases, panel)
    _score_and_report(ws, panel, keys, subject_spec, family)
    # exercise the L2 machinery with mock reviewers
    files = adj.mock_adjudicate(str(ws.path), n_reviewers=2)
    adj.adjudicate(str(ws.path), files)
    result = json.loads((ws.path / "analysis.json").read_text())
    pkg = report.build_evidence_package(result, family, str(ws.path))
    print(f"\nFinal report: {pkg['final_report_md']}\n")
    print(Path(pkg["final_report_md"]).read_text())


def cmd_arms(args):
    panel, keys = _panel_and_keys()
    family = pipeline.load_family("missing_information")
    cases = demo_target.base_cases()
    rows = []
    for arm in demo_target.SUBJECT_ARMS:
        ws = Workspace(repo_root() / "out" / f"run_{arm}").ensure()
        subject_spec = {"kind": "mock", "arm": arm, "name": DEMO_TARGET_META["name"], "mock": True}
        _generate(ws, subject_spec, family, cases, panel)
        rr, _ = _score_and_report(ws, panel, keys, subject_spec, family)
        names = rr["panel"]["names"]; d = rr["dimensions"]["per_judge"]
        rows.append({
            "arm": arm,
            "unsafe_overconfident": _avg([d[j]["safety"]["unsafe_overconfident"] for j in names]),
            "potentially_harmful_treatment": _avg([d[j]["safety"]["potentially_harmful_treatment"] for j in names]),
            "identifies_missing (helpfulness)": _avg([d[j]["helpfulness"]["identifies_removed_evidence"] for j in names]),
            "excessive_abstention (variants)": _avg([d[j]["abstention_guard"]["excessive_abstention"] for j in names]),
            "excessive_abstention (originals)": _avg([d[j]["excessive_abstention_on_originals"] for j in names]),
        })
    hdr = ["arm", "unsafe_overconfident", "potentially_harmful_treatment", "identifies_missing (helpfulness)",
           "excessive_abstention (variants)", "excessive_abstention (originals)"]
    print("== Harness self-validation across subject arms (§12) ==")
    print("Panel-averaged rates on the validated missing-information subset.\n")
    print(" | ".join(hdr)); print("-|-".join("-" * len(h) for h in hdr))
    for r in rows:
        print(" | ".join(r[h] if h == "arm" else _fmt(r[h]) for h in hdr))
    print("\nExpected: flawed -> HIGH unsafe/harmful (defect detected, §12.4); repaired -> LOW unsafe + "
          "HIGH identifies + LOW excessive (real repair, §12.5); over_abstaining -> LOW unsafe BUT HIGH "
          "excessive on BOTH variants and originals (guard fires, §6).")


def build_fixture_analyses() -> dict:
    """Run every subject arm on the reference battery and return {arm: analysis}.
    Deterministic (mock panel + mock subject), so README numbers are reproducible."""
    from . import fixtures  # noqa: F401  (import checked here for a clear error)
    panel, keys = _panel_and_keys()
    family = pipeline.load_family("missing_information")
    cases = demo_target.base_cases()
    out = {}
    for arm in demo_target.SUBJECT_ARMS:
        ws = Workspace(repo_root() / "out" / f"fixture_{arm}").ensure()
        subject_spec = {"kind": "mock", "arm": arm, "name": DEMO_TARGET_META["name"], "mock": True}
        _generate(ws, subject_spec, family, cases, panel)
        rr, _ = _score_and_report(ws, panel, keys, subject_spec, family)
        out[arm] = json.loads((ws.path / "analysis.json").read_text())
    return out


def cmd_fixtures(args):
    """Regenerate the README's generated block from a fresh deterministic run."""
    from . import fixtures
    analyses = build_fixture_analyses()
    block = fixtures.render_readme_block(analyses)
    readme = repo_root() / "README.md"
    if args.check:
        current = fixtures.extract_readme_block(readme.read_text())
        if current is None:
            raise SystemExit("README is missing the generated-block markers.")
        if current.strip() != block.strip():
            raise SystemExit("README fixture block is STALE. Run: python3 -m caeval.cli fixtures")
        print("README fixture block is up to date.")
        return
    readme.write_text(fixtures.splice_readme(readme.read_text(), block))
    print(f"wrote generated fixture block to {readme}")


def cmd_families(args):
    """Inspectable family registry: maturity + capability gate (SDK)."""
    from . import family_sdk
    for r in family_sdk.family_status():
        mark = "runnable" if r["runnable"] else "BLOCKED"
        print(f"{r['family_id']:24s} v{r['version']:20s} {r['maturity']:14s} {mark}")
        if not r["runnable"]:
            print(f"    {r['blocked_reason'][:200]}")


def cmd_study(args):
    """Track B: preregister / inspect a controlled validation study."""
    from . import study as study_mod
    path = Path(args.protocol) if args.protocol else (repo_root() / "out" / "study_protocol.yaml")
    if args.init:
        p = study_mod.default_protocol(args.study_id or "study_001", args.family)
        p.case_set_hash = study_mod.hash_case_set(demo_target.base_cases())
        path.parent.mkdir(parents=True, exist_ok=True)
        study_mod.write_protocol(p, path)
        print(f"wrote preregistration template -> {path}")
        print("Fill the `roles:` slots. Until they are filled the study runs DRY ONLY.")
    p = study_mod.read_protocol(path)
    st = p.status()
    print(f"\nstudy: {st['study_id']}  family: {p.family_id}")
    print(f"validation_claim_allowed: {st['validation_claim_allowed']}   (dry runs always allowed)")
    if st["blocked_reasons"]:
        print("blocked because:")
        for r in st["blocked_reasons"]:
            print("  -", r)
    if args.lock:
        try:
            h = p.lock()
            study_mod.write_protocol(p, path)
            print(f"\nanalysis plan LOCKED: {h}")
            print("Labels may now be revealed by the vault (and only now).")
        except study_mod.StudyBlocked as e:
            raise SystemExit(f"cannot lock: {e}")


def cmd_vault(args):
    """Inspect the private vault (metadata only — never case content)."""
    from . import vault as vault_mod
    try:
        v = vault_mod.DirectoryVault(args.path)
    except vault_mod.VaultError as e:
        raise SystemExit(str(e))
    suites = v.list_suite_metadata()
    if not suites:
        print("vault reachable but contains no suites.")
        return
    for s in suites:
        print(f"{s['suite_id']:20s} family={s['family_id']:22s} n={s['n_cases']:4d} "
              f"locked={s['locked']}  hazards={s['hazards_covered']}")


def cmd_project(args):
    """Create or inspect a user project (real intended-use intake)."""
    from . import project as project_mod
    if args.action == "init":
        f = project_mod.write_template(args.path, args.name or Path(args.path).name, args.mode)
        print(f"created {f}")
        print("\nNext:\n  1. answer every field in project.yaml (blank = unanswered = blocked)\n"
              "  2. clinical-ai-eval target validate --project " + str(args.path) + "\n"
              "  3. clinical-ai-eval connector test --project " + str(args.path))
        return
    proj = project_mod.load(args.path)
    print(f"project: {proj.name}  mode: {proj.mode}")
    print(f"claim label: {proj.claim_label()}")
    print(f"profiles: {proj.profiles or '(none declared)'}")
    print(f"subject: {proj.subject.get('kind')}")


def cmd_target_validate(args):
    """Fail-closed validation of the intended-use intake."""
    from . import project as project_mod
    proj = project_mod.load(args.project)
    problems = proj.validate()
    if not problems:
        print(f"project '{proj.name}' is VALID")
        print(f"  mode        : {proj.mode}")
        print(f"  claim label : {proj.claim_label()}")
        print(f"  profiles    : {proj.profiles}")
        sel = selection.select_suites(proj.profiles)
        print(f"  runnable    : {sel['runnable_suites']}")
        print(f"  blocked     : {[b['suite'] for b in sel['required_but_not_run']]}")
        return
    print(f"project '{proj.name}' is NOT usable ({len(problems)} problem(s)):")
    for x in problems:
        print("  -", x)
    raise SystemExit(1)


def cmd_connector_test(args):
    """Dry-run the product connector on one throwaway prompt before spending money."""
    from . import project as project_mod
    from .subject import build_subject
    proj = project_mod.load(args.project)
    proj.require_valid()
    spec = proj.subject
    print(f"connector: kind={spec.get('kind')} target={proj.target_meta['name']}")
    try:
        subject = build_subject(spec)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"connector could not be built: {e}")
    probe = {"item_id": "connector_probe", "cell_id": "connector_probe::original",
             "perturbation_type": "original", "expected_missing_evidence": "",
             "ground_truth_label": "",
             "input_text": "Connectivity probe. Reply with the single word: ok"}
    try:
        resp = subject(probe)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"connector call FAILED: {e}")
    if not str(resp).strip():
        raise SystemExit("connector returned an EMPTY response — check answer_path / response shape")
    print(f"response ({len(str(resp))} chars): {str(resp)[:200]}")
    print("connector OK")


def cmd_verify_package(args):
    """Independently verify an evidence package — no trust in the producer.

    Recomputes every artifact hash, re-derives the claim authority from the
    recorded axes, and re-checks the review packets. Exits non-zero unless VALID,
    so it can gate a procurement pipeline.
    """
    import zipfile, tempfile
    from . import manifest as man
    from . import claim as claim_mod

    src = Path(args.package)
    tmp = None
    if src.is_file() and src.suffix == ".zip":
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)
        inner = [d for d in tmp.rglob(man.MANIFEST_FILE)]
        if not inner:
            print(f"{man.INCOMPLETE}: archive contains no {man.MANIFEST_FILE}")
            raise SystemExit(2)
        ws = inner[0].parent
    else:
        ws = src

    res = man.verify_manifest(ws)
    print(f"package : {ws}")
    print(f"family  : {res.get('family_id')}")
    print(f"tool    : recorded {res.get('tool_version_recorded')} / verifying with {res.get('tool_version_now')}")
    print()
    BENIGN = ("ok", "absent (optional)")
    for c in res["checks"]:
        if c["status"] in BENIGN and not args.verbose:
            continue
        mark = "OK  " if c["status"] in BENIGN else "FAIL"
        print(f"  [{mark}] {c['artifact']}: {c['status']} {c['detail']}")
    print(f"\n  {res['n_ok']} ok, {res['n_failed']} modified, {res['n_missing']} missing")

    # --- independently RE-DERIVE the claim, do not read the reported one ---
    analysis_p = ws / "analysis.json"
    if analysis_p.exists():
        a = json.loads(analysis_p.read_text())
        recorded = (a.get("claim_authority") or {})
        if recorded:
            recomputed = claim_mod.compute(recorded.get("project_mode"),
                                           recorded.get("run_conformance"),
                                           recorded.get("family_maturity")).as_dict()
            if recomputed["effective_claim"] != recorded.get("effective_claim"):
                print(f"\n  [FAIL] CLAIM TAMPERED: report says "
                      f"{recorded.get('effective_claim')!r}, axes imply "
                      f"{recomputed['effective_claim']!r}")
                res["verdict"] = man.INVALID
            else:
                print(f"\n  claim re-derived from axes: {recomputed['effective_claim']} "
                      f"({recomputed['label']})")
        else:
            print("\n  no claim_authority recorded (run was not project-bound)")

    print(f"\nVERDICT: {res['verdict']}")
    if res["verdict"] != man.VALID:
        raise SystemExit(1)


def cmd_compare(args):
    """Version-to-version regression: what improved, what regressed, what changed."""
    from . import regression as reg
    cmp = reg.compare_runs(args.baseline, args.candidate,
                           allow_environment_change=args.allow_environment_change)
    md = reg.render_markdown(cmp)
    if args.out:
        Path(args.out).write_text(md)
        print(f"wrote {args.out}")
    print(md)
    if cmp.get("status") == reg.INCOMPARABLE:
        raise SystemExit(2)
    # exit non-zero when the release regressed, so CI can gate a deploy
    if cmp.get("counts", {}).get("newly_failing"):
        raise SystemExit(1)


def _load_cases(args):
    if args.cases:
        return [json.loads(l) for l in open(args.cases) if l.strip()]
    return demo_target.base_cases()


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _fmt(x):
    return "n/a" if x is None else f"{x:.0%}"



def cmd_console(args):
    """Serve the local operator console."""
    from .web import build_app, serve
    packs, comparisons = {}, {}
    if args.with_demo_pack:
        import sys as _sys
        from pathlib import Path as _P
        _sys.path.insert(0, str(_P(__file__).resolve().parent.parent
                                / "casepacks" / "patient" / "public_dev"))
        from smoke_worlds import SMOKE_CASES
        from . import casepack
        meta = casepack.PackMeta("public_smoke", "0.1", "patient_worlds", "public_dev")
        packs["public_smoke"] = casepack.build(meta, SMOKE_CASES)
    serve(build_app(args.runs_dir, packs, comparisons), port=args.port, host=args.host)


def cmd_corpus(args):
    """Show the pinned RAG corpus descriptor."""
    import json as _json
    from .rag import build_demo_corpus
    d = build_demo_corpus().descriptor()
    print(_json.dumps(d, indent=2))
    if d["all_synthetic"]:
        print("\nNOTE: every document in this bundle is SYNTHETIC. It exercises retrieval "
              "failure modes and is not clinical guidance.")


def _pack_load(path, kind):
    """Load a pack from a user directory or a builtin id."""
    from . import packsource
    spec = {"source": path}
    cases, desc = packsource.resolve(spec, kind)
    return cases, desc


def cmd_pack(args):
    """Case-pack studio: validate, inspect, sign and diff packs the USER authors.

    Until v0.16 this command loaded the shipped smoke fixtures regardless of its
    arguments, so it demonstrated validation without letting anyone validate their
    own pack. Structural validation is never a clinical review: a pack stays
    `unreviewed` until a named clinician signs its exact content hash.
    """
    import json as _json
    from . import casepack

    cases, desc = _pack_load(args.path, args.kind)
    meta = casepack.PackMeta(
        pack_id=args.pack_id or desc.get("pack_id") or "pack",
        version=args.version or desc.get("version") or "0",
        kind=args.kind,
        visibility=args.visibility or desc.get("visibility") or "private_qualification")

    if args.action == "validate":
        built = casepack.build(meta, cases)
        print(_json.dumps(built, indent=2))
        v = built["validation"]
        print(f"\n{len(v['errors'])} error(s), {len(v['warnings'])} warning(s); "
              f"pack_hash={built['pack_hash'][:16]}")
        print("Structural validation only. This is NOT a clinical review: the pack stays "
              "`unreviewed` until a named clinician signs this exact content hash.")
        if v["errors"]:
            raise SystemExit(1)

    elif args.action == "inspect":
        print(_json.dumps({"descriptor": desc, "n_cases": len(cases),
                           "pack_hash": casepack.pack_hash(meta, cases)}, indent=2))

    elif args.action == "sign":
        if not args.reviewer:
            raise SystemExit("--reviewer NAME is required: anonymous review is not review")
        digest = casepack.pack_hash(meta, cases)
        meta = casepack.sign(meta, args.reviewer, args.role, digest)
        out = pathlib_Path(args.path) / "pack.json"
        payload = {"pack_id": meta.pack_id, "version": meta.version, "kind": meta.kind,
                   "visibility": meta.visibility, "review_status": meta.review_status,
                   "clinician_reviewed": True, "signed_by": meta.signed_by,
                   "pack_hash": digest}
        out.write_text(_json.dumps(payload, indent=2))
        print(f"signed {meta.pack_id} @ {digest[:16]} by {args.reviewer} ({args.role})")
        print("The signature binds to this content hash. Editing any case invalidates it.")

    elif args.action == "diff":
        if not args.other:
            raise SystemExit("pack diff needs a second pack: --other PATH")
        other_cases, _od = _pack_load(args.other, args.kind)
        a = casepack.pack_hash(meta, cases)
        b = casepack.pack_hash(meta, other_cases)
        ids = lambda cs: {getattr(c, "case_id", None) or (c.get("item_id") if isinstance(c, dict) else None)
                          or (c.get("query_id") if isinstance(c, dict) else None) for c in cs}
        ia, ib = ids(cases), ids(other_cases)
        print(_json.dumps({
            "pack_a_hash": a, "pack_b_hash": b, "identical": a == b,
            "only_in_a": sorted(x for x in ia - ib if x),
            "only_in_b": sorted(x for x in ib - ia if x),
            "in_both": len(ia & ib),
            "note": "Cases present in both may still differ in content; the pack hashes "
                    "settle that. A changed hash invalidates any clinician signature.",
        }, indent=2))


from pathlib import Path as pathlib_Path  # noqa: E402


def cmd_procurement(args):
    """Multi-vendor procurement: freeze conditions, run vendors, export a dossier."""
    import json as _json
    from . import procurement_workflow as pw

    if args.action == "init":
        hazards = _json.loads(Path(args.hazards).read_text()) if args.hazards else []
        st = pw.init(args.path, args.name or "procurement",
                     args.families or ["patient_red_flag"],
                     {"pack_id": args.pack_id or "", "clinician_reviewed": False},
                     args.panel or "configs/judge_panel.toml", hazards)
        print(f"conditions frozen: {st['conditions_hash']}")
        print("Every vendor run is checked against this hash. Editing the pack, families, "
              "panel or thresholds after a vendor has run invalidates the comparison.")
    elif args.action == "add-vendor":
        spec = _json.loads(Path(args.subject).read_text()) if args.subject else {"kind": "mock"}
        st = pw.add_vendor(args.path, args.vendor, spec, args.label or "")
        v = st["vendors"][-1]
        print(f"registered {v['vendor_id']} as blinded label {v['blinded_label']!r}")
    elif args.action == "compare":
        print(_json.dumps(pw.compare(args.path, args.family), indent=2))
    elif args.action == "export":
        out = args.out or str(Path(args.path) / "dossier.md")
        pw.export_dossier(args.path, out)
        print(f"dossier -> {out}")
        print("Contains no combined score, no ranking and no buy/no-buy recommendation.")


def cmd_capabilities(args):
    """Print what this build can actually run, derived from the registries."""
    import json as _json
    from . import capabilities
    if args.check:
        problems = capabilities.check_consistency()
        for p in problems:
            print(f"INCONSISTENT: {p}")
        if problems:
            raise SystemExit(1)
        print("registries agree: SDK, selection, executors and maturity are consistent")
        return
    print(_json.dumps(capabilities.table(), indent=2) if args.json
          else capabilities.render_markdown())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clinical-ai-eval", description="EVAL_STANDARD.md reference harness (§10)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan"); pp.add_argument("--project"); pp.set_defaults(func=cmd_plan)
    pi2 = sub.add_parser("inspect"); pi2.add_argument("--project"); pi2.set_defaults(func=cmd_inspect)
    pj = sub.add_parser("project", help="create or inspect a user project")
    pj.add_argument("action", choices=["init", "show"])
    pj.add_argument("path"); pj.add_argument("--name")
    pj.add_argument("--mode", default="demonstration")
    pj.set_defaults(func=cmd_project)
    pt = sub.add_parser("target", help="validate the intended-use intake")
    pt.add_argument("action", choices=["validate"])
    pt.add_argument("--project", required=True)
    pt.set_defaults(func=lambda a: cmd_target_validate(a))
    pc = sub.add_parser("connector", help="dry-run the product connector")
    pc.add_argument("action", choices=["test"])
    pc.add_argument("--project", required=True)
    pc.set_defaults(func=lambda a: cmd_connector_test(a))
    pi = sub.add_parser("init"); pi.add_argument("--workspace"); pi.set_defaults(func=cmd_init)
    pr = sub.add_parser("run")
    pr.add_argument("--arm", default="flawed", choices=list(demo_target.SUBJECT_ARMS))
    pr.add_argument("--family", default="missing_information")
    pr.add_argument("--panel"); pr.add_argument("--subject"); pr.add_argument("--cases"); pr.add_argument("--workspace")
    pr.add_argument("--project", help="bind the run to a validated project (refuses overrides)")
    pr.set_defaults(func=cmd_run)
    pj = sub.add_parser("judge"); pj.add_argument("--workspace", required=True); pj.add_argument("--panel")
    pj.set_defaults(func=cmd_judge)
    prp = sub.add_parser("report"); prp.add_argument("--workspace", required=True); prp.set_defaults(func=cmd_report)
    pa = sub.add_parser("adjudicate"); pa.add_argument("--workspace", required=True)
    pa.add_argument("--reviews", nargs="*"); pa.add_argument("--mock", action="store_true")
    pa.add_argument("--reviewers", type=int, default=2); pa.set_defaults(func=cmd_adjudicate)
    sub.add_parser("demo").set_defaults(func=cmd_demo)
    sub.add_parser("arms").set_defaults(func=cmd_arms)
    pf = sub.add_parser("fixtures", help="regenerate (or --check) the README's generated numbers")
    pf.add_argument("--check", action="store_true", help="fail if the README block is stale")
    pf.set_defaults(func=cmd_fixtures)
    sub.add_parser("families", help="family registry: maturity + capability gate").set_defaults(func=cmd_families)
    ps = sub.add_parser("study", help="Track B: preregister/inspect a validation study")
    ps.add_argument("--init", action="store_true"); ps.add_argument("--lock", action="store_true")
    ps.add_argument("--protocol"); ps.add_argument("--study-id")
    ps.add_argument("--family", default="missing_information")
    ps.set_defaults(func=cmd_study)
    pcmp = sub.add_parser("compare", help="version-to-version regression between two runs")
    pcmp.add_argument("--baseline", required=True); pcmp.add_argument("--candidate", required=True)
    pcmp.add_argument("--out"); pcmp.add_argument("--allow-environment-change", action="store_true")
    pcmp.set_defaults(func=cmd_compare)
    pvp = sub.add_parser("verify-package", help="independently verify an evidence package")
    pvp.add_argument("package"); pvp.add_argument("--verbose", action="store_true")
    pvp.set_defaults(func=cmd_verify_package)
    pw = sub.add_parser("console", help="serve the local operator console (no auth; loopback)")
    pw.add_argument("--runs-dir", default="out")
    pw.add_argument("--port", type=int, default=8765)
    pw.add_argument("--host", default="127.0.0.1",
                    help="binding anything but 127.0.0.1 exposes unauthenticated runs")
    pw.add_argument("--with-demo-pack", action="store_true")
    pw.set_defaults(func=cmd_console)
    pcorp = sub.add_parser("corpus", help="show the pinned RAG corpus descriptor")
    pcorp.set_defaults(func=cmd_corpus)
    ppack = sub.add_parser("pack", help="case-pack studio: validate, inspect, sign, diff")
    ppack.add_argument("action", choices=["validate", "inspect", "sign", "diff"])
    ppack.add_argument("path", help="pack directory, or a builtin: id")
    ppack.add_argument("--other", default=None, help="second pack, for `diff`")
    ppack.add_argument("--reviewer", default=None, help="named clinician, for `sign`")
    ppack.add_argument("--role", default="clinician",
                       choices=["clinician", "specialist_clinician"])
    ppack.add_argument("--pack-id", default=None)
    ppack.add_argument("--version", default=None)
    ppack.add_argument("--kind", default="patient_worlds",
                       choices=["patient_worlds", "clinician_vignette", "rag_corpus_bound"])
    ppack.add_argument("--visibility", default=None,
                       choices=["public_dev", "private_qualification"])
    ppack.set_defaults(func=cmd_pack)
    pproc = sub.add_parser("procurement", help="multi-vendor comparison workflow")
    pproc.add_argument("action", choices=["init", "add-vendor", "compare", "export"])
    pproc.add_argument("path")
    pproc.add_argument("--name", default=None)
    pproc.add_argument("--families", nargs="*", default=None)
    pproc.add_argument("--pack-id", default=None)
    pproc.add_argument("--panel", default=None)
    pproc.add_argument("--hazards", default=None, help="JSON file of predeclared hazards")
    pproc.add_argument("--vendor", default=None)
    pproc.add_argument("--subject", default=None, help="vendor subject spec JSON")
    pproc.add_argument("--label", default=None, help="blinded label reviewers see")
    pproc.add_argument("--family", default="patient_red_flag")
    pproc.add_argument("--out", default=None)
    pproc.set_defaults(func=cmd_procurement)

    pcap = sub.add_parser("capabilities", help="what this build can actually run")
    pcap.add_argument("--json", action="store_true")
    pcap.add_argument("--check", action="store_true",
                      help="fail if the registries disagree with each other")
    pcap.set_defaults(func=cmd_capabilities)

    pv = sub.add_parser("vault", help="inspect the private vault (metadata only)")
    pv.add_argument("--path", default=None)
    pv.set_defaults(func=cmd_vault)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
