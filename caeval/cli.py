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
from .adapters import persistable
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


def resolve_panel_path(panel_path, project_dir=None):
    """A panel path from the CLI or project.yaml. Relative paths are tried
    against the project directory, then the current directory, then the
    installed package data, so a project works from wherever it is run and
    from an installed wheel, not only from a source checkout."""
    if not panel_path:
        return None
    path = Path(panel_path)
    if path.is_absolute():
        return path
    for base in ([Path(project_dir)] if project_dir else []) + [Path.cwd(), repo_root()]:
        if (base / path).exists():
            return base / path
    raise SystemExit(f"judge panel not found: {panel_path} (looked in the project directory, "
                     f"the current directory and {repo_root()})")


def _panel_and_keys(panel_path=None, project_dir=None):
    panel_path = resolve_panel_path(panel_path, project_dir)
    panel = pipeline.load_panel(panel_path) if panel_path else pipeline.load_panel()
    pipeline.assess_panel(panel)  # enforce >=2 DISTINCT providers BEFORE touching keys (§7, §11)
    keys = None
    if not all(j.get("mock") for j in panel["judges"]):
        from .providers import load_keys
        keys = load_keys(registry=pipeline.panel_registry(panel))
    return panel, keys


def _score_and_report(ws: Workspace, panel, keys, subject_spec, family, project=None,
                      binding=None, pack_descriptor=None):
    # Reports and evidence packages are made to be shared: never carry a
    # connector credential into them, whatever the caller passed.
    subject_spec = persistable(subject_spec)
    responses = ws.read_responses()
    scored = pipeline.score_responses(responses, panel, keys)
    result = pipeline.analyze(scored, responses, family, subject_spec, panel)
    # Run metadata originally described the CONFIGURED panel before scoring.
    # Replace that provisional conformance with the OBSERVED result so human
    # adjudication cannot upgrade an API-failed L0 run from a stale L1 flag.
    run_meta = ws.read_run_meta()
    run_meta.setdefault("panel", {})["conformance_level"] = result["panel"]["conformance_level"]
    ws.write_run_meta(run_meta)
    if project is not None:
        from . import claim as claim_mod, maturity as maturity_mod
        # All five axes. The generic path previously passed three, so once the pack
        # and target axes existed it reported NO CLAIM for every run — correct
        # fail-closed behaviour, but it was failing closed on missing wiring rather
        # than on missing evidence.
        target_desc = {"target_id": subject_spec.get("name"),
                       "is_mock": bool(subject_spec.get("mock")
                                       or subject_spec.get("kind") == "mock")}
        authority = claim_mod.compute(project.mode,
                                      result["panel"]["conformance_level"],
                                      maturity_mod.family_maturity(family),
                                      claim_mod.pack_authority(pack_descriptor or {}),
                                      claim_mod.target_provenance(target_desc))
        result["case_pack"] = pack_descriptor or {}
        result["target_provenance"] = target_desc
        result["claim_authority"] = authority.as_dict()
        result["plan_binding"] = binding or {}
        if _conditions_hash(project):
            result["conditions_hash"] = _conditions_hash(project)
            run_meta["conditions_hash"] = result["conditions_hash"]
            result["judge_config_digest"] = pipeline.execution_panel_digest(panel)
            run_meta["judge_config_digest"] = result["judge_config_digest"]
            ws.write_run_meta(run_meta)
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
        # Persisted spec is credential-free: ${ENV} references are kept, any
        # literal secret is replaced. The live spec above is what was called.
        "generated_at": utc_now_iso(), "subject_spec": persistable(subject_spec), "family_id": family["family_id"],
        "panel": {"names": [j["name"] for j in panel["judges"]], "judges": panel["judges"],
                  "conformance_level": pipeline.assess_panel(panel)["conformance_level"],
                  "all_mock": pipeline.assess_panel(panel)["all_mock"],
                  "mock_in_headline": pipeline.assess_panel(panel)["mock_in_headline"]},
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


def _conditions_hash(proj) -> str:
    return str((proj.data.get("procurement", {}) or {}).get("conditions_hash", "") or "")


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

    local_only = proj.subject.get("local_only", False)
    if type(local_only) is not bool:
        raise SystemExit("subject.local_only must be a boolean")
    if local_only:
        if (proj.subject.get("kind")!="http" or proj.subject.get("modality")!="conversation"
                or any(executors.resolve(f).executor_id!=executors.PATIENT_EPISODE for f in runnable)):
            raise SystemExit("local_only is currently supported only for patient conversational HTTP projects")
        # Refuse reuse, so a failed local run cannot leave a previous completed
        # manifest in the same workspace. No credential loading or calls yet.
        root = Path(args.workspace) if args.workspace else Path(proj.path)/"out"
        for family_id in runnable:
            dest=root/("run_"+family_id)
            if dest.exists() or dest.is_symlink():
                raise SystemExit("local_only requires a fresh output workspace")
        if root.is_symlink():
            raise SystemExit("local_only refuses a linked output workspace")
        _conversation_spec(proj.subject)

    panel, keys = _panel_and_keys(proj.data.get("panel", {}).get("config"), proj.path)

    subject_spec = dict(proj.subject)
    # Paths in project.yaml are relative to the project, not to wherever the
    # command happens to be run from.
    if subject_spec.get("responses_file") and not Path(subject_spec["responses_file"]).is_absolute():
        subject_spec["responses_file"] = str(Path(proj.path) / subject_spec["responses_file"])
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
            pkg = _run_patient_family(ws, proj, family_id, cases, pack_desc,
                                      subject_spec, panel=panel, binding=binding,
                                      conditions_hash=_conditions_hash(proj))
        elif ex.executor_id == executors.RAG_TRACE:
            pkg = _run_rag_family(ws, proj, family_id, cases, pack_desc,
                                  subject_spec, panel=panel, binding=binding,
                                  conditions_hash=_conditions_hash(proj))
        else:
            _generate(ws, subject_spec, family, cases, panel)
            actual = claim_mod.build_binding(proj, family_id,
                                             [j["name"] for j in panel["judges"]],
                                             packsource.content_hash(cases, ex.pack_kind))
            claim_mod.verify_binding(binding, actual)
            _rr, pkg = _score_and_report(ws, panel, keys, subject_spec, family,
                                         project=proj, binding=binding,
                                         pack_descriptor=pack_desc)
        results.append((family_id, pkg))

    print(f"project: {proj.name}   mode: {proj.mode}")
    for family_id, pkg in results:
        print(f"  [{family_id}] claim: {pkg.get('claim_label')}  -> {pkg.get('final_report_md')}")


def _conversation_spec(subject_spec: dict):
    """The conversational connector exactly as project.yaml describes it.

    Every mapping field is passed through: a product whose reply is not at the
    default `answer` path, or whose transcript field is not `messages`, could not
    otherwise be connected at all."""
    from . import adapters
    if subject_spec.get("kind") != "http":
        raise SystemExit(
            f"a conversational subject must be kind: http (got {subject_spec.get('kind')!r}); "
            "set modality: conversation on an http subject")
    local_only = subject_spec.get("local_only", False)
    if type(local_only) is not bool:
        raise adapters.AdapterError("subject.local_only must be boolean")
    extra = dict(subject_spec.get("extra", {}))
    if subject_spec.get("retries") is not None:
        extra["_retries"] = int(subject_spec["retries"])
    return adapters.AdapterSpec(
        adapter_id=subject_spec.get("name", "target"),
        kind="http_conversation",
        version=str(subject_spec.get("version", "unknown")),
        url=subject_spec["url"],
        headers=subject_spec.get("headers", {}),
        extra=extra,
        history_field=subject_spec.get("history_field", "messages"),
        answer_path=subject_spec.get("answer_path", "answer"),
        session_field=subject_spec.get("session_field", ""),
        timeout=subject_spec.get("timeout", 30) if local_only else int(subject_spec.get("timeout", 120)),
        local_only=local_only,
        protocol=subject_spec.get("protocol",""),
        target_name=subject_spec.get("target_name",""),
    )


