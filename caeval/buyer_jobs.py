"""Durable local candidate assessment queue and canonical execution.

The browser queues jobs, but an authorised operator runs the worker. A real
judge panel requires explicit approval for that specific job. This module
does not provide institutional IAM, network isolation or clinical validation.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import buyer_agent as buyer, buyer_assessment as assessment, buyer_manual as manual
from . import buyer_research, claim, manifest, pipeline, project, selection, packsource, maturity, executors

MAX_JOBS = 200
TIMEOUT_SECONDS = 1800


def _root(directory):
    buyer.load(directory)
    root = manual._directory(directory, "evaluation-jobs")
    root.mkdir(mode=0o700, exist_ok=True)
    return root


@contextmanager
def _db(directory):
    path = _root(directory) / "jobs.sqlite3"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise buyer.BuyerError("invalid job database")
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    conn = sqlite3.connect(path, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL, status TEXT NOT NULL,
          mode TEXT NOT NULL, real_judges INTEGER NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP, started_at TEXT,
          finished_at TEXT, result TEXT NOT NULL DEFAULT '{}')""")
        yield conn
    finally:
        conn.close()


def _public(row):
    obj = dict(row)
    obj["result"] = json.loads(obj["result"])
    obj["real_judges"] = bool(obj["real_judges"])
    obj["decision_grade"] = False
    obj["response_provenance"] = "operator_attested_unverified"
    return obj


def enqueue(directory, assessment_id):
    record = assessment.check(directory, assessment_id)
    _, panel = assessment._panel(directory, record["panel_id"])
    real = any(not j["mock"] for j in panel["judges"])
    with _db(directory) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= MAX_JOBS:
            raise buyer.BuyerError("job limit reached")
        job = "job-" + uuid.uuid4().hex
        db.execute("INSERT INTO jobs(id,assessment_id,status,mode,real_judges) VALUES (?,?,?,?,?)",
                   (job, assessment_id, "queued", record["mode"], int(real)))
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
        db.commit()
        return _public(row)


def _get(db, job_id):
    manual._identifier(job_id)
    row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise buyer.BuyerError("evaluation job not found")
    return row


def list_jobs(directory):
    with _db(directory) as db:
        return [_public(row) for row in db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (MAX_JOBS,))]


def _job_path(directory, job_id):
    manual._identifier(job_id)
    path = _root(directory) / job_id
    if path.is_symlink():
        raise buyer.BuyerError("invalid job workspace")
    return path


def _read_inputs(job, binding):
    """Check the complete private execution input set and recompute the plan."""
    if (job / "pack").is_symlink() or not (job / "pack").is_dir():
        raise buyer.BuyerError("invalid execution case pack")
    files = binding["file_sha256"]
    required = {"project.yaml", "responses.json", "panel.toml"}
    pack_files = set(files) - required
    case_files = pack_files & {"pack/cases.json", "pack/cases.jsonl"}
    if (not required <= set(files) or len(case_files) != 1
            or pack_files - case_files - {"pack/pack.json"}):
        raise buyer.BuyerError("unexpected execution input inventory")
    if {p.name for p in (job / "pack").iterdir()} != {p.split("/",1)[1] for p in pack_files}:
        raise buyer.BuyerError("execution case pack inventory changed")
    for name, digest in files.items():
        p = job / name
        if p.is_symlink() or not p.is_file() or p.stat().st_size > manual.MAX_REQUEST_BYTES:
            raise buyer.BuyerError("invalid execution input")
        if assessment._sha(p.read_bytes()) != digest:
            raise buyer.BuyerError("execution input changed")
    for rel, digest in binding["scientific_definitions"].items():
        if manifest.hash_file(manifest.definition_file(rel)) != digest:
            raise buyer.BuyerError("scientific definition changed")
    cfg = project.load(job)
    cfg.require_valid()
    panel = pipeline.load_panel(job / "panel.toml")
    pipeline.assess_panel(panel)
    cases, desc = packsource.resolve({"source":str(job / "pack")},"clinician_vignette")
    if desc["content_hash"] != binding["pack_content_hash"]:
        raise buyer.BuyerError("execution pack content changed")
    responses = json.loads((job / "responses.json").read_text(encoding="utf-8"))
    if not isinstance(responses,dict) or any(not isinstance(v,str) or not v.strip() for v in responses.values()):
        raise buyer.BuyerError("invalid collected response mapping")
    return cfg,panel,cases,desc,responses


