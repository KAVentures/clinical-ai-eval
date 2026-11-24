"""Offline draft evaluation planning for buyer and research browser workflows.

A scope preview calls the existing scientific intake and selection functions.
It is NOT a validated run project, frozen procurement, clinical approval, or
authority to call a model. No endpoints, credentials or patient records are needed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import buyer_agent as buyer, intake, project, selection, manifest
from .util import repo_root
from .version import __version__

MAX_INPUT = 128 * 1024


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value, field, limit=4000):
    if not isinstance(value, str) or len(value) > limit or project._is_unanswered(value):
        raise buyer.BuyerError(f"{field} requires an explicit nonempty answer")
    return value.strip()


def _rules_hash():
    return manifest.hash_file(repo_root() / "selection_rules.yaml")


def _plan_dir(directory):
    path = Path(directory).resolve() / "plans"
    if path.is_symlink():
        raise buyer.BuyerError("plan directory must not be a symlink")
    return path


def preview(directory, spec: dict) -> dict:
    state = buyer.load(directory)
    if not isinstance(spec, dict) or set(spec) != {
            "vendor_id", "target_name", "target_version", "profiles", "answers"}:
        raise buyer.BuyerError("scope preview requires vendor_id, target_name, target_version, profiles and answers")
    vendor_id = _text(spec["vendor_id"], "vendor_id", 64)
    if vendor_id not in {v["vendor_id"] for v in state["vendors"]}:
        raise buyer.BuyerError("vendor must be registered in this buyer project")
    target_name = _text(spec["target_name"], "target_name", 300)
    version = _text(spec["target_version"], "target_version", 300)
    profiles = spec["profiles"]
    if (not isinstance(profiles, list) or not profiles
            or any(not isinstance(p, str) or p not in intake.TARGET_PROFILES for p in profiles)
            or len(set(profiles)) != len(profiles)):
        raise buyer.BuyerError("select distinct known target profiles")
    answers = spec["answers"]
    if not isinstance(answers, dict) or set(answers) != set(intake.INTAKE_QUESTIONS):
        raise buyer.BuyerError("answer every canonical scientific intake question")
    answers = {q: _text(answers[q], q) for q in intake.INTAKE_QUESTIONS}
    # This is a preview, not a runnable target descriptor. Explicitly record
    # unknown endpoint/modalities rather than pretending a connector exists.
    target = {"name": target_name, "version": version, "profiles": sorted(profiles),
              "endpoint": "not configured (scope preview)", "input_modalities": [],
              "output_actions": [], **answers}
    plan = intake.build_eval_plan(target)
    plan["selection"] = selection.select_suites(plan["target_profile"]["types"])
    definition_hashes = {
        row["suite"]: manifest.hash_file(repo_root() / "tests" / row["suite"] / "family.yaml")
        for row in plan["selection"]["required_suites"]
    }
    record = {
        "schema": "buyer-scope-preview/1", "tool_version": __version__,
        "buyer_scope": {k: state.get(k, "") for k in
                         ("project_name", "intended_use", "setting", "jurisdiction", "created_at")},
        "vendor_id": vendor_id, "selection_rules_hash": _rules_hash(),
        "family_definition_hashes": definition_hashes, "plan": plan,
        "decision_grade": False, "execution_authorized": False,
        "status": "draft_scope_preview", "network_calls": 0,
        "limitations": [
            "This is a draft test-selection preview, not a validated project or frozen procurement.",
            "Connector, case pack, judges, acceptance thresholds and clinical reviewers still need configuration.",
            "Runnable is a software capability, not a clinically validated family or a passed test.",
        ],
    }
    plan_id = _digest(record)
    record = {**record, "plan_id": plan_id}
    folder = _plan_dir(directory)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = folder / (plan_id + ".json")
    # Repeating identical planning is idempotent. Never overwrite a changed
    # snapshot or follow a path supplied by a user.
    if dest.is_symlink():
        raise buyer.BuyerError("plan snapshot must not be a symlink")
    try:
        buyer._write_json(dest, record, create=True)
    except FileExistsError:
        if load(directory, plan_id, include_freshness=False) != record:
            raise buyer.BuyerError("existing plan snapshot conflicts with generated plan")
    return record


def load(directory, plan_id: str, *, include_freshness=True) -> dict:
    buyer.load(directory)
    if not isinstance(plan_id, str) or len(plan_id) != 64 or any(
            ch not in "0123456789abcdef" for ch in plan_id):
        raise buyer.BuyerError("invalid plan identifier")
    path = _plan_dir(directory) / (plan_id + ".json")
    if path.is_symlink() or not path.is_file():
        raise buyer.BuyerError("plan snapshot not found")
    with path.open("rb") as handle:
        raw = handle.read(512 * 1024 + 1)
    if len(raw) > 512 * 1024:
        raise buyer.BuyerError("plan snapshot exceeds size limit")
    try:
        record = json.loads(raw.decode("utf-8"))
        if (not isinstance(record, dict) or record.get("plan_id") != plan_id
                or _digest({k: v for k, v in record.items() if k != "plan_id"}) != plan_id):
            raise buyer.BuyerError("plan snapshot integrity mismatch")
        if record.get("schema") != "buyer-scope-preview/1":
            raise buyer.BuyerError("unsupported plan snapshot")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise buyer.BuyerError("invalid or modified plan snapshot") from exc
    if not include_freshness:
        return record
    current = record["selection_rules_hash"] == _rules_hash()
    for family, digest in record["family_definition_hashes"].items():
        if not buyer.IDENTIFIER.fullmatch(family):
            raise buyer.BuyerError("invalid family in plan snapshot")
        current = current and digest == manifest.hash_file(
            repo_root() / "tests" / family / "family.yaml")
    return {**record, "definitions_current": bool(current and record["tool_version"] == __version__)}


def preview_file(directory, path: str) -> dict:
    with Path(path).expanduser().open("rb") as handle:
        raw = handle.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise buyer.BuyerError("plan input exceeds size limit")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise buyer.BuyerError("invalid plan input JSON") from exc
    return preview(directory, data)