def _run_patient_family(ws, proj, family_id, cases, pack_desc, subject_spec,
                        panel=None, binding=None, conditions_hash=""):
    """Multi-turn execution, then the SHARED assurance lifecycle.

    The subject must be CONVERSATIONAL. A one-shot adapter cannot be driven through
    an episode, so this refuses rather than sending the transcript as a single
    prompt and scoring the reply as a trajectory.
    """
    from . import adapters, executors, lifecycle, pipeline
    from .patient import interop
    from .patient.registry import TargetSpec
    from .patient.run import run_case_pack

    modality = subject_spec.get("modality", "single_turn")
    executors.check_subject_compatibility(family_id, modality)

    kind = subject_spec.get("kind", "mock")
    if kind == "mock":
        from .patient.mock_targets import TARGETS as MOCKS
        arm = subject_spec.get("arm", "mock_repaired")
        if arm not in MOCKS:
            raise SystemExit(
                f"unknown patient mock arm {arm!r}; known: {sorted(MOCKS)}. Refusing to "
                f"fall back: a typo would silently evaluate a different target.")
        spec = TargetSpec(subject_spec.get("name", arm), str(subject_spec.get("version", "0")),
                          MOCKS[arm], "mock", "project-declared mock subject", is_mock=True)
        target_fn = MOCKS[arm]
    else:
        aspec = _conversation_spec(subject_spec)
        if aspec.local_only:
            from .patient.session import MAX_TURNS, STRESS_TESTS
            # Budget for every possible stress episode plus connection probes.
            if sum(len(case.worlds) for case in cases)*len(STRESS_TESTS)*MAX_TURNS+4 > adapters.LOCAL_MAX_CALLS:
                raise adapters.AdapterError("local-only case pack exceeds candidate call budget")
        adapter, probe = adapters.connect(aspec)
        (Path(ws.path) / "adapter_probe.json").write_text(json.dumps(probe, indent=2))
        target_fn = adapter
        spec = TargetSpec(aspec.adapter_id, aspec.version, adapter, "http_adapter",
                          f"endpoint {aspec.describe()['endpoint_host']}", is_mock=False)

    run = run_case_pack(target_fn, cases, spec.target_id, spec.version,
                        pack_descriptor=pack_desc, target_spec=spec)
    if kind != "mock" and aspec.local_only and any(ep.get("errors") for ep in run["episodes"]):
        # An interrupted conversation is not an observed safe or unsafe answer.
        # Preserve a bounded diagnostic, but do not publish a completed package.
        (Path(ws.path)/"local_transport_failure.json").write_text(json.dumps({
            "status":"incomplete","episodes_with_transport_errors":sum(bool(ep.get("errors")) for ep in run["episodes"]),
            "decision_grade":False
        }))
        raise adapters.AdapterError("local candidate episode failed; no completed evidence package")
    from .patient.judging import SAFETY_FIELDS as PATIENT_SAFETY_FIELDS

    cells = interop.to_records(run)
    if panel:
        cells = _score_patient_cells(cells, panel)
    deterministic = [{"unit_id": q["episode_id"], "strata": q["strata"],
                      "content": _episode_transcript(run, q["episode_id"])}
                     for q in interop.review_queue(run)]
    # UNION with judge-derived triggers, computed after scoring.
    queue = lifecycle.merge_review_queue(
        deterministic, cells, PATIENT_SAFETY_FIELDS,
        content_for=lambda c: _episode_transcript(run, c["patient"]["episode_id"])
        if c.get("patient") else c.get("response_text", ""))

    summary = dict(run["summary"])
    summary["coverage"] = run["coverage"]
    return lifecycle.finalize(
        ws, project=proj, family_id=family_id, family=pipeline.load_family(family_id),
        executor_id="patient_episode", cells=cells, summary=summary,
        pack_descriptor=pack_desc, target_descriptor=run["target_spec"],
        panel=panel, review_queue=queue, binding=binding,
        conditions_hash=conditions_hash,
        mandatory_strata=lifecycle.mandatory_strata_for(PATIENT_SAFETY_FIELDS),
        artifacts={"episodes.jsonl": run["episodes"],
                   "paired.json": run["paired"],
                   "substitution_effects.json": run["substitution_effects"],
                   "coverage.json": run["coverage"]})


def _episode_transcript(run, episode_id: str) -> str:
    ep = next((e for e in run["episodes"] if e["episode_id"] == episode_id), None)
    if not ep:
        return ""
    return "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in ep["turns"])


def _score_patient_cells(cells, panel):
    """Score episodes with the CONFIGURED panel using the patient judge contract.

    The configured blinded-provider quorum, fail-closed scoring and disagreement
    are shared; only the prompt contract differs, because the one-shot rubric
    does not describe a conversation.
    """
    from .patient import judging
    # HEADLINE = BLINDED ONLY. Rubric-aware judges are defect detectors and are
    # excluded from quorum: this repo measured a +64pp cueing gap, and a cued judge
    # scoring the headline would reintroduce it in the patient family.
    blinded = [j for j in panel.get("judges", [])
               if j.get("mode", "blinded") == "blinded"]
    out = []
    for c in cells:
        labels, errors = {}, []
        for j in blinded:
            try:
                lab, _meta = judging.score_episode(j, c["judge_payload"])
                if lab is not None:
                    labels[j["name"]] = lab
                else:
                    errors.append(f"{j['name']}: unusable judge response")
            except Exception as e:  # noqa: BLE001
                errors.append(f"{j['name']}: {e!r}")
        # FAIL CLOSED: a cell without its configured distinct-provider quorum
        # remains unscored (NA), never a reassuring zero. A prespecified single
        # real judge is allowed for L1 screening; the shared lifecycle separately
        # blocks mock-contaminated panels from earning an evidence claim.
        usable = {k: v for k, v in labels.items() if isinstance(v, dict)}
        providers = {j["provider"] for j in blinded if j["name"] in usable}
        min_needed = max(1, int(panel.get("min_distinct_providers", 2)))
        out.append({**c, "panel_labels": usable, "panel_errors": errors,
                    "panel_scored": len(providers) >= min_needed})
    return out


def _run_rag_family(ws, proj, family_id, cases, pack_desc, subject_spec,
                    panel=None, binding=None, conditions_hash=""):
    """Corpus-bound retrieval trace, then the SHARED assurance lifecycle."""
    from . import lifecycle, pipeline
    from .rag import execute as rag_exec
    from .rag.probes import SCORABLE_FIELDS as RAG_SAFETY_FIELDS
    from .subject import build_subject

    subject = build_subject(subject_spec)
    queries = cases if (isinstance(cases, list) and cases
                        and isinstance(cases[0], dict)
                        and "supporting_doc_id" in cases[0]) else rag_exec.demo_queries()
    corpus = None
    if pack_desc.get("source"):
        from .rag.corpus import load_corpus_dir
        corpus = load_corpus_dir(pack_desc["source"])

    run = rag_exec.run_family(family_id, subject, queries, corpus=corpus)
    cells = run["records"]
    # A reviewer cannot judge whether an answer was supported, or whether a
    # citation was superseded, from the answer alone. The unit carries the whole
    # evidential context: question, retrieved documents and their versions, the
    # context actually shown, the citations as resolved, and why it was routed.
    def _rag_content(t):
        cites = t["citations"]
        return "\n\n".join([
            f"CLINICAL QUESTION:\n{t['query']}",
            f"CORPUS: {t['corpus_hash'][:16]}",
            f"RETRIEVED DOCUMENT IDs (in rank order): "
            f"{', '.join(t['retrieved_document_ids']) or '(none)'}",
            f"RETRIEVED CONTEXT AS SHOWN TO THE PRODUCT:\n{t['retrieved_chunks']}",
            f"PRODUCT ANSWER:\n{t['final_answer']}",
            f"CITATIONS RESOLVED AGAINST THE CORPUS: "
            f"resolved={cites.get('resolved')} unresolved={cites.get('unresolved')} "
            f"superseded={cites.get('superseded')} "
            f"support_not_verified={cites.get('unverified_support')}",
            f"ROUTED BECAUSE: "
            f"{', '.join(k for k, v in t['deterministic'].items() if v) or 'citation support unverified'}",
        ])

    deterministic = [{"unit_id": t["query_id"] + "::" + t["probe_id"],
                      "strata": sorted(k for k, v in t["deterministic"].items() if v)
                                or ["citation_support_unverified"],
                      "content": _rag_content(t)}
                     for t in run["traces"]
                     if any(t["deterministic"].values())
                     or t["citations"].get("unverified_support")]
    by_unit = {t["query_id"] + "::" + t["probe_id"]: t for t in run["traces"]}
    queue = lifecycle.merge_review_queue(
        deterministic, cells, RAG_SAFETY_FIELDS,
        content_for=lambda c: _rag_content(by_unit[c["item_id"] + "::" + c["test_id"]])
        if (c.get("item_id", "") + "::" + c.get("test_id", "")) in by_unit
        else c.get("response_text", ""))

    target_desc = {"target_id": subject_spec.get("name", "target"),
                   "version": str(subject_spec.get("version", "0")),
                   "kind": subject_spec.get("kind", "mock"),
                   "is_mock": subject_spec.get("kind") == "mock"}
    return lifecycle.finalize(
        ws, project=proj, family_id=family_id, family=pipeline.load_family(family_id),
        executor_id="rag_trace", cells=cells, summary=run["summary"],
        pack_descriptor=pack_desc, target_descriptor=target_desc,
        panel=panel, review_queue=queue, binding=binding,
        conditions_hash=conditions_hash,
        mandatory_strata=lifecycle.mandatory_strata_for(RAG_SAFETY_FIELDS),
        artifacts={"rag_traces.jsonl": run["traces"], "corpus.json": run["corpus"],
                   "skipped.json": run["skipped"]})


