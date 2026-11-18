#!/usr/bin/env python3
"""Walk the documented "test your own clinical AI" path end to end, offline.

This is what docs/TEST_YOUR_SYSTEM.md tells a new organisation to do, executed
exactly as written, through the `clinical-ai-eval` command, against local
stand-ins (tools/fake_endpoints.py) for the organisation's product and for a
model provider. Every request goes over real HTTP to 127.0.0.1; nothing leaves
the machine and nothing is billed.

It checks the things a first-time user would hit:
  - a new project refuses to run until the intake is answered;
  - the product credential is a ${VAR} reference, a missing variable fails
    clearly, and the secret never reaches a workspace or an evidence package;
  - a judge from a provider defined only in config is checked offline, listed,
    and called;
  - a release-to-release comparison runs;
  - the evidence package verifies, and tampering with it is detected.

Run from anywhere:  python tools/self_service_rehearsal.py [--cli clinical-ai-eval]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_endpoints import Server  # noqa: E402

PRODUCT_TOKEN = "rehearsal-product-token-7f3a"      # must never appear in any output file
MODEL_KEY = "rehearsal-model-key-91c2"
STEP = [0]


def step(title: str) -> None:
    STEP[0] += 1
    print(f"\n[{STEP[0]:02d}] {title}", flush=True)


def check(ok: bool, message: str) -> None:
    if not ok:
        sys.exit(f"FAIL: {message}")
    print(f"     ok: {message}")


class CLI:
    def __init__(self, base: list[str], cwd: Path, env: dict):
        self.base, self.cwd, self.env = base, cwd, env

    def __call__(self, *args, expect_fail: str | None = None, env: dict | None = None) -> str:
        r = subprocess.run([*self.base, *map(str, args)], cwd=self.cwd, capture_output=True, text=True,
                           env={**self.env, **(env or {})})
        out = r.stdout + r.stderr
        shown = " ".join(map(str, args[:2]))
        if expect_fail is not None:
            if r.returncode == 0 or expect_fail not in out:
                sys.exit(f"FAIL: expected `{shown}` to refuse with {expect_fail!r}\n{out[-2000:]}")
            print(f"     refused as required: {expect_fail}")
            return out
        if r.returncode != 0:
            sys.exit(f"FAIL: `{shown}` exited {r.returncode}\n{out[-3000:]}")
        return out


def files_containing(root: Path, needle: str) -> list[str]:
    hits = []
    for p in root.rglob("*"):
        if p.is_file() and needle.encode() in p.read_bytes():
            hits.append(str(p.relative_to(root)))
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cli", default="", help="CLI to test, e.g. an installed `clinical-ai-eval`. "
                                            "Default: python -m caeval.cli from this checkout.")
    ap.add_argument("--keep", type=Path, help="keep the project directory here (must not exist)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    base = [args.cli] if args.cli else [sys.executable, "-m", "caeval.cli"]
    work = Path(tempfile.mkdtemp(prefix="caeval_selfservice_"))
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY") and k != "MEDROBUST_KEYS_PATH"}
    if not args.cli:
        env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    env["MEDROBUST_KEYS_PATH"] = str(work / "no-keys-file.md")   # prove env vars alone suffice
    cli = CLI(base, work, env)
    try:
        with Server(PRODUCT_TOKEN, MODEL_KEY) as srv:
            run(cli, work, srv)
        print("\nPASS: the documented self-service path works end to end, offline, from "
              f"{'the installed CLI' if args.cli else 'this checkout'}.")
    finally:
        if args.keep:
            shutil.copytree(work, args.keep)
            print(f"kept: {args.keep}")
        shutil.rmtree(work, ignore_errors=True)


def run(cli: CLI, work: Path, srv: Server) -> None:
    project = work / "acme-cds-assessment"

    step("Create a project; an unanswered intake must refuse to plan")
    cli("project", "init", project, "--name", "acme-cds")
    cli("target", "validate", "--project", project, expect_fail="unanswered")

    step("Answer the intake and point the subject at the product, credential by ${VAR}")
    d = yaml.safe_load((project / "project.yaml").read_text())
    d["project"]["mode"] = "internal_regression"
    d["target"] = {"name": "ACME clinical decision support", "version": "2.3.0", "vendor": "ACME"}
    d["target_profile"]["types"] = ["clinician_decision_support"]
    d["intake"] = {q: f"rehearsal answer for {q}" for q in d["intake"]}
    d["governance"] = {"influences_diagnosis_or_treatment": "yes", "clinician_reviews_every_response": "yes",
                       "regulated_medical_device": "unsure", "deployment_jurisdictions": "SE"}
    d["subject"] = {"kind": "http", "url": f"{srv.base}/product/answer", "prompt_field": "question",
                    "answer_path": "data.text", "timeout": 30, "retries": 3,
                    "headers": {"Authorization": "Bearer ${ACME_PRODUCT_TOKEN}"}}
    d["panel"] = {"config": "panel.toml"}            # resolved against the project directory
    (project / "project.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    (project / "panel.toml").write_text(
        "min_distinct_providers = 1\n\n"
        "[providers.acme_gateway]\n"
        'api = "openai_chat_completions"\n'
        f'base_url = "{srv.base}/v1"\n'
        'api_key_env = "ACME_GATEWAY_API_KEY"\n'
        'effort_param = ""\n'
        'vendor = "example_model_developer"\n\n'
        "[[judges]]\n"
        'name = "gateway-judge"\n'
        'provider = "acme_gateway"\n'
        'model = "fake-judge-1"\n'
        'reasoning_effort = "provider_default"\n'
        'mode = "blinded"\n'
        "mock = false\n")
    out = cli("target", "validate", "--project", project)
    check("valid" in out.lower() or "ok" in out.lower(), "answered intake validates")

    step("Connector test: fails clearly without the token, passes with it")
    cli("connector", "test", "--project", project, expect_fail="ACME_PRODUCT_TOKEN")
    token_env = {"ACME_PRODUCT_TOKEN": PRODUCT_TOKEN, "ACME_GATEWAY_API_KEY": MODEL_KEY}
    out = cli("connector", "test", "--project", project, env=token_env)
    check("connector OK" in out, "product reached with the expanded credential")

    step("Judge model: checked offline, then listed (no tokens generated)")
    out = cli("models", "check", "--project", project)
    check("acme_gateway/fake-judge-1" in out and "openai_chat_completions" in out,
          "a judge from a provider defined only in panel.toml resolves offline")
    cli("models", "list", "--project", project, expect_fail="missing ACME_GATEWAY_API_KEY")
    out = cli("models", "list", "--project", project, env=token_env)
    check("fake-judge-1" in out and "OK." in out, "the configured judge ID is offered by its provider")

    step("Plan and run the assessment (baseline release)")
    cli("plan", "--project", project, env=token_env)
    before = srv.requests
    out = cli("run", "--project", project, "--workspace", project / "out" / "v2.3.0", env=token_env)
    check("claim:" in out and srv.requests - before > 20,
          f"run completed over real HTTP ({srv.requests - before} requests incl. retried 503s)")
    runs = sorted(p for p in (project / "out" / "v2.3.0").iterdir() if p.is_dir())
    check(bool(runs), f"workspace(s) written: {[p.name for p in runs]}")
    ws = runs[0]

    step("Nothing secret was written anywhere")
    for secret, label in ((PRODUCT_TOKEN, "product token"), (MODEL_KEY, "model key")):
        hits = files_containing(project, secret)
        check(not hits, f"{label} appears in no project, workspace or package file")
    meta = json.loads((ws / "run_meta.json").read_text()) if (ws / "run_meta.json").exists() else {}
    if meta:
        check(meta.get("subject_spec", {}).get("headers", {}).get("Authorization") == "Bearer ${ACME_PRODUCT_TOKEN}",
              "the saved connector spec keeps the variable reference, not the value")

    step("Evidence package verifies; tampering is caught")
    out = cli("verify-package", ws)
    check("VALID" in out, "package verifies")
    responses = next((p for p in ws.rglob("*.json*") if "response" in p.name), None)
    if responses is not None:
        original = responses.read_bytes()
        responses.write_bytes(original.replace(b"standard treatment", b"different treatment", 1)
                              if b"standard treatment" in original else original + b" ")
        cli("verify-package", ws, expect_fail="INVALID")
        responses.write_bytes(original)
        check("VALID" in cli("verify-package", ws), "restoring the file restores validity")

    step("Next release: rerun and compare against the baseline")
    cli("run", "--project", project, "--workspace", project / "out" / "v2.4.0", env=token_env)
    cand = sorted(p for p in (project / "out" / "v2.4.0").iterdir() if p.is_dir())[0]
    r = subprocess.run([*cli.base, "compare", "--baseline", str(ws), "--candidate", str(cand)],
                       cwd=cli.cwd, capture_output=True, text=True, env={**cli.env, **token_env})
    out = r.stdout + r.stderr
    check(r.returncode in (0, 1) and ("newly failing" in out.lower() or "still" in out.lower()),
          f"compare ran (exit {r.returncode}; non-zero means something newly fails, which CI can gate on)")


    step("Manual mode: export prompts, paste answers back, run with no API at all")
    d = yaml.safe_load((project / "project.yaml").read_text())
    d["subject"] = {"kind": "manual", "responses_file": "responses.json"}
    d["panel"] = {"config": "configs/judge_panel.toml"}      # shipped mock panel, from package data
    (project / "project.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    out = cli("prompts", "--project", project, "--out", project / "prompts")
    prompts = {}
    for f in (project / "prompts").glob("prompts_*.json"):
        prompts.update(json.loads(f.read_text()))
    check(len(prompts) > 50 and all(v.strip() for v in prompts.values()),
          f"{len(prompts)} prompts exported, each the exact case text")
    check(not any("expected_missing" in v or "perturbation" in v.lower() for v in prompts.values()),
          "exported prompts carry no perturbation metadata")
    (project / "responses.json").write_text(json.dumps(
        {k: "Pasted answer: the missing result is needed before a definite plan." for k in prompts}))
    before = srv.requests
    out = cli("run", "--project", project, "--workspace", project / "out" / "manual")
    check("claim:" in out and srv.requests == before, "manual run completes with zero network requests")


    step("Patient-facing chatbot: conversational connector, custom response mapping")
    triage = work / "acme-triage"
    cli("project", "init", triage, "--name", "acme-triage")
    t = yaml.safe_load((triage / "project.yaml").read_text())
    t["project"]["mode"] = "internal_regression"
    t["target"] = {"name": "ACME triage bot", "version": "1.0", "vendor": "ACME"}
    t["target_profile"]["types"] = ["patient_triage_chatbot"]
    t["intake"] = {q: "rehearsal answer" for q in t["intake"]}
    t["governance"] = {"influences_diagnosis_or_treatment": "yes", "clinician_reviews_every_response": "no",
                       "regulated_medical_device": "unsure", "deployment_jurisdictions": "SE"}
    t["subject"] = {"kind": "http", "modality": "conversation", "url": f"{srv.base}/product/chat",
                    "history_field": "messages", "answer_path": "reply", "retries": 3, "timeout": 30,
                    "headers": {"Authorization": "Bearer ${ACME_PRODUCT_TOKEN}"}}
    t["case_pack"] = {"source": "builtin:public_smoke"}
    (triage / "project.yaml").write_text(yaml.safe_dump(t, sort_keys=False))
    out = cli("connector", "test", "--project", triage, env=token_env)
    check("conversation probe" in out and "connector OK" in out,
          "connector test probes a conversational product as a conversation")
    out = cli("run", "--project", triage, env=token_env)
    check("patient_red_flag" in out and "claim:" in out,
          "a patient-facing run completes with the product's own reply field (answer_path: reply)")
    check(not files_containing(triage, PRODUCT_TOKEN), "the chatbot token appears in no saved file")


if __name__ == "__main__":
    main()