def _verify(directory, row):
    job = _job_path(directory, row["id"])
    if not job.is_dir() or (job / "out").is_symlink():
        raise buyer.BuyerError("missing execution workspace")
    binding = manual._read(job / "binding.json")
    if (binding.get("assessment_id") != row["assessment_id"]
            or binding.get("mode") != row["mode"]
            or assessment.buyer_plan._digest({k: v for k, v in binding.items()
                                              if k != "assessment_id"}) != row["assessment_id"]):
        raise buyer.BuyerError("execution binding changed")
    cfg,panel,cases,desc,responses = _read_inputs(job,binding)
    if cfg.mode != row["mode"] or cfg.subject.get("kind") != "manual":
        raise buyer.BuyerError("execution project changed")
    if any(not j["mock"] for j in panel["judges"]) != row["real_judges"]:
        raise buyer.BuyerError("execution panel policy changed")
    selected = selection.select_suites(cfg.profiles)
    expected = selected["runnable_suites"]
    if not expected or any(executors.resolve(f).executor_id != executors.GENERIC_PAIRED_TEXT for f in expected):
        raise buyer.BuyerError("unsupported execution family")
    out = job / "out"
    if not out.is_dir() or {p.name for p in out.iterdir() if p.name.startswith("run_")} != {"run_"+f for f in expected}:
        raise buyer.BuyerError("execution family inventory differs from plan")
    packages = []
    seen = set()
    for family in expected:
        manual._identifier(family)
        ws = out / ("run_" + family)
        if ws.is_symlink() or not ws.is_dir():
            raise buyer.BuyerError("missing family package")
        meta = buyer_research._preflight(ws)
        if manifest.verify_manifest(ws)["verdict"] != manifest.VALID or meta["family_id"] != family:
            raise buyer.BuyerError("invalid family package")
        def read_json(key):
            return json.loads(buyer_research.verified_bytes(ws, meta, key))
        analysis = read_json("analysis")
        bound = read_json("plan_binding")
        run_meta = read_json("run_meta")
        family_def = pipeline.load_family(family)
        # Re-derive the bound plan from the actual prepared project, not from
        # the run's own reported binding.
        names = [j["name"] for j in panel["judges"]]
        claim.verify_binding(bound, claim.build_binding(cfg,family,names,desc["content_hash"]))
        derived = claim.compute(cfg.mode,run_meta["panel"]["conformance_level"],
                                maturity.family_maturity(family_def),
                                claim.pack_authority(desc),"real")
        if (analysis["family_id"] != family or run_meta["family_id"] != family
                or analysis.get("plan_binding") != bound
                or analysis.get("case_pack",{}).get("content_hash") != desc["content_hash"]
                or run_meta["panel"]["names"] != names
                or run_meta["subject_spec"].get("kind") != "manual"
                or analysis["claim_authority"] != derived.as_dict()
                or derived.effective_claim not in ("none","demonstration","internal_regression")):
            raise buyer.BuyerError("run package does not match bound assessment")
        # Check exact per-family coverage (a union across families could hide
        # a missing baseline shared by two suites), prompt text and responses.
        expected_rows = pipeline.generate_responses(lambda rec: responses[rec["cell_id"]],family_def,cases)
        actual_rows = [json.loads(line) for line in buyer_research.verified_bytes(ws,meta,"responses").splitlines() if line.strip()]
        if actual_rows != expected_rows:
            raise buyer.BuyerError("family prompts or responses differ from canonical collected cells")
        ids = {r["cell_id"] for r in actual_rows}
        if len(ids)!=len(actual_rows):
            raise buyer.BuyerError("duplicate response cell")
        seen.update(ids)
        review = read_json("review_manifest")
        review_ids=[r["cell_id"] for r in review["expected_cells"]]
        if (review["n_expected"] != len(review_ids) or len(review_ids)!=len(set(review_ids))
                or not set(review_ids)<=ids):
            raise buyer.BuyerError("invalid review coverage")
        packages.append({"family_id": family, "manifest_hash": meta["manifest_hash"],
                         "claim": derived.effective_claim, "review_count": review["n_expected"],
                         "n_responses": len(actual_rows)})
    if seen != set(responses):
        raise buyer.BuyerError("not every collected response was executed")
    return {"packages":packages,"required_but_not_run":selected["required_but_not_run"]}