GENERIC_ONLY_COMMANDS = {}


def _rejudge_lifecycle(ws, meta, args):
    """Re-score a patient/RAG run from FROZEN judge inputs, without regenerating.

    This is the property the generic path has always had and the newer backends
    only claimed: `responses.jsonl` now carries the executor's judge payload, so a
    swapped panel scores exactly what the original panel saw.
    """
    import json as _json
    from . import lifecycle, pipeline

    run = Path(ws.path)
    frozen = [_json.loads(l) for l in (run / "responses.jsonl").read_text().splitlines()
              if l.strip()]
    cells = [_json.loads(l) for l in (run / "results.jsonl").read_text().splitlines()
             if l.strip()]
    analysis = _json.loads((run / "analysis.json").read_text())
    contract = frozen[0].get("judge_contract", "one_shot_v1") if frozen else "one_shot_v1"
    if contract != "patient_multiturn_v1":
        raise SystemExit(
            f"re-judging is implemented for the patient contract; this run uses "
            f"{contract!r}. The RAG backend's endpoints are deterministic and are not "
            f"produced by a judge, so there is nothing to re-score.")
    panel, _keys = _panel_and_keys(args.panel)
    pipeline.assess_panel(panel)          # >=2 distinct blinded providers, up front

    payloads = {f["unit_id"]: f.get("judge_payload") for f in frozen}
    rescored = []
    for c in cells:
        uid = c.get("item_id") or c.get("perturbation_id")
        payload = payloads.get(uid) or c.get("judge_payload")
        if payload is None:
            raise SystemExit(
                f"unit {uid!r} has no frozen judge payload; this run predates "
                f"executor-specific frozen inputs and cannot be re-judged without "
                f"regenerating it.")
        rescored.append({**c, "judge_payload": payload})
    rescored = _score_patient_cells(rescored, panel)

    from .patient.judging import SAFETY_FIELDS
    queue = lifecycle.merge_review_queue(
        [], rescored, SAFETY_FIELDS,
        content_for=lambda c: (c.get("judge_payload") or {}).get("episode_ref", ""))

    class _P:
        name = analysis.get("family_id", "run")
        mode = analysis["claim_authority"]["project_mode"]

    pkg = lifecycle.finalize(
        ws, project=_P(), family_id=analysis["family_id"],
        family=pipeline.load_family(analysis["family_id"]),
        executor_id=analysis["executor"], cells=rescored,
        summary=analysis.get("summary", {}),
        pack_descriptor=analysis.get("case_pack", {}),
        target_descriptor=analysis.get("target", {}),
        panel=panel, review_queue=queue, binding=None, artifacts={},
        mandatory_strata=lifecycle.mandatory_strata_for(SAFETY_FIELDS),
        conditions_hash=analysis.get("conditions_hash") or "")
    print(f"re-judged {ws.path} with panel "
          f"{[j['name'] for j in panel['judges']]} -> {pkg['conformance_level']}")
    return pkg


def _report_lifecycle(ws, meta):
    """Re-emit a patient/RAG evidence package from what is already on disk."""
    import json as _json
    run = Path(ws.path)
    lvl = _reemit_after_adjudication(ws, meta)
    print(f"re-emitted evidence package -> {run / 'final_report.md'} ({lvl})")
    return lvl


def _adjudicate_units(ws, meta, args):
    """L2 adjudication for the patient and RAG backends (caeval/unit_review.py)."""
    from . import unit_review

    if args.mock:
        files = unit_review.mock_reviews(ws.path)
        synthetic = True
        print(f"generated {len(files)} SYNTHETIC reviewer file(s) — these exercise the "
              f"gate and cannot pass it")
    else:
        files = args.reviews
        if not files:
            raise SystemExit(
                "provide filled review CSVs with --reviews a.csv b.csv (or --mock to "
                "exercise the path with synthetic reviewers). The blinded queue is "
                f"{ws.path / 'human_review.csv'}.")
        synthetic = False
    rep = unit_review.adjudicate(ws.path, files, synthetic=synthetic)
    print(f"adjudication: L2 gate {rep['gate_outcome']}")
    print(f"  resolutions: {rep['counts']}")
    print(f"  inter-rater agreement: {rep['inter_rater_agreement']}")
    for prob in rep["integrity_problems"]:
        print(f"  [BLOCKS L2] {prob}")

    # Re-emit the package so conformance and claim authority reflect the gate.
    final = _reemit_after_adjudication(ws, meta)
    if rep["l2_adjudication_gate_passed"] and final != "L2":
        print(f"  NOTE: the L2 gate passed but this run is {final}. L2 also requires L1 "
              f"(>=2 distinct REAL blinded providers); human review cannot substitute "
              f"for a conformant judge panel.")
    return rep


def _reemit_after_adjudication(ws, meta):
    """Rebuild analysis/report/manifest so the recorded conformance is the one the
    gate actually produced — not the one recorded before reviews existed."""
    import json as _json
    from . import lifecycle, pipeline, project as project_mod

    run = Path(ws.path)
    cells = [_json.loads(l) for l in (run / "results.jsonl").read_text().splitlines()
             if l.strip()]
    analysis = _json.loads((run / "analysis.json").read_text())
    queue = []
    man = _json.loads((run / "review_manifest.json").read_text())
    for u in man["expected_units"]:
        queue.append({"unit_id": u["unit_id"], "strata": u["strata"], "content": ""})

    class _P:
        name = analysis.get("family_id", "run")
        mode = analysis["claim_authority"]["project_mode"]

    lifecycle.finalize(
        ws, project=_P(), family_id=analysis["family_id"],
        family=pipeline.load_family(analysis["family_id"]),
        executor_id=analysis["executor"], cells=cells,
        summary=analysis.get("summary", {}),
        pack_descriptor=analysis.get("case_pack", {}),
        target_descriptor=analysis.get("target", {}),
        # Restore the panel that scored this run; passing an empty panel would
        # recompute conformance as L0 and silently erase an earned L1.
        panel={"judges": meta.get("panel_config", [])}, review_queue=queue,
        binding=None, artifacts={},
        conditions_hash=analysis.get("conditions_hash") or "")
    lvl = _json.loads((run / "analysis.json").read_text())["conformance_level"]
    print(f"  re-emitted package -> {run / 'final_report.md'} (conformance {lvl})")
    return lvl


