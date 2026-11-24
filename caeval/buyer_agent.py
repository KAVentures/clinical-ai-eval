"""Local-first buyer workspace for healthcare AI procurement.

This is an administrative evidence index, not a clinical evaluator or certification.
All commands use local files only; no provider API, network, or model call is made.
The existing procurement_workflow remains the only route to verified comparisons.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import procurement_workflow

SCHEMA_VERSION = 1
STATE_FILE = "buyer.json"
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
EVIDENCE_KINDS = (
    "intended_use", "clinical_validation", "regulatory_status",
    "security", "data_processing", "integration", "monitoring",
)
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class BuyerError(ValueError):
    """A user-actionable local workspace error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _root(directory) -> Path:
    return Path(directory).expanduser().resolve()


def _write_json(path: Path, data: dict, *, create: bool = False) -> None:
    """Write owner-only local state without exposing partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if create:
        # 'x' prevents clobbering an existing buyer project.
        with path.open("x", encoding="utf-8") as f:
            os.chmod(path, 0o600)
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return
    fd, temporary = tempfile.mkstemp(prefix=".buyer-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.chmod(temporary, 0o600)
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def init(directory, name: str, intended_use: str = "", setting: str = "",
         jurisdiction: str = "") -> dict:
    if not str(name).strip():
        raise BuyerError("project name is required")
    root = _root(directory)
    if (root / STATE_FILE).exists():
        raise BuyerError(f"{root / STATE_FILE} exists; refusing to overwrite the buyer project")
    newly_created = not root.exists()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Do not chmod an existing organisation-owned directory: it may have a
    # deliberately managed shared-access policy. New projects start owner-only.
    if newly_created and os.name == "posix":
        os.chmod(root, 0o700)
    data = {
        "schema_version": SCHEMA_VERSION, "project_name": name.strip(),
        "intended_use": intended_use.strip(), "setting": setting.strip(),
        "jurisdiction": jurisdiction.strip(), "created_at": _now(),
        "vendors": [], "evidence": [],
    }
    _write_json(root / STATE_FILE, data, create=True)
    return {"directory": str(root), "project": data["project_name"],
            "status": "created", "network_calls": 0}


def load(directory) -> dict:
    path = _root(directory) / STATE_FILE
    if not path.is_file() or path.is_symlink():
        raise BuyerError(f"no local {STATE_FILE} at {path}; run 'buyer init' first")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise BuyerError("buyer workspace JSON is invalid") from exc
    if data.get("schema_version") != SCHEMA_VERSION:
        raise BuyerError("unsupported buyer workspace schema version")
    if not isinstance(data.get("vendors"), list) or not isinstance(data.get("evidence"), list):
        raise BuyerError("buyer workspace vendors/evidence must be arrays")
    return data


def add_vendor(directory, vendor_id: str, name: str) -> dict:
    if not IDENTIFIER.fullmatch(vendor_id or ""):
        raise BuyerError("vendor-id must be 1-64 lowercase letters, digits, '-' or '_'")
    if not str(name).strip():
        raise BuyerError("vendor name is required")
    root = _root(directory)
    data = load(root)
    if any(v["vendor_id"] == vendor_id for v in data["vendors"]):
        raise BuyerError(f"vendor {vendor_id!r} is already registered")
    data["vendors"].append({"vendor_id": vendor_id, "name": name.strip(),
                            "added_at": _now()})
    _write_json(root / STATE_FILE, data)
    return {"vendor_id": vendor_id, "name": name.strip(), "status": "registered"}


def _verify_document(root: Path, evidence: dict) -> str:
    """Do not trust a path in edited state: only our content-addressed store."""
    digest = evidence.get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return "invalid_record"
    relative = evidence.get("relative_path", "")
    if relative != f"evidence/{digest}":
        return "invalid_record"
    path = root / "evidence" / digest
    if path.is_symlink() or not path.is_file():
        return "missing"
    try:
        with path.open("rb") as f:
            h = _digest_stream(f).hexdigest()
    except OSError:
        return "unreadable"
    return "intact" if h == digest else "changed"


def _digest_stream(handle):
    h = hashlib.sha256()
    for block in iter(lambda: handle.read(1024 * 1024), b""):
        h.update(block)
    return h


def add_evidence(directory, vendor_id: str, kind: str, source_file: str,
                 title: str = "", source_filename: str | None = None,
                 *, _validated_questionnaire: bool = False) -> dict:
    root = _root(directory)
    data = load(root)
    if vendor_id not in {v["vendor_id"] for v in data["vendors"]}:
        raise BuyerError(f"vendor {vendor_id!r} is not registered")
    if kind not in EVIDENCE_KINDS and not (kind == "vendor_questionnaire" and _validated_questionnaire):
        raise BuyerError(f"kind must be one of: {', '.join(EVIDENCE_KINDS)}")
    source = Path(source_file).expanduser()
    # A local copy is required: referenced external paths are not durable and
    # may accidentally expose sensitive home directories in exported reports.
    if source.is_symlink() or not source.is_file():
        raise BuyerError("source must be a regular, non-symlink local file")
    if not stat.S_ISREG(source.stat().st_mode):
        raise BuyerError("source must be a regular file")
    if source.stat().st_size > MAX_DOCUMENT_BYTES:
        raise BuyerError("file exceeds 20 MiB limit; use a smaller evidence artifact")
    blob = source.read_bytes()
    if len(blob) > MAX_DOCUMENT_BYTES:
        raise BuyerError("file exceeds 20 MiB limit")
    digest = hashlib.sha256(blob).hexdigest()
    store = root / "evidence"
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = store / digest
    if target.is_symlink():
        raise BuyerError("evidence store contains a symlink; refusing to write")
    if target.exists():
        if _verify_document(root, {"sha256": digest, "relative_path": f"evidence/{digest}"}) != "intact":
            raise BuyerError("evidence store has a conflicting file; refusing overwrite")
    else:
        # O_EXCL prevents overwriting an existing content-addressed artifact.
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise BuyerError("evidence destination appeared concurrently; retry") from exc
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
    record = {
        "evidence_id": "ev-" + uuid.uuid4().hex[:12],
        "vendor_id": vendor_id, "kind": kind,
        "title": (title or source.name).strip(),
        "source_filename": (source_filename or source.name).replace("\\", "/").rsplit("/", 1)[-1][:200],
        "sha256": digest,
        "relative_path": f"evidence/{digest}", "bytes": len(blob),
        "added_at": _now(),
        "review_status": "not_reviewed",
    }
    data["evidence"].append(record)
    _write_json(root / STATE_FILE, data)
    return {"evidence_id": record["evidence_id"], "vendor_id": vendor_id,
            "kind": kind, "sha256": digest, "integrity": "intact",
            "clinical_validation": "not_established"}


def _assessment_status(root: Path) -> dict:
    assessment_dir = root / "assessment"
    if not (assessment_dir / procurement_workflow.STATE_FILE).exists():
        return {"status": "not_initialized",
                "next_step": "Use 'procurement init' in the local assessment/ subdirectory with buyer-frozen hazards and pack hash."}
    try:
        a = procurement_workflow.load(assessment_dir)
    except (ValueError, OSError, RuntimeError) as exc:
        return {"status": "invalid", "reason": str(exc)}
    registered = {v["vendor_id"] for v in a.get("vendors", [])}
    results = a.get("results", {})
    return {
        "status": "configured", "conditions_hash": a.get("conditions_hash"),
        "frozen_families": a.get("conditions", {}).get("families", []),
        "case_pack_hash_locked": bool(a.get("conditions", {}).get("case_pack", {}).get("content_hash")),
        "registered_vendor_ids": sorted(registered),
        "verified_result_counts": {
            k: sum(1 for val in families.values()
                   if val.get("ingestion") == "verified_package")
            for k, families in results.items()
        },
    }


def status(directory) -> dict:
    root = _root(directory)
    data = load(root)
    missing_intake = [k for k in ("intended_use", "setting", "jurisdiction") if not data.get(k)]
    vendors = []
    for v in data["vendors"]:
        rows = [e for e in data["evidence"] if e.get("vendor_id") == v["vendor_id"]]
        docs = [{**{k: e.get(k) for k in ("evidence_id", "kind", "title", "sha256", "review_status")},
                 "integrity": _verify_document(root, e)} for e in rows]
        supplied = {d["kind"] for d in docs if d["integrity"] == "intact"}
        from . import buyer_questionnaire as questionnaire
        questionnaires = []
        for doc in docs:
            if doc["kind"] != questionnaire.KIND:
                continue
            summary = {"evidence_id": doc["evidence_id"], "integrity": doc["integrity"],
                       "claim_authority": "vendor_self_reported_unverified", "decision_grade": False}
            if doc["integrity"] == "intact":
                try:
                    with (root / "evidence" / doc["sha256"]).open("rb") as handle:
                        summary.update(questionnaire.validate(root, v["vendor_id"], handle.read(questionnaire.MAX_BYTES + 1)))
                    summary["schema_status"] = "valid"
                except (OSError, BuyerError):
                    summary["schema_status"] = "invalid_or_stale"
            questionnaires.append(summary)
        vendors.append({"vendor_id": v["vendor_id"], "name": v["name"],
                        "documents": docs, "questionnaires": questionnaires, "missing_evidence_kinds": sorted(set(EVIDENCE_KINDS) - supplied),
                        "all_documents_intact": all(d["integrity"] == "intact" for d in docs)})
    assessment = _assessment_status(root)
    for v in vendors:
        if assessment["status"] != "configured":
            v["evaluation_status"] = "not_configured"
            v["verified_package_count"] = 0
        elif v["vendor_id"] not in assessment["registered_vendor_ids"]:
            v["evaluation_status"] = "not_registered_for_testing"
            v["verified_package_count"] = 0
        else:
            count = assessment["verified_result_counts"].get(v["vendor_id"], 0)
            v["verified_package_count"] = count
            v["evaluation_status"] = ("verified_package_present" if count > 0
                                      else "awaiting_verified_evidence")
    actions = []
    if missing_intake:
        actions.append({"id": "complete_intake", "description": "Complete buyer intended use, setting, and jurisdiction before making claims."})
    if not vendors:
        actions.append({"id": "register_vendors", "description": "Register candidate vendors locally."})
    if any(v["missing_evidence_kinds"] for v in vendors):
        actions.append({"id": "request_vendor_evidence", "description": "Collect missing vendor documents; presence alone does not establish validity."})
    if assessment["status"] == "not_initialized":
        actions.append({"id": "freeze_assessment", "description": "Freeze reviewed case pack, hazards, thresholds, and evaluators through existing procurement init."})
    if assessment["status"] == "invalid":
        actions.append({"id": "repair_assessment", "description": "Fix invalid frozen assessment state before any comparison."})
    if any(v["evaluation_status"] == "not_registered_for_testing" for v in vendors):
        actions.append({"id": "register_assessment_subjects",
                        "description": "Register each relevant candidate in the frozen clinical testing study before running it."})
    if any(v["evaluation_status"] == "awaiting_verified_evidence" for v in vendors):
        actions.append({"id": "complete_clinical_testing",
                        "description": "Run and clinician-review candidate evaluations; ingest only verified packages. A registered vendor without evidence has not passed."})
    return {
        "schema_version": SCHEMA_VERSION, "directory": str(root),
        "project_name": data["project_name"], "intended_use": data.get("intended_use"),
        "missing_intake": missing_intake, "vendors": vendors, "assessment": assessment,
        "next_actions": actions, "decision_grade": False,
        "decision_grade_reason": "An administrative index is never clinical validation, independent package verification, or procurement authorization.",
        "network_calls": 0,
    }


def _md(value) -> str:
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_dossier(directory) -> str:
    """Combine the administrative register and freshly reverified clinical evidence.

    Reconstruct comparisons from the original evidence packages, not editable cached
    result rows. Failed/missing packages are explicitly excluded. This is still an
    exploratory dossier; a valid package is not a validated clinical measurement.
    """
    root = _root(directory)
    report = status(root)
    L = [
        f"# Buyer evidence register — {_md(report['project_name'])}", "",
        "> Administrative inventory only. Document presence is not independent verification,",
        "> clinical validation, regulatory certification, or a purchasing recommendation.", "",
        f"Intended use: {_md(report['intended_use'] or '(not provided)')}",
        f"Missing intake fields: {_md(', '.join(report['missing_intake']) or 'none')}", "",
    ]
    for v in report["vendors"]:
        L.extend([f"## {_md(v['name'])}", "",
                  "| Evidence kind | Document | Integrity | Review |",
                  "| --- | --- | --- | --- |"])
        for e in v["documents"]:
            L.append(f"| {_md(e['kind'])} | {_md(e['title'])} | {_md(e['integrity'])} | {_md(e['review_status'])} |")
        L.extend(["", f"Missing evidence types: {_md(', '.join(v['missing_evidence_kinds']) or 'none')}", ""])
        for q in v.get("questionnaires", []):
            L.append(f"- Vendor disclosure {_md(q['evidence_id'])}: self-reported, unverified; "
                     f"integrity={_md(q['integrity'])}; schema={_md(q.get('schema_status', 'not_checked'))}.")
            if q.get("schema_status") == "valid":
                L.append(f"  Product version: {_md(q['product_version_self_reported'])}; "
                         f"responses: {_md(q['counts'])}. No independent corroboration implied.")
        L.append("")
    L.extend(["## Clinical assessment", "",
              f"Status: {_md(report['assessment']['status'])}", ""])
    if report["assessment"]["status"] == "configured":
        L.extend([f"Frozen conditions hash: `{_md(report['assessment']['conditions_hash'])}`", ""])
        L.extend(_clinical_dossier_lines(root, report))
    else:
        L.extend(["No verified clinical comparison is available from this workspace.", ""])
    return "\n".join(L)


def _clinical_dossier_lines(root: Path, report: dict) -> list[str]:
    from copy import deepcopy
    pw = procurement_workflow
    original = pw.load(root / "assessment")
    if original["conditions_hash"] != report["assessment"]["conditions_hash"]:
        raise BuyerError("assessment changed during report creation; retry")
    buyer_ids = {v["vendor_id"] for v in report["vendors"]}
    study_ids = {v["vendor_id"] for v in original["vendors"]}
    lines = [
        "### Reverification at export", "",
        "Clinical results below are reconstructed from the original packages.",
        "Package integrity is not proof of clinical validity or vendor identity.", "",
    ]
    for missing in sorted(buyer_ids - study_ids):
        lines.append(f"- Candidate {_md(missing)}: not registered in the clinical study.")
    for extra in sorted(study_ids - buyer_ids):
        lines.append(f"- Study participant {_md(extra)}: not registered in the buyer shortlist; excluded.")
    rebuilt = deepcopy(original)
    rebuilt["results"] = {}
    rebuilt["vendors"] = [v for v in rebuilt["vendors"] if v["vendor_id"] in buyer_ids]
    # Private scratch state; do not change the original frozen procurement or
    # silently trust modified cached cells. Re-ingestion enforces the same gates.
    with tempfile.TemporaryDirectory(prefix="caeval-reverify-") as tmp:
        pw.save(tmp, rebuilt)
        for vendor_id in sorted(buyer_ids & study_ids):
            recorded = original.get("results", {}).get(vendor_id, {})
            for family in original["conditions"]["families"]:
                item = recorded.get(family)
                if not item or item.get("ingestion") != "verified_package":
                    lines.append(f"- {_md(vendor_id)} / {_md(family)}: no verified package recorded.")
                    continue
                package = item.get("package") or {}
                try:
                    if not package.get("workspace") or not package.get("package_digest"):
                        raise BuyerError("package location or digest missing")
                    # Refuse changed manifests even if the replacement is internally
                    # valid. A frozen study cannot silently substitute new evidence.
                    ws = Path(package["workspace"])
                    from . import manifest
                    current = json.loads((ws / manifest.MANIFEST_FILE).read_text(encoding="utf-8"))
                    if current.get("manifest_hash") != package["package_digest"]:
                        raise BuyerError("package digest changed")
                    if current.get("family_id") != family:
                        raise BuyerError("package family mismatch")
                    pw.ingest_package(tmp, vendor_id, ws)
                except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                    lines.append(f"- {_md(vendor_id)} / {_md(family)}: package missing, changed or invalid; excluded.")
                    continue
                lines.append(f"- {_md(vendor_id)} / {_md(family)}: package integrity reverified.")
        lines += ["", "### Clinical evaluation report", "",
                  pw.export_dossier(tmp), ""]
    return lines


def dossier(directory, output_file: str) -> dict:
    output = Path(output_file).expanduser().resolve()
    if output.exists():
        raise BuyerError(f"refusing to overwrite existing dossier {output}; choose a new file")
    content = render_dossier(directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as f:
        os.chmod(output, 0o600)
        f.write(content)
    return {"path": str(output), "status": "exported",
            "decision_grade": False, "network_calls": 0}


def contract() -> dict:
    """Machine-readable discovery for Claude Code, CLI agents and future MCP servers."""
    return {
        "schema_version": SCHEMA_VERSION, "transport": "local_subprocess_json",
        "network_calls": 0, "network_policy": "none except explicitly authorized job-execute", "data_storage": "local_only",
        "entrypoint": "clinical-ai-eval buyer",
        "actions": ["contract", "init", "vendor-add", "evidence-add", "status", "dossier", "serve", "questionnaire-template", "questionnaire-import", "research", "plan-preview", "plan-show", "rehearsal-run", "rehearsal-show", "manual-packs", "manual-prepare", "manual-template", "manual-import", "manual-responses", "assessment-panels", "assessment-prepare", "assessment-check", "job-enqueue", "job-list", "job-status", "job-cancel", "job-execute", "review-template", "review-submit", "review-inventory"],
        "action_schemas": {
            "contract": {"required": [], "writes": False},
            "init": {"required": ["path", "--name"],
                     "optional": ["--intended-use", "--setting", "--jurisdiction"],
                     "writes": True},
            "vendor-add": {"required": ["path", "--vendor-id", "--name"], "writes": True},
            "evidence-add": {"required": ["path", "--vendor-id", "--kind", "--file"],
                             "optional": ["--title"], "writes": True},
            "status": {"required": ["path"], "writes": False},
            "questionnaire-template": {"required": ["path", "--vendor-id"], "writes": False},
            "research": {"required": ["path"], "writes": False},
            "plan-preview": {"required": ["path", "--file"], "writes": True},
            "plan-show": {"required": ["path", "--plan-id"], "writes": False},
            "rehearsal-run": {"required": ["path", "--plan-id"], "writes": True, "synthetic_only": True},
            "rehearsal-show": {"required": ["path", "--job-id"], "writes": False},
            "manual-packs": {"required": ["path"], "writes": False},
            "manual-prepare": {"required": ["path", "--plan-id", "--pack-id"], "writes": True},
            "manual-template": {"required": ["path", "--exchange-id"], "writes": False},
            "manual-import": {"required": ["path", "--file"], "writes": True},
            "manual-responses": {"required": ["path", "--submission-id"], "writes": False,
                                 "unscored": True},
            "assessment-panels": {"required": ["path"], "writes": False},
            "assessment-prepare": {"required": ["path", "--file"], "writes": True,
                                   "executes": False},
            "assessment-check": {"required": ["path", "--assessment-id"], "writes": False},
            "job-enqueue": {"required": ["path", "--assessment-id"], "writes": True},
            "job-list": {"required": ["path"], "writes": False},
            "job-status": {"required": ["path", "--job-id"], "writes": False},
            "job-cancel": {"required": ["path", "--job-id"], "writes": True},
            "job-execute": {"required": ["path", "--job-id"], "optional": ["--allow-model-calls"],
                            "writes": True, "may_call_models": True},
            "review-template": {"required": ["path", "--job-id", "--family-id"], "writes": False},
            "review-submit": {"required": ["path", "--file"], "writes": True},
            "review-inventory": {"required": ["path", "--job-id", "--family-id"], "writes": False},
            "questionnaire-import": {"required": ["path", "--vendor-id", "--file"], "writes": True},
            "dossier": {"required": ["path", "--out"], "writes": True},
            "serve": {"required": ["path"], "optional": ["--port", "--no-browser"],
                      "writes": True, "interactive": True,
                      "scope": "loopback_only"},
        },
        "evidence_kinds": list(EVIDENCE_KINDS),
        "max_document_bytes": MAX_DOCUMENT_BYTES,
        "notes": [
            "Only job-execute with explicit authorization can call configured real judge endpoints; all other buyer actions are local.",
            "An external agent may still send data to its model provider; keep private clinical data out of agent prompts unless approved.",
            "Clinical testing and verified comparisons use the separate existing run, adjudicate, and procurement commands.",
            "This module does not certify products or authorize clinical deployment.",
        ],
    }