def status(directory, job_id):
    with _db(directory) as db:
        row = _public(_get(db, job_id))
    if row["status"] == "completed":
        try:
            if _verify(directory, row) != row["result"]:
                raise buyer.BuyerError("execution result changed")
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            row.update(status="invalid", result={"reason": "package integrity or binding changed"})
    return row


def cancel(directory, job_id):
    with _db(directory) as db:
        _get(db, job_id)
        if db.execute("UPDATE jobs SET status='cancelled', finished_at=CURRENT_TIMESTAMP "
                      "WHERE id=? AND status='queued'", (job_id,)).rowcount != 1:
            raise buyer.BuyerError("only queued jobs can be cancelled")
        db.commit()
    return status(directory, job_id)


def execute(directory, job_id, *, allow_model_calls=False):
    """Claim exactly one job atomically; no automatic retries or provider calls."""
    with _db(directory) as db:
        db.execute("BEGIN IMMEDIATE")
        row = _public(_get(db, job_id))
        if row["status"] != "queued":
            raise buyer.BuyerError("only queued jobs may execute")
        if row["real_judges"] and not allow_model_calls:
            raise buyer.BuyerError("real judges require explicit operator approval for model calls")
        db.execute("UPDATE jobs SET status='running', started_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        db.commit()
    final, result = "failed", {"reason": "execution or verification failed"}
    try:
        record = assessment.check(directory, row["assessment_id"])
        expected, files = assessment._material(directory, record["submission_id"],
                                                record["panel_id"], record["governance"])
        if expected != record or record["mode"] != row["mode"]:
            raise buyer.BuyerError("assessment changed before execution")
        job = _job_path(directory, job_id)
        job.mkdir(mode=0o700)
        for name, raw in files.items():
            p = job / name
            p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with p.open("xb") as f:
                p.chmod(0o600)
                f.write(raw)
        buyer._write_json(job / "binding.json", record, create=True)
        _, panel, _, _, _ = _read_inputs(job, record)
        if any(not j["mock"] for j in panel["judges"]) != row["real_judges"]:
            raise buyer.BuyerError("judge network policy changed")
        # Preserve import resolution when running from a source checkout, but
        # do not accept an executable, cwd or arbitrary flags from the user.
        env = os.environ.copy()
        package_parent = str(Path(__file__).resolve().parents[1])
        env["PYTHONPATH"] = package_parent + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run([sys.executable, "-m", "caeval.cli", "run", "--project", "."],
                              cwd=job, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=TIMEOUT_SECONDS, check=False)
        if proc.returncode != 0:
            raise buyer.BuyerError("canonical runner failed")
        result = _verify(directory, row)
        final = "completed"
    except subprocess.TimeoutExpired:
        result = {"reason": "evaluation timed out; no automatic retry"}
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        pass
    finally:
        with _db(directory) as db:
            db.execute("UPDATE jobs SET status=?, result=?, finished_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
                       (final, json.dumps(result), job_id))
            db.commit()
    return status(directory, job_id)