def _require_generic_workspace(ws, command: str) -> dict:
    """`judge`, `report` and `adjudicate` still assume the one-shot workspace.

    They previously crashed with a bare KeyError on a patient or RAG workspace.
    Crashing is at least loud, but it reads as a bug rather than as a boundary, and
    a user cannot tell which. Refuse explicitly and say what is missing — and
    NEVER let a command designed for one record shape run over another, which is
    how a run gets re-scored against the wrong contract and still emits a report.
    """
    try:
        meta = ws.read_run_meta()
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"{command}: cannot read run_meta.json in {ws.path} ({e})")
    executor = meta.get("executor", "generic_paired_text")
    if executor != "generic_paired_text":
        raise SystemExit(
            f"`{command}` ({GENERIC_ONLY_COMMANDS[command]}) is implemented for the "
            f"generic_paired_text backend only; this workspace was produced by "
            f"`{executor}`.\n"
            f"Refusing to run: this command expects one-shot records "
            f"(cell_id / subject_spec / the one-shot judge contract) and would either "
            f"crash or re-score the run against a contract that does not describe it.\n"
            f"The run itself is complete and verifiable — check it with "
            f"`clinical-ai-eval verify-package --workspace {ws.path}`.\n"
            f"Per-executor judge/report/adjudicate is not implemented in this build; "
            f"L2 adjudication is therefore unreachable for `{executor}` runs.")
    return meta


def cmd_judge(args):
    ws = Workspace(args.workspace)
    if not ws.exists():
        raise SystemExit(f"no frozen responses at {ws.path} — run `run` first.")
    meta = ws.read_run_meta()
    if meta.get("executor", "generic_paired_text") != "generic_paired_text":
        return _rejudge_lifecycle(ws, meta, args)
    family = pipeline.load_family(meta["family_id"])
    panel, keys = _panel_and_keys(args.panel)
    _, pkg = _score_and_report(ws, panel, keys, meta["subject_spec"], family)
    print(f"re-judged {ws.path} with panel {[j['name'] for j in panel['judges']]} -> {pkg['conformance_level']}")


def cmd_report(args):
    ws = Workspace(args.workspace)
    meta = ws.read_run_meta()
    if meta.get("executor", "generic_paired_text") != "generic_paired_text":
        return _report_lifecycle(ws, meta)
    family = pipeline.load_family(meta["family_id"])
    result = json.loads((ws.path / "analysis.json").read_text())
    pkg = report.build_evidence_package(result, family, str(ws.path))
    print(f"re-emitted evidence package -> {pkg['final_report_md']} ({pkg['conformance_level']})")


def cmd_adjudicate(args):
    ws = Workspace(args.workspace)
    meta = ws.read_run_meta()
    executor = meta.get("executor", "generic_paired_text")
    if executor != "generic_paired_text":
        return _adjudicate_units(ws, meta, args)
    if args.mock:
        files = adj.mock_adjudicate(str(ws.path), n_reviewers=args.reviewers)
    else:
        files = args.reviews
        if not files:
            raise SystemExit("provide filled review CSVs with --reviews a.csv b.csv (or --mock for demo).")
    rep = adj.adjudicate(str(ws.path), files, consensus_file=getattr(args, "consensus", None))
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
    if spec.get("modality") == "conversation":
        # A conversational product is probed as a conversation: one exchange,
        # then repeated resets to see whether replies are stable.
        from . import adapters
        try:
            _adapter, report_ = adapters.connect(_conversation_spec(spec))
        except (adapters.AdapterError, SystemExit) as e:
            raise SystemExit(f"connector call FAILED: {e}")
        det = report_.get("determinism", {})
        print(f"conversation probe: capabilities={report_.get('capabilities')} "
              f"deterministic={det.get('deterministic')} ({det.get('distinct_replies')} distinct of {det.get('trials')})")
        if report_.get("missing_for_patient_family"):
            print(f"note: missing for the patient family: {report_['missing_for_patient_family']}. "
                  + str(det.get("note", "")))
        print("connector OK")
        return
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


def _safe_extract(zf, dest):
    """Extract a package archive without trusting its member names.

    `extractall()` honours absolute paths, `..` traversal and symlinks, so a
    hostile archive can write anywhere the process can — and this command exists
    to be pointed at packages from OTHER parties, which is exactly the untrusted
    case.
    """
    import os
    from pathlib import Path as _P
    dest = _P(dest).resolve()
    seen = set()
    for info in zf.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        # symlinks and devices: the high bits of external_attr carry st_mode
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise SystemExit(f"refusing archive: {name!r} is a symlink")
        p = _P(name)
        if p.is_absolute() or any(part == ".." for part in p.parts):
            raise SystemExit(f"refusing archive: unsafe member path {name!r}")
        target = (dest / p).resolve()
        if not str(target).startswith(str(dest) + os.sep):
            raise SystemExit(f"refusing archive: {name!r} escapes the extraction root")
        if str(target) in seen:
            raise SystemExit(f"refusing archive: duplicate member {name!r}")
        seen.add(str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src_f, open(target, "wb") as out_f:
            out_f.write(src_f.read())


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
            _safe_extract(z, tmp)
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
            recomputed = claim_mod.compute(
                recorded.get("project_mode"),
                recorded.get("run_conformance"),
                recorded.get("family_maturity"),
                recorded.get("case_pack_authority", "unknown"),
                recorded.get("target_provenance", "unknown")).as_dict()
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
                     {"pack_id": args.pack_id or "",
                      "content_hash": args.pack_hash or "",
                      "clinician_reviewed": False},
                     args.panel or "configs/judge_panel.toml", hazards)
        print(f"conditions frozen: {st['conditions_hash']}")
        print("Every vendor run is checked against this hash. Editing the pack, families, "
              "panel or thresholds after a vendor has run invalidates the comparison.")
    elif args.action == "add-vendor":
        spec = _json.loads(Path(args.subject).read_text()) if args.subject else {"kind": "mock"}
        st = pw.add_vendor(args.path, args.vendor, spec, args.label or "")
        v = st["vendors"][-1]
        print(f"registered {v['vendor_id']} as blinded label {v['blinded_label']!r}")
    elif args.action == "ingest-package":
        if not args.vendor or not args.workspace:
            raise SystemExit("procurement ingest-package requires --vendor ID and --workspace DIR")
        state = pw.ingest_package(args.path, args.vendor, args.workspace)
        print(f"verified evidence package ingested for {args.vendor}; "
              f"families now recorded: {sorted(state['results'].get(args.vendor, {}))}")
    elif args.action == "compare":
        print(_json.dumps(pw.compare(args.path, args.family), indent=2))
    elif args.action == "export":
        out = args.out or str(Path(args.path) / "dossier.md")
        pw.export_dossier(args.path, out)
        print(f"dossier -> {out}")
        print("Contains no combined score, no ranking and no buy/no-buy recommendation.")



def cmd_agent(args):
    """Export explicitly approved aggregate metadata or serve it via local stdio."""
    import sys
    from . import agent_snapshot, agent_mcp, buyer_agent
    try:
        if args.action == "export":
            if not args.out:
                raise buyer_agent.BuyerError("--out is required")
            print(json.dumps(agent_snapshot.export(args.path,args.out),indent=2))
        else:
            if args.out:
                raise buyer_agent.BuyerError("--out is only supported for export")
            agent_mcp.serve(args.path)
    except (buyer_agent.BuyerError,OSError,ValueError,RuntimeError,KeyError,TypeError):
        print("Agent export or server failed; verify the approved local input.",file=sys.stderr)
        raise SystemExit(1)


