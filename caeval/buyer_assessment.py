"""Create a reproducible, offline-prepared research project from a manual exchange.

The browser cannot upload arbitrary panel configurations, execute model calls, or
grant clinical authority. An administrator stages a panel; the operator supplies
governance answers. The generated project is capped at internal regression.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from . import buyer_agent as buyer, buyer_manual as manual, buyer_plan, project, pipeline, manifest, packsource
from .util import repo_root

MAX_PANEL_BYTES = 256 * 1024
MAX_ASSESSMENTS = 100


def panels(directory):
    buyer.load(directory)
    base = manual._directory(directory, "approved-panels")
    if not base.is_dir():
        return []
    return sorted(p.stem for p in base.glob("*.toml") if p.is_file()
                  and not p.is_symlink() and buyer.IDENTIFIER.fullmatch(p.stem))[:100]


def _panel(directory, panel_id):
    manual._identifier(panel_id)
    path = manual._directory(directory, "approved-panels") / (panel_id + ".toml")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PANEL_BYTES:
        raise buyer.BuyerError("select an operator-staged judge panel")
    raw = path.read_bytes()
    # Validate the exact snapshot, not a mutable source path.
    with tempfile.TemporaryDirectory(prefix="caeval-panel-") as tmp:
        snapshot = Path(tmp) / "panel.toml"
        snapshot.write_bytes(raw)
        try:
            config = pipeline.load_panel(snapshot)
            pipeline.assess_panel(config)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            raise buyer.BuyerError("staged judge panel failed canonical validation") from exc
    return raw, config


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _governance(spec):
    if not isinstance(spec, dict) or set(spec) != set(project.MANDATORY_GOVERNANCE):
        raise buyer.BuyerError("answer all canonical governance questions")
    result = {}
    for key in project.MANDATORY_GOVERNANCE:
        val = buyer_plan._text(spec[key], key, 500)
        if key in ("influences_diagnosis_or_treatment", "clinician_reviews_every_response"):
            if val not in ("yes", "no"):
                raise buyer.BuyerError(key + " must be yes or no")
        elif key == "regulated_medical_device" and val not in ("yes", "no", "unsure"):
            raise buyer.BuyerError(key + " must be yes, no or unsure")
        result[key] = val
    return result


def _material(directory, submission_id, panel_id, governance, *, conditions_hash=None, conn=None):
    root = Path(directory).resolve()
    state = buyer.load(root)
    sub = manual._submission(root, submission_id)
    if sub["status"] != "complete_unscored":
        raise buyer.BuyerError("complete candidate responses are required")
    exchange = manual._load(root, sub["exchange_id"])
    plan = buyer_plan.load(root, exchange["plan_id"])
    raw_panel, panel = _panel(root, panel_id)
    mock = any(j["mock"] for j in panel["judges"])
    mode = "demonstration" if mock else "internal_regression"
    answers = _governance(governance)
    vendor = next(v for v in state["vendors"] if v["vendor_id"] == exchange["vendor_id"])
    config = project.template("Candidate response assessment", mode)
    config["target"] = {"name": exchange["target"]["name"],
                        "version": exchange["target"]["version"],
                        "vendor": vendor["name"]}
    config["target_profile"]["types"] = plan["plan"]["target_profile"]["types"]
    config["intake"] = plan["plan"]["intake"]
    config["governance"] = answers
    config["subject"] = {"kind": "manual", "responses_file": "responses.json"}
    config["case_pack"] = {"source": "pack"}
    config["panel"] = {"config": "panel.toml"}
    # Explicitly no frozen procurement conditions or self-declared reviewers.
    config["procurement"] = {"conditions_hash": ""}
    if conditions_hash is not None:
        from . import buyer_risk_activation as activation
        actual=activation.check(root,exchange["plan_id"],exchange["pack_content_hash"],panel_id,raw_panel,conn=conn)
        if actual!=conditions_hash:
            raise buyer.BuyerError("requested procurement conditions do not match activated study")
        config["procurement"]["conditions_hash"]=actual
    config["clinical_review"] = {"reviewers": [], "resolution_mode": "consensus", "tie_reviewer": ""}
    source = manual._directory(root, "approved-packs") / exchange["pack_id"]
    files = {"panel.toml": raw_panel,
             "responses.json": buyer_plan._canonical(sub["mapping"]),
             "project.yaml": buyer_plan._canonical(config)}
    for name in ("cases.json", "cases.jsonl", "pack.json"):
        path = source / name
        if path.is_symlink():
            raise buyer.BuyerError("case pack contains a symbolic link")
        if path.is_file():
            files["pack/" + name] = path.read_bytes()
    definitions = {rel: manifest.hash_file(manifest.definition_file(rel))
                   for rel in (*manifest.DEFINITION_SOURCES,
                               *manifest.EXECUTOR_DEFINITION_SOURCES["generic_paired_text"])}
    if not all(definitions.values()):
        raise buyer.BuyerError("scientific definitions are missing")
    record = {
        "schema": "bound-manual-assessment/1", "submission_id": submission_id,
        "exchange_id": sub["exchange_id"], "plan_id": exchange["plan_id"],
        "panel_id": panel_id, "governance": answers, "mode": mode,
        "status": "prepared_not_executed", "decision_grade": False,
        "response_provenance": "operator_attested_unverified",
        "network_authorized": False, "n_responses": len(sub["mapping"]),
        "pack_content_hash": exchange["pack_content_hash"],
        "file_sha256": {name: _sha(raw) for name, raw in files.items()},
        "scientific_definitions": definitions,
    }
    if conditions_hash is not None:
        record["conditions_hash"]=conditions_hash
    record["assessment_id"] = buyer_plan._digest(record)
    return record, files


def _base(directory):
    return manual._directory(directory, "prepared-assessments")


def prepare(directory, submission_id, panel_id, governance, *, conditions_hash=None, expected_assessment_id=None, conn=None):
    record, files = _material(directory, submission_id, panel_id, governance, conditions_hash=conditions_hash, conn=conn)
    if expected_assessment_id is not None and record["assessment_id"]!=expected_assessment_id:
        raise buyer.BuyerError("prepared inputs changed since explicit approval")
    base = _base(directory)
    base.mkdir(mode=0o700, exist_ok=True)
    dest = base / record["assessment_id"]
    if dest.is_symlink():
        raise buyer.BuyerError("assessment directory must not be a symbolic link")
    if dest.exists():
        return check(directory, record["assessment_id"], conn=conn)
    if sum(1 for _ in base.iterdir()) >= MAX_ASSESSMENTS:
        raise buyer.BuyerError("prepared assessment limit reached")
    # Publish only after validating the complete private staging directory.
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=base) as tmp:
        stage = Path(tmp) / "assessment"
        stage.mkdir(mode=0o700)
        for name, raw in files.items():
            path = stage / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as handle:
                path.chmod(0o600)
                handle.write(raw)
        project.load(stage).require_valid()
        # Validate the exact copied case content, not just the mutable original.
        _, desc = packsource.resolve({"source": str(stage / "pack")}, "clinician_vignette")
        if desc["content_hash"] != record["pack_content_hash"]:
            raise buyer.BuyerError("case content changed during staging")
        buyer._write_json(stage / "binding.json", record, create=True)
        try:
            stage.rename(dest)
        except OSError:
            if not dest.is_dir() or dest.is_symlink():
                raise
            # Concurrent identical publication is permitted only after check().
    return check(directory, record["assessment_id"], conn=conn)


def check(directory, assessment_id, *, conn=None):
    """Revalidate the original inputs AND the copied research project."""
    identifier = manual._identifier(assessment_id, digest=True)
    dest = _base(directory) / identifier
    if dest.is_symlink() or not dest.is_dir() or (dest / "pack").is_symlink():
        raise buyer.BuyerError("prepared assessment not found")
    record = manual._read(dest / "binding.json")
    if (not isinstance(record, dict) or record.get("assessment_id") != identifier
            or buyer_plan._digest({k: v for k, v in record.items() if k != "assessment_id"}) != identifier):
        raise buyer.BuyerError("assessment binding integrity mismatch")
    expected, files = _material(directory, record["submission_id"], record["panel_id"], record["governance"], conditions_hash=record.get("conditions_hash"), conn=conn)
    if record != expected:
        raise buyer.BuyerError("source plan, responses, pack, panel or definitions changed; prepare again")
    for name, raw in files.items():
        p = dest / name
        if (p.is_symlink() or not p.is_file() or p.stat().st_size != len(raw)
                or _sha(p.read_bytes()) != record["file_sha256"][name]):
            raise buyer.BuyerError("prepared project file changed or missing: " + name)
    pack_names = {p.name for p in (dest / "pack").iterdir()}
    if pack_names != {name.split("/", 1)[1] for name in files if name.startswith("pack/")}:
        raise buyer.BuyerError("unexpected file in prepared pack")
    project.load(dest).require_valid()
    return record


def prepare_file(directory, source_file):
    with Path(source_file).expanduser().open("rb") as handle:
        spec = manual._parse(handle.read(manual.MAX_REQUEST_BYTES + 1))
    required={"submission_id", "panel_id", "governance"}
    if not isinstance(spec, dict) or not required<=set(spec) or set(spec)-required-{"conditions_hash"}:
        raise buyer.BuyerError("provide submission_id, panel_id, governance and optional conditions_hash")
    if "conditions_hash" in spec and not isinstance(spec["conditions_hash"],str):
        raise buyer.BuyerError("explicit activated conditions hash required")
    return prepare(directory, spec["submission_id"], spec["panel_id"], spec["governance"], conditions_hash=spec.get("conditions_hash"))
