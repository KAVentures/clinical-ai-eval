"""Bounded synthetic browser rehearsal using the canonical research CLI.

No vendor endpoint, API key, case-pack upload or arbitrary command is accepted.
Only shipped clinician fixtures and deterministic mock judges are used. A rehearsal
is NOT an assessment of the candidate named in the source planning snapshot.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from . import buyer_agent as buyer, buyer_plan, manifest, pipeline, project, selection
from .util import repo_root

PROFILES = {"clinician_decision_support", "medication_assistant"}
FAMILIES = {"missing_information", "conflicting_evidence"}
TIMEOUT_SECONDS = 120
MAX_REHEARSALS = 100


def _folder(directory) -> Path:
    root = Path(directory).resolve() / "rehearsals"
    if root.is_symlink():
        raise buyer.BuyerError("rehearsal directory must not be a symbolic link")
    return root


def run(directory, plan_id: str) -> dict:
    """Run a fresh isolated demonstration, then verify every expected package."""
    root = Path(directory).resolve()
    state = buyer.load(root)
    plan = buyer_plan.load(root, plan_id)
    scope = {k: state.get(k, "") for k in
             ("project_name", "intended_use", "setting", "jurisdiction", "created_at")}
    if (not plan["definitions_current"] or plan["buyer_scope"] != scope
            or plan["vendor_id"] not in {v["vendor_id"] for v in state["vendors"]}):
        raise buyer.BuyerError("saved scope is stale; regenerate the plan")
    profiles = plan["plan"]["target_profile"]["types"]
    if not profiles or not set(profiles) <= PROFILES:
        raise buyer.BuyerError("browser rehearsal currently supports clinician decision support and medication assistant only")
    selected = selection.select_suites(profiles)
    if selected != plan["plan"]["selection"]:
        raise buyer.BuyerError("saved selection does not match canonical rules")
    expected = selected["runnable_suites"]
    if not expected or not set(expected) <= FAMILIES:
        raise buyer.BuyerError("unsupported synthetic rehearsal family")
    panel_source = repo_root() / "configs" / "judge_panel.toml"
    panel = pipeline.load_panel(panel_source)
    if not panel.get("judges") or not all(j.get("mock") is True for j in panel["judges"]):
        raise buyer.BuyerError("rehearsal requires the installed all-mock judge panel")
    jobs = _folder(root)
    jobs.mkdir(mode=0o700, exist_ok=True)
    if sum(1 for p in jobs.iterdir()) >= MAX_REHEARSALS:
        raise buyer.BuyerError("local rehearsal limit reached; archive old jobs")
    job_id = "demo-" + uuid.uuid4().hex
    job = jobs / job_id
    job.mkdir(mode=0o700)
    # Never put actual vendor identity or user-entered clinical text into the
    # target descriptor. The plan link records only the intended test selection.
    config = project.template("Synthetic browser rehearsal", "demonstration")
    config["target"] = {"name": "Synthetic fixture — NOT a vendor product",
                        "version": "builtin-flawed", "vendor": "synthetic-fixture"}
    config["target_profile"]["types"] = profiles
    config["intake"] = {q: "Synthetic software rehearsal; not a real intended-use statement."
                        for q in project.MANDATORY_INTAKE}
    config["governance"] = {
        "influences_diagnosis_or_treatment": "no",
        "clinician_reviews_every_response": "no",
        "regulated_medical_device": "no",
        "deployment_jurisdictions": "synthetic demonstration only",
    }
    config["subject"] = {"kind": "mock", "arm": "flawed", "mock": True}
    config["case_pack"] = {"source": "builtin:demo_clinician"}
    # Copy the inspected panel into a private, explicit path. Never resolve a
    # project-relative override controlled by a browser request.
    panel_file = job / "panel.toml"
    shutil.copyfile(panel_source, panel_file)
    if not all(j.get("mock") is True for j in pipeline.load_panel(panel_file)["judges"]):
        raise buyer.BuyerError("copied panel is not all-mock")
    config["panel"] = {"config": str(panel_file)}
    project_file = job / "project.yaml"
    # JSON is valid YAML; use private writer without another serializer.
    buyer._write_json(project_file, config, create=True)
    project.load(project_file).require_valid()
    record = {
        "schema": "browser-rehearsal/1", "job_id": job_id, "source_plan_id": plan_id,
        "status": "running", "mode": "demonstration", "target": "synthetic fixture",
        "candidate_evaluated": False, "decision_grade": False,
        "network_calls": 0, "expected_families": expected, "packages": [],
    }
    result_file = job / "job.json"
    buyer._write_json(result_file, record, create=True)
    try:
        # Explicit argv; no shell, external endpoint, arbitrary flags or remote
        # model. Bounded synchronous execution; hospital deployment will use a
        # permission-checked worker queue instead of a browser request.
        proc = subprocess.run(
            [sys.executable, "-m", "caeval.cli", "run", "--project", str(project_file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, check=False,
        )
        if proc.returncode != 0:
            record.update(status="failed", reason="canonical evaluation process failed")
        else:
            packages = []
            for family in expected:
                ws = job / "out" / ("run_" + family)
                verdict = manifest.verify_manifest(ws).get("verdict")
                meta = json.loads((ws / manifest.MANIFEST_FILE).read_text(encoding="utf-8"))
                analysis = json.loads((ws / "analysis.json").read_text(encoding="utf-8"))
                authority = analysis.get("claim_authority", {})
                if (verdict != manifest.VALID or meta.get("family_id") != family
                        or authority.get("project_mode") != "demonstration"):
                    raise buyer.BuyerError("rehearsal package failed integrity or demonstration-mode check")
                packages.append({"family_id": family, "verdict": verdict,
                                 "manifest_hash": meta["manifest_hash"],
                                 "effective_claim": authority.get("effective_claim")})
            record.update(status="completed", packages=packages)
    except subprocess.TimeoutExpired:
        record.update(status="failed", reason="canonical evaluation timed out")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        record.update(status="failed", reason="evaluation or package verification failed")
    buyer._write_json(result_file, record)
    return record


def load(directory, job_id: str) -> dict:
    buyer.load(directory)
    if not isinstance(job_id, str) or not buyer.IDENTIFIER.fullmatch(job_id) or not job_id.startswith("demo-"):
        raise buyer.BuyerError("invalid rehearsal identifier")
    path = _folder(directory) / job_id / "job.json"
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        raise buyer.BuyerError("rehearsal not found")
    with path.open("rb") as handle:
        raw = handle.read(65537)
    if len(raw) > 65536:
        raise buyer.BuyerError("invalid rehearsal record")
    record = json.loads(raw.decode("utf-8"))
    if record.get("job_id") != job_id or record.get("schema") != "browser-rehearsal/1":
        raise buyer.BuyerError("invalid rehearsal record")
    # Reverify completed packages on retrieval; stale or changed results do not
    # inherit the prior status. Never expose raw patient content or cached metrics.
    if record.get("status") == "completed":
        try:
            expected = record["expected_families"]
            packages = record["packages"]
            if not expected or not set(expected) <= FAMILIES or {p["family_id"] for p in packages} != set(expected):
                raise buyer.BuyerError("incomplete rehearsal inventory")
            for p in packages:
                ws = path.parent / "out" / ("run_" + p["family_id"])
                if ws.is_symlink():
                    raise buyer.BuyerError("invalid rehearsal package path")
                meta = json.loads((ws / manifest.MANIFEST_FILE).read_text(encoding="utf-8"))
                if (meta.get("manifest_hash") != p["manifest_hash"]
                        or manifest.verify_manifest(ws).get("verdict") != manifest.VALID):
                    raise buyer.BuyerError("changed rehearsal package")
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            return {**record, "status": "invalid", "packages": [],
                    "candidate_evaluated": False, "decision_grade": False}
    return {**record, "candidate_evaluated": False, "decision_grade": False}