def cmd_institution(args):
    """Trusted server administrator commands; credentials never appear in argv."""
    import getpass
    from . import institution_access as access, institution_web
    try:
        if args.action == "serve":
            if not args.origin:
                raise access.AccessDenied("--origin must be the exact HTTPS origin")
            institution_web.serve(args.path, args.origin, args.port)
            return
        if args.action in ("backup-create","backup-check","backup-restore"):
            from . import institution_backup as backup
            if args.action == "backup-check":
                result = backup.check(args.path)
            else:
                if not args.destination:
                    raise access.AccessDenied("--destination is required")
                if args.action == "backup-create":
                    result = backup.create(args.path,args.destination,services_stopped=args.confirm_services_stopped)
                else:
                    result = backup.restore(args.path,args.destination,services_stopped=args.confirm_services_stopped)
        elif args.action in ("pdf-stage","pdf-replay"):
            from . import pdf_source_review as pdf
            if not args.evidence_id:
                raise access.AccessDenied("--evidence-id required")
            result=pdf.stage(args.path,args.evidence_id,services_stopped=args.confirm_services_stopped,confirmed=args.confirm_pdf_extraction,replay=args.action=="pdf-replay")
        elif args.action in ("retention-preview","retention-apply"):
            from . import institution_retention as retention
            if args.action=="retention-preview":
                result=retention.preview(args.path,services_stopped=args.confirm_services_stopped,as_of=args.as_of)
            else:
                if args.as_of is None or not args.approved_digest:
                    raise access.AccessDenied("--as-of and --approved-digest are required")
                result=retention.apply(args.path,args.as_of,args.approved_digest,services_stopped=args.confirm_services_stopped,confirmed=args.confirm_deletion)
        elif args.action == "account-create":
            if not args.user_id or not args.role:
                raise access.AccessDenied("--user-id and --role are required")
            pw = getpass.getpass("New account password: ")
            if pw != getpass.getpass("Repeat password: "):
                raise access.AccessDenied("passwords do not match")
            result = access.provision(args.path,args.user_id,args.role,pw)
        elif args.action == "account-disable":
            if not args.user_id:
                raise access.AccessDenied("--user-id is required")
            access.disable(args.path,args.user_id)
            result = {"disabled":args.user_id}
        elif args.action == "review-assign":
            if not all((args.user_id,args.job_id,args.family_id)):
                raise access.AccessDenied("--user-id, --job-id and --family-id are required")
            result = access.assign(args.path,args.user_id,args.job_id,args.family_id)
        else:
            raise access.AccessDenied("unknown institutional action")
        print(json.dumps({"ok":True,"result":result},indent=2))
    except (access.AccessDenied,OSError,ValueError,RuntimeError,KeyError,TypeError,RecursionError) as exc:
        print(json.dumps({"ok":False,"error":str(exc)},indent=2))
        raise SystemExit(1)


def cmd_buyer(args):
    """Local-only, machine-readable buyer evidence workspace for agents and humans."""
    import json as _json
    from . import buyer_agent as buyer

    try:
        if args.action not in ("contract","candidate-openapi","citation-trace-openapi","conversation-openapi") and not args.path:
            raise buyer.BuyerError("buyer workspace PATH is required")
        if args.action == "contract":
            result = buyer.contract()
        elif args.action == "candidate-openapi":
            from . import candidate_protocol
            result = candidate_protocol.openapi()
        elif args.action == "citation-trace-openapi":
            from . import citation_trace_protocol
            result = citation_trace_protocol.openapi()
        elif args.action == "conversation-openapi":
            from . import conversation_protocol
            result = conversation_protocol.openapi()
        elif args.action == "init":
            result = buyer.init(args.path, args.name or "",
                                args.intended_use or "", args.setting or "",
                                args.jurisdiction or "")
        elif args.action == "vendor-add":
            result = buyer.add_vendor(args.path, args.vendor_id or "", args.name or "")
        elif args.action == "evidence-add":
            result = buyer.add_evidence(args.path, args.vendor_id or "",
                                        args.kind or "", args.file or "",
                                        args.title or "")
        elif args.action in ("questionnaire-template", "questionnaire-import"):
            from . import buyer_questionnaire as questionnaire
            if not args.vendor_id:
                raise buyer.BuyerError("--vendor-id is required")
            if args.action == "questionnaire-template":
                result = questionnaire.template(args.path, args.vendor_id)
            else:
                if not args.file:
                    raise buyer.BuyerError("--file is required for questionnaire-import")
                result = questionnaire.import_file(args.path, args.vendor_id, args.file)
        elif args.action in ("plan-preview", "plan-show"):
            from . import buyer_plan
            if args.action == "plan-preview":
                if not args.file:
                    raise buyer.BuyerError("--file is required for plan-preview")
                result = buyer_plan.preview_file(args.path, args.file)
            else:
                if not args.plan_id:
                    raise buyer.BuyerError("--plan-id is required for plan-show")
                result = buyer_plan.load(args.path, args.plan_id)
        elif args.action in ("manual-packs", "manual-prepare", "manual-template", "manual-import", "manual-responses"):
            from . import buyer_manual
            if args.action == "manual-packs":
                result = {"pack_ids": buyer_manual.packs(args.path)}
            elif args.action == "manual-prepare":
                if not args.plan_id or not args.pack_id:
                    raise buyer.BuyerError("--plan-id and --pack-id are required")
                result = buyer_manual.prepare(args.path, args.plan_id, args.pack_id)
            elif args.action == "manual-template":
                if not args.exchange_id:
                    raise buyer.BuyerError("--exchange-id is required")
                result = buyer_manual.template(args.path, args.exchange_id)
            elif args.action == "manual-import":
                if not args.file:
                    raise buyer.BuyerError("--file is required")
                result = buyer_manual.import_file(args.path, args.file)
            else:
                if not args.submission_id:
                    raise buyer.BuyerError("--submission-id is required")
                result = buyer_manual.responses(args.path, args.submission_id)
        elif args.action in ("connector-list", "collection-queue", "collection-list", "collection-status", "collection-cancel", "collection-execute"):
            from . import buyer_connector as connector
            if args.action == "connector-list":
                result = {"connectors": connector.connectors(args.path)}
            elif args.action == "collection-queue":
                result = connector.queue(args.path, args.exchange_id or "", args.connector_id or "")
            elif args.action == "collection-list":
                result = connector.list_collections(args.path)
            elif args.action == "collection-status":
                result = connector.status(args.path, args.collection_id or "")
            elif args.action == "collection-cancel":
                result = connector.cancel(args.path, args.collection_id or "")
            else:
                result = connector.execute(args.path, args.collection_id or "",
                                           args.approve_binding or "", args.operator or "")
                if result["state"] != "complete_unscored":
                    print(_json.dumps({"ok": False, "result": result}, indent=2))
                    raise SystemExit(1)
        elif args.action in ("job-enqueue", "job-list", "job-status", "job-cancel", "job-execute"):
            from . import buyer_jobs
            if args.action == "job-list":
                result = buyer_jobs.list_jobs(args.path)
            elif args.action == "job-enqueue":
                result = buyer_jobs.enqueue(args.path, args.assessment_id or "")
            elif args.action == "job-status":
                result = buyer_jobs.status(args.path, args.job_id or "")
            elif args.action == "job-cancel":
                result = buyer_jobs.cancel(args.path, args.job_id or "")
            else:
                result = buyer_jobs.execute(args.path, args.job_id or "",
                                            allow_model_calls=args.allow_model_calls)
                if result["status"] != "completed":
                    print(_json.dumps({"ok": False, "result": result}, indent=2))
                    raise SystemExit(1)
        elif args.action in ("review-template", "review-submit", "review-inventory"):
            from . import buyer_physician_review as reviews
            if args.action == "review-submit":
                if not args.file:
                    raise buyer.BuyerError("--file is required")
                result = reviews.submit_file(args.path, args.file)
            elif args.action == "review-template":
                result = reviews.template(args.path, args.job_id or "", args.family_id or "")
            else:
                result = reviews.inventory(args.path, args.job_id or "", args.family_id or "")
        elif args.action in ("assessment-panels", "assessment-prepare", "assessment-check"):
            from . import buyer_assessment
            if args.action == "assessment-panels":
                result = {"panel_ids": buyer_assessment.panels(args.path)}
            elif args.action == "assessment-prepare":
                if not args.file:
                    raise buyer.BuyerError("--file is required for assessment-prepare")
                result = buyer_assessment.prepare_file(args.path, args.file)
            else:
                if not args.assessment_id:
                    raise buyer.BuyerError("--assessment-id is required")
                result = buyer_assessment.check(args.path, args.assessment_id)
        elif args.action in ("rehearsal-run", "rehearsal-show"):
            from . import buyer_rehearsal
            if args.action == "rehearsal-run":
                if not args.plan_id:
                    raise buyer.BuyerError("--plan-id is required for rehearsal-run")
                result = buyer_rehearsal.run(args.path, args.plan_id)
            else:
                if not args.job_id:
                    raise buyer.BuyerError("--job-id is required for rehearsal-show")
                result = buyer_rehearsal.load(args.path, args.job_id)
            if result["status"] in ("failed", "invalid"):
                print(_json.dumps({"ok": False, "result": result}, indent=2))
                raise SystemExit(1)
        elif args.action == "research":
            from . import buyer_research
            result = buyer_research.overview(args.path)
        elif args.action == "serve":
            from . import buyer_web
            buyer_web.serve(args.path, port=args.port, open_browser=not args.no_browser)
            return
        elif args.action == "status":
            result = buyer.status(args.path)
        elif args.action == "dossier":
            if not args.out:
                raise buyer.BuyerError("--out is required for dossier")
            result = buyer.dossier(args.path, args.out)
        else:
            raise buyer.BuyerError("unknown buyer command")
        print(_json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    except (buyer.BuyerError, FileNotFoundError, PermissionError, OSError) as exc:
        print(_json.dumps({"ok": False, "error": {"type": type(exc).__name__,
                                                   "message": str(exc)}},
                          ensure_ascii=False, indent=2))
        raise SystemExit(1)


def cmd_reference_actions(args):
    """Prepare/score offline, or explicitly run a bounded real provider."""
    from . import reference_actions as ra
    pack = ra.load_json(args.cases)
    if pathlib_Path(args.out).resolve() == pathlib_Path(args.cases).resolve():
        raise SystemExit("--out may not overwrite the clinician reference pack")
    if args.action == "prepare":
        rows = ra.make_requests(pack, repeats=args.repeats)
        ra.write_jsonl(args.out, rows)
        print(f"Prepared {len(rows)} blinded action requests -> {args.out}; "
              "reference sets are NOT included in model-facing requests.")
        return
    if args.action == "run":
        if args.max_calls is None or not args.provider or not args.model:
            raise SystemExit("run requires --provider, --model, and --max-calls")
        from . import reference_action_runner as runner
        summary = runner.run(
            pack, args.out, provider=args.provider, model=args.model,
            max_calls=args.max_calls, repeats=args.repeats,
            max_tokens=args.max_tokens, high=args.high,
            confirm_remote=args.confirm_send_to_provider)
        print(json.dumps(summary, indent=2))
        if summary["remaining_incomplete"]:
            raise SystemExit("Provider run incomplete: retry explicitly with "
                             "the same --out, model and casepack; no pass claim")
        return
    if args.action == "compare":
        if not args.baseline or not args.candidate:
            raise SystemExit("reference-actions compare requires --baseline and --candidate JSONL")
        from . import reference_action_regression as regression
        result = regression.compare(
            pack, ra.read_jsonl(args.baseline), ra.read_jsonl(args.candidate),
            repeats=args.repeats)
        pathlib_Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib_Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True))
        print(regression.render_markdown(result))
        if result["status"] != "COMPLETE_EXPLORATORY_COMPARISON":
            raise SystemExit("Reference-action comparison INCOMPLETE: never infer "
                             f"no regression from missing responses; inspect {args.out}")
        return
    if not args.responses:
        raise SystemExit("reference-actions score requires --responses JSONL")
    rows = ra.read_jsonl(args.responses)
    report = ra.score_pack(pack, rows, repeats=args.repeats)
    pathlib_Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib_Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps({"coverage": report["coverage"],
                      "safety": report["safety"],
                      "usefulness": report["usefulness"],
                      "counterfactual": {k: v for k, v in report["counterfactual"].items()
                                         if k != "rows"},
                      "repeatability": report["repeatability"],
                      "claim": report["conformance"]}, indent=2))
    if report["coverage"]["n_invalid"] or report["coverage"]["n_missing"]:
        raise SystemExit("Incomplete or invalid model responses: no pass claim; "
                         f"inspect {args.out}")


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


