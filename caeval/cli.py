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


def _score_and_report(ws: Workspace, panel, keys, subject_spec, family):
    responses = ws.read_responses()
    scored = pipeline.score_responses(responses, panel, keys)
    result = pipeline.analyze(scored, responses, family, subject_spec, panel)
    (ws.path / "analysis.json").write_text(json.dumps({k: v for k, v in result.items() if k != "_response_rows"}))
    pkg = report.build_evidence_package(result, family, str(ws.path))
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
    plan = _plan(DEMO_TARGET_META)
    out = repo_root() / "out"; out.mkdir(exist_ok=True)
    intake_mod.write_eval_plan(plan, str(out / "eval_plan.yaml"))
    print(yaml.safe_dump(plan, sort_keys=False))
    print(f"wrote {out/'eval_plan.yaml'}")


def cmd_inspect(args):
    plan = _plan(DEMO_TARGET_META)
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


def _load_cases(args):
    if args.cases:
        return [json.loads(l) for l in open(args.cases) if l.strip()]
    return demo_target.base_cases()


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _fmt(x):
    return "n/a" if x is None else f"{x:.0%}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="clinical-ai-eval", description="EVAL_STANDARD.md reference harness (§10)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").set_defaults(func=cmd_plan)
    sub.add_parser("inspect").set_defaults(func=cmd_inspect)
    pi = sub.add_parser("init"); pi.add_argument("--workspace"); pi.set_defaults(func=cmd_init)
    pr = sub.add_parser("run")
    pr.add_argument("--arm", default="flawed", choices=list(demo_target.SUBJECT_ARMS))
    pr.add_argument("--family", default="missing_information")
    pr.add_argument("--panel"); pr.add_argument("--subject"); pr.add_argument("--cases"); pr.add_argument("--workspace")
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
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