def _model_entries(args):
    """(role, entry, registry) for every real model a run would call."""
    from . import llm
    from . import project as project_mod
    out = []
    panel = pipeline.load_panel(args.panel) if args.panel or not args.project else None
    if args.project:
        proj = project_mod.load(args.project)
        spec = proj.subject
        kind = spec.get("kind")
        if kind in ("llm", "openai", "anthropic", "xai", "google"):
            registry = llm.provider_registry({"providers": spec.get("providers") or {}})
            out.append(("subject", {"provider": spec.get("provider") if kind == "llm" else kind,
                                    "model": spec.get("model"),
                                    "reasoning_effort": spec.get("reasoning_effort", "high")}, registry))
        panel_path = (proj.data.get("panel") or {}).get("config")
        if not args.panel and panel_path:
            panel = pipeline.load_panel(resolve_panel_path(panel_path, proj.path))
    if panel:
        registry = pipeline.panel_registry(panel)
        for j in panel["judges"]:
            if not j.get("mock"):
                out.append((f"judge:{j['name']}", j, registry))
    return out, panel


def cmd_models(args):
    """Which real models a run would call, checked offline (`check`) or against
    each provider's model list (`list`, no tokens generated)."""
    from . import llm, providers
    entries, panel = _model_entries(args)
    if panel is not None:
        mocks = [j["name"] for j in panel["judges"] if j.get("mock")]
        if mocks:
            print(f"mock judges (offline, no provider): {', '.join(mocks)}")
    if not entries:
        print("No real models configured: this run makes no paid calls.")
        return
    problems = []
    print(f"{'role':30} {'provider/model':46} {'api':24} {'effort':16} key")
    for role, e, registry in entries:
        try:
            spec = llm.resolve_provider(e["provider"], registry)
        except llm.ProviderConfigError as exc:
            problems.append(f"{role}: {exc}")
            continue
        effort = str(e.get("reasoning_effort") or "high")
        if effort not in llm.EFFORTS_BY_API[spec["api"]]:
            problems.append(f"{role}: reasoning_effort {effort!r} not valid for {spec['api']}")
        print(f"{role:30} {(str(e['provider']) + '/' + str(e['model'])):46} {spec['api']:24} {effort:16} "
              f"{spec.get('api_key_env') or '-'}")
    if args.action == "list":
        missing = []
        seen = {}
        for role, e, registry in entries:
            if e["provider"] not in seen:
                try:
                    seen[e["provider"]] = llm.list_models(e["provider"], providers.load_keys(registry=registry), registry)
                except Exception as exc:  # noqa: BLE001  no listing endpoint, key, network
                    seen[e["provider"]] = None
                    problems.append(f"{e['provider']}: could not list models, so {e['model']} is unverified "
                                    f"({type(exc).__name__}: {str(exc)[:200]})")
            ids = seen[e["provider"]]
            if ids is not None and e["model"] not in ids:
                missing.append(f"{e['provider']}/{e['model']}")
        for prov, ids in seen.items():
            if ids:
                shown = [i for i in ids if args.grep.lower() in i.lower()]
                print(f"\n{prov}: {len(ids)} models offered" + (f", {len(shown)} matching {args.grep!r}" if args.grep else ""))
                for i in shown[:200]:
                    print(f"  {i}")
        if missing:
            problems.append("not offered to this key: " + ", ".join(missing))
    for p in problems:
        print(f"ERROR: {p}")
    if problems:
        raise SystemExit(1)
    print("\nOK. `models check` is offline; `models list` asks each provider which IDs your key can use "
          "(no tokens generated). `connector test` then makes one real call to the subject.")


def cmd_prompts(args):
    """Write the exact prompts a run will send, for `subject.kind: manual`.

    For systems that cannot be called programmatically: paste each prompt into the
    system, put each answer in the responses file under the same key, then `run`.
    The prompt file contains only the case text the system would receive; which
    fact was changed is never written into it."""
    from . import executors, packsource
    from . import project as project_mod
    proj = project_mod.load(args.project)
    proj.require_valid()
    sel = selection.select_suites(proj.profiles)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    responses_file = proj.subject.get("responses_file")
    total = 0
    combined: dict = {}
    for family_id in sel["runnable_suites"]:
        ex = executors.resolve(family_id)
        if ex.executor_id != executors.GENERIC_PAIRED_TEXT:
            print(f"  [{family_id}] skipped: manual responses apply to single-turn text families only")
            continue
        cases, _desc = packsource.resolve(proj.data.get("case_pack", {}), ex.pack_kind)
        prompts: dict = {}

        def _capture(record):
            prompts[record.get("cell_id") or f"{record.get('item_id')}::{record.get('perturbation_type')}"] = \
                record["input_text"]
            return "(placeholder)"

        pipeline.generate_responses(_capture, pipeline.load_family(family_id), cases)
        (out_dir / f"prompts_{family_id}.json").write_text(json.dumps(prompts, indent=2, ensure_ascii=False))
        combined.update({k: "" for k in prompts})
        total += len(prompts)
        print(f"  [{family_id}] {len(prompts)} prompts -> {out_dir / f'prompts_{family_id}.json'}")
    template = out_dir / "responses_template.json"
    template.write_text(json.dumps(combined, indent=2))
    print(f"{total} prompts. Fill {template} (same keys, the system's answer as the value) and save it "
          f"as {responses_file or 'the file named in subject.responses_file'}; then `run --project`.")


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
    ppr = sub.add_parser("prompts", help="write the prompts a run will send (for subject.kind: manual)")
    ppr.add_argument("--project", required=True); ppr.add_argument("--out", required=True)
    ppr.set_defaults(func=cmd_prompts)
    pmod = sub.add_parser("models", help="check (offline) or list (free) the real models a run would call")
    pmod.add_argument("action", choices=["check", "list"])
    pmod.add_argument("--project"); pmod.add_argument("--panel")
    pmod.add_argument("--grep", default="", help="with list: only show IDs containing this text")
    pmod.set_defaults(func=cmd_models)
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
    pa.add_argument("--reviews", nargs="*")
    pa.add_argument("--consensus", help="post-independent consensus CSV for tied cells")
    pa.add_argument("--mock", action="store_true")
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
    px = sub.add_parser("verify-experimental-evidence", help="replay experimental research evidence offline")
    px.add_argument("package")
    px.add_argument("--expected-digest", help="independently communicated package digest")
    def run_experimental_evidence(args):
        from . import experimental_evidence
        return experimental_evidence.cli(args)
    px.set_defaults(func=run_experimental_evidence)
    from . import project_experimental_cli
    project_experimental_cli.configure(sub.add_parser("experimental-project", help="project-bound experimental research execution (offline)"))
    pfhir = sub.add_parser("fhir-readiness", help="synthetic FHIR R4 extraction assessment and offline replay")
    fhir_sub=pfhir.add_subparsers(dest="action",required=True)
    q=fhir_sub.add_parser("prepare")
    q.add_argument("--target-name",required=True)
    q.add_argument("--target-version",required=True)
    q=fhir_sub.add_parser("assess")
    q.add_argument("--plan",required=True)
    q.add_argument("--responses",required=True)
    q=fhir_sub.add_parser("verify")
    q.add_argument("evidence")
    q.add_argument("--expected-digest")
    def run_fhir(args):
        from . import fhir_readiness
        return fhir_readiness.cli(args)
    pfhir.set_defaults(func=run_fhir)
    phl7 = sub.add_parser("hl7-readiness", help="synthetic HL7 v2.5.1 ORU_R01 extraction assessment and offline replay")
    hl7_sub=phl7.add_subparsers(dest="action",required=True)
    q=hl7_sub.add_parser("prepare")
    q.add_argument("--target-name",required=True)
    q.add_argument("--target-version",required=True)
    q=hl7_sub.add_parser("assess")
    q.add_argument("--plan",required=True)
    q.add_argument("--responses",required=True)
    q=hl7_sub.add_parser("verify")
    q.add_argument("evidence")
    q.add_argument("--expected-digest")
    def run_hl7(args):
        from . import hl7_readiness
        return hl7_readiness.cli(args)
    phl7.set_defaults(func=run_hl7)
    from . import interoperability_protocol
    pinter=sub.add_parser("interoperability-collect",help="collect fixed synthetic FHIR or HL7 probes through approved adapters")
    pinter.add_argument("action",choices=["openapi","preview","run"])
    pinter.add_argument("path",nargs="?")
    pinter.add_argument("--plan-id")
    pinter.add_argument("--connector-id")
    pinter.add_argument("--kind",choices=["fhir","hl7"])
    pinter.add_argument("--approved-digest")
    pinter.add_argument("--confirm-candidate-calls",action="store_true")
    pinter.set_defaults(func=interoperability_protocol.cli)
    from . import image_text_readiness
    pimage=sub.add_parser("image-text-readiness",help="fixed synthetic image and text extraction assay")
    pimage.add_argument("action",choices=["openapi","prepare","assess","verify","preview","run"])
    pimage.add_argument("path",nargs="?")
    for key in ("target-name","target-version","plan","responses","evidence","expected-digest",
                "plan-id","connector-id","approved-digest"):
        pimage.add_argument("--"+key)
    pimage.add_argument("--confirm-candidate-calls",action="store_true")
    pimage.set_defaults(func=image_text_readiness.cli)

    from . import diagnostic_imaging
    pdiag = sub.add_parser("diagnostic-imaging", help="offline exploratory diagnostic imaging study")
    diagnostic_imaging.configure(pdiag)

    from . import imaging_transport
    ptransport = sub.add_parser("imaging-transport", help="approved local exact-asset imaging collection")
    imaging_transport.configure(ptransport)

    from . import unified_assessment
    punified = sub.add_parser("unified-assessment", help="verify portable administrative programme coverage")
    unified_assessment.configure(punified)

    from . import institution_rag_trace
    pragtrace = sub.add_parser("verify-rag-rehearsal", help="replay private synthetic RAG trace evidence offline")
    institution_rag_trace.configure(pragtrace)

    from . import rag_grounding
    pgrounding = sub.add_parser("rag-grounding", help="verify controlled RAG grounding evidence")
    rag_grounding.configure(pgrounding)

    from . import rag_collection
    prcollection = sub.add_parser("rag-collection", help="approved local RAG trace collection and verification")
    rag_collection.configure(prcollection)

    from . import rag_paired_analysis
    pcomparison = sub.add_parser("rag-comparison", help="verify paired RAG research comparison")
    rag_paired_analysis.configure(pcomparison)

    from . import matched_version_regression
    pversionregression = sub.add_parser("verify-matched-regression", help="replay matched historical version research")
    matched_version_regression.configure(pversionregression)

    from . import hospital_referral
    preferral = sub.add_parser("verify-hospital-referral", help="verify restricted administrative hospital gate evidence")
    hospital_referral.configure(preferral)
    from . import portable_calibration
    pcal=sub.add_parser("verify-cohort-calibration",help="offline replay of a label-only validation cohort export")
    pcal.add_argument("package")
    pcal.add_argument("--expected-digest")
    pcal.set_defaults(func=portable_calibration.cli)
    pcd = sub.add_parser("verify-committee-dossier", help="verify frozen portable committee evidence offline")
    pcd.add_argument("dossier")
    pcd.add_argument("--expected-digest", help="independently communicated dossier digest")
    pcd.add_argument("--canonical-package", action="append", default=[], help="explicit local canonical package directory (repeatable)")
    def run_committee_dossier(args):
        from . import committee_portable
        return committee_portable.cli(args)
    pcd.set_defaults(func=run_committee_dossier)
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
    pproc.add_argument("action", choices=["init", "add-vendor", "ingest-package", "compare", "export"])
    pproc.add_argument("path")
    pproc.add_argument("--name", default=None)
    pproc.add_argument("--families", nargs="*", default=None)
    pproc.add_argument("--pack-id", default=None)
    pproc.add_argument("--pack-hash", default=None,
                       help="SHA-256/content hash of the exact frozen case pack")
    pproc.add_argument("--panel", default=None)
    pproc.add_argument("--hazards", default=None, help="JSON file of predeclared hazards")
    pproc.add_argument("--vendor", default=None)
    pproc.add_argument("--subject", default=None, help="vendor subject spec JSON")
    pproc.add_argument("--workspace", default=None, help="verified run workspace (ingest-package)")
    pproc.add_argument("--label", default=None, help="blinded label reviewers see")
    pproc.add_argument("--family", default="patient_red_flag")
    pproc.add_argument("--out", default=None)
    pproc.set_defaults(func=cmd_procurement)

    pb = sub.add_parser("buyer", help="local-only buyer workspace (JSON for CLI agents)")
    pb.add_argument("action", choices=["contract", "candidate-openapi", "citation-trace-openapi", "conversation-openapi", "init", "vendor-add", "evidence-add", "status", "dossier", "serve", "questionnaire-template", "questionnaire-import", "research", "plan-preview", "plan-show", "rehearsal-run", "rehearsal-show", "manual-packs", "manual-prepare", "manual-template", "manual-import", "manual-responses", "assessment-panels", "assessment-prepare", "assessment-check", "job-enqueue", "job-list", "job-status", "job-cancel", "job-execute", "review-template", "review-submit", "review-inventory", "connector-list", "collection-queue", "collection-list", "collection-status", "collection-cancel", "collection-execute"])
    pb.add_argument("--plan-id", help="content-addressed draft scope plan identifier")
    pb.add_argument("--job-id", help="saved synthetic rehearsal identifier")
    pb.add_argument("--pack-id", help="operator-staged approved-packs directory identifier")
    pb.add_argument("--exchange-id", help="manual prompt exchange digest")
    pb.add_argument("--connector-id", help="administrator-staged local candidate connector")
    pb.add_argument("--collection-id", help="saved candidate collection job")
    pb.add_argument("--approve-binding", help="explicit approval of exact queued collection binding hash")
    pb.add_argument("--operator", help="operator responsible for collection")
    pb.add_argument("--submission-id", help="manual response submission digest")
    pb.add_argument("--assessment-id", help="prepared assessment content digest")
    pb.add_argument("--family-id", help="canonical family identifier for review")
    pb.add_argument("--allow-model-calls", action="store_true", help="explicitly authorize the staged real judge panel for this one job")
    pb.add_argument("path", nargs="?", help="local buyer workspace directory (except contract)")
    pb.add_argument("--name", help="project name on init, vendor name on vendor-add")
    pb.add_argument("--intended-use", default="", help="planned clinical/administrative use")
    pb.add_argument("--setting", default="", help="care setting")
    pb.add_argument("--jurisdiction", default="", help="e.g. SE, EU, UK")
    pb.add_argument("--vendor-id", default=None)
    pb.add_argument("--kind", default=None, help="evidence kind (see buyer contract)")
    pb.add_argument("--file", default=None, help="local evidence document to copy privately")
    pb.add_argument("--title", default=None)
    pb.add_argument("--out", default=None, help="new dossier path; never overwrites")
    pb.add_argument("--port", type=int, default=0, help="local browser port; 0 picks a free port")
    pb.add_argument("--no-browser", action="store_true", help="do not automatically open the browser")
    pb.set_defaults(func=cmd_buyer)

    pag = sub.add_parser("agent",help="explicit aggregate snapshot export and read-only local MCP")
    pag.add_argument("action",choices=["export","mcp"])
    pag.add_argument("path",help="buyer workspace for export; approved snapshot file for MCP")
    pag.add_argument("--out",help="new aggregate snapshot JSON file; export only")
    pag.set_defaults(func=cmd_agent)

    pin = sub.add_parser("institution", help="trusted administrator: isolated institutional browser and account provisioning")
    pin.add_argument("action", choices=["serve","account-create","account-disable","review-assign","backup-create","backup-check","backup-restore","retention-preview","retention-apply","pdf-stage","pdf-replay"])
    pin.add_argument("path", help="one organisation-owned buyer workspace")
    pin.add_argument("--user-id")
    pin.add_argument("--role", choices=["admin","operator","observer","reviewer","vendor"])
    pin.add_argument("--job-id")
    pin.add_argument("--family-id")
    pin.add_argument("--origin", help="exact HTTPS origin, TLS handled by approved proxy")
    pin.add_argument("--port", type=int, default=8767)
    pin.add_argument("--destination", help="new approved local backup or restored workspace directory")
    pin.add_argument("--confirm-services-stopped", action="store_true", help="confirm gateway and all evaluation workers are stopped before backup or restore")
    pin.add_argument("--as-of",type=int,help="approved retention preview Unix timestamp")
    pin.add_argument("--approved-digest",help="exact retention preview digest")
    pin.add_argument("--confirm-deletion",action="store_true",help="explicitly approve previewed maintenance deletion")
    pin.add_argument("--evidence-id",help="registered original PDF evidence identifier")
    pin.add_argument("--confirm-pdf-extraction",action="store_true",help="confirm isolated, approved local PDF extraction")
    pin.set_defaults(func=cmd_institution)

    pra = sub.add_parser(
        "reference-actions",
        help="offline clinical reference actions, paired fact changes and repeatability")
    pra.add_argument("action", choices=["prepare", "run", "score", "compare"])
    pra.add_argument("--cases", required=True, help="clinician-defined reference-action JSON pack")
    pra.add_argument("--responses", help="structured model responses JSONL (score mode)")
    pra.add_argument("--baseline", help="baseline model responses JSONL (compare mode)")
    pra.add_argument("--candidate", help="candidate model responses JSONL (compare mode)")
    pra.add_argument("--out", required=True, help="requests JSONL, provider results JSONL or report JSON")
    pra.add_argument("--repeats", type=int, default=1)
    pra.add_argument("--provider", choices=["openai", "anthropic", "google", "xai"],
                     help="provider to call (run mode only)")
    pra.add_argument("--model", help="exact provider model identifier (run mode only)")
    pra.add_argument("--max-calls", type=int,
                     help="REQUIRED hard maximum paid requests for this run invocation")
    pra.add_argument("--max-tokens", type=int, default=800)
    pra.add_argument("--high", action="store_true",
                     help="opt into provider high reasoning; may be more costly")
    pra.add_argument("--confirm-send-to-provider", action="store_true",
                     help="explicitly authorize sending all case prompts to the chosen provider")
    pra.set_defaults(func=cmd_reference_actions)

    from . import evaluator_service
    peval = sub.add_parser("evaluator-api", help="scoped institutional request and status service")
    peval.add_argument("action",choices=["openapi","issue","revoke","inventory","link","approve-queue","serve","verify-receipt","package-preview","package-approve"])
    peval.add_argument("path",nargs="?")
    peval.add_argument("--owner")
    peval.add_argument("--assessment-id")
    peval.add_argument("--ttl",type=int,default=3600)
    peval.add_argument("--out",help="new private bearer token file outside the workspace")
    peval.add_argument("--grant-id")
    peval.add_argument("--request-id")
    peval.add_argument("--job-id")
    peval.add_argument("--port",type=int,default=8771)
    peval.add_argument("--confirm",action="store_true",help="explicit approval of grant or existing job linkage")
    peval.add_argument("--file")
    peval.add_argument("--expected-digest")
    peval.add_argument("--family")
    peval.set_defaults(func=evaluator_service.cli)

    from . import evaluator_mcp
    pmachine = sub.add_parser("evaluator-mcp",help="scoped machine client over stdio MCP")
    pmachine.add_argument("--port",type=int,default=8771)
    pmachine.add_argument("--token-file",required=True)
    pmachine.set_defaults(func=evaluator_mcp.cli)

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
