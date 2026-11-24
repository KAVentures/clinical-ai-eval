"""Read-only local research browser data, shared by CLI and web.

Use the canonical capability registry and manifest verifier; do not duplicate
scientific scoring or expose raw patient text, secrets, or absolute paths.
Only operator-staged workspaces under the buyer project's research-runs/ are
visible. This is not an authentication boundary for a multi-user deployment.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import buyer_agent as buyer, capabilities, manifest

MAX_RUNS = 200
MAX_FILES = 3000
MAX_BYTES = 128 * 1024 * 1024


def _safe_tree(path: Path):
    """Reject links, special files and unbounded inputs before the verifier reads."""
    total = 0
    count = 0
    for p in path.rglob("*"):
        count += 1
        if count > MAX_FILES:
            raise buyer.BuyerError("research package exceeds file limit")
        if p.is_symlink() or not (p.is_file() or p.is_dir()):
            raise buyer.BuyerError("research package contains a link or special file")
        if p.is_file():
            total += p.stat().st_size
            if total > MAX_BYTES:
                raise buyer.BuyerError("research package exceeds byte limit")


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    return not value.startswith("/") and all(x not in ("", ".", "..") for x in value.split("/"))


def _preflight(ws: Path):
    """Validate the manifest's read scope before invoking existing verification.

    Integrity is not authenticity: a malicious author can recompute hashes.
    Required core entries cannot be omitted or redirected. A newer unsupported
    manifest is not silently accepted by the browser.
    """
    _safe_tree(ws)
    obj = json.loads((ws / manifest.MANIFEST_FILE).read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or obj.get("schema") != "assessment_manifest/1":
        raise buyer.BuyerError("unsupported assessment manifest")
    artifacts = obj.get("artifacts")
    definitions = obj.get("definitions")
    review = obj.get("review_inputs")
    if not all(isinstance(v, dict) for v in (artifacts, definitions, review)):
        raise buyer.BuyerError("invalid manifest mappings")
    if set(artifacts) != set(manifest.ARTIFACTS):
        raise buyer.BuyerError("manifest artifact inventory differs from this build")
    for key, (rel, required) in manifest.ARTIFACTS.items():
        rec = artifacts[key]
        if (not isinstance(rec, dict) or rec.get("path") != rel
                or rec.get("required") is not required):
            raise buyer.BuyerError("manifest artifact mapping differs from this build")
        if rec.get("present") is not (rec.get("sha256") is not None):
            raise buyer.BuyerError("inconsistent manifest artifact presence")
        if rec.get("present") and not (ws / rel).is_file():
            raise buyer.BuyerError("previously recorded artifact is missing")
        if rec.get("sha256") is not None and (
                not isinstance(rec["sha256"], str) or len(rec["sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in rec["sha256"])):
            raise buyer.BuyerError("invalid artifact digest")
    family = obj.get("family_id")
    if not isinstance(family, str) or not buyer.IDENTIFIER.fullmatch(family):
        raise buyer.BuyerError("invalid research family identifier")
    known = {r["family_id"]: r for r in capabilities.table()}
    if family not in known:
        raise buyer.BuyerError("unknown research family")
    executor = obj.get("executor") or "generic_paired_text"
    if known[family].get("executor") != executor:
        raise buyer.BuyerError("research family and executor mismatch")
    if not isinstance(executor, str) or executor not in manifest.EXECUTOR_DEFINITION_SOURCES:
        raise buyer.BuyerError("unknown research executor")
    expected = set(manifest.DEFINITION_SOURCES)
    expected.update(manifest.EXECUTOR_DEFINITION_SOURCES[executor])
    expected.add(f"tests/{family}/family.yaml")
    if set(definitions) != expected or not all(definitions.values()):
        raise buyer.BuyerError("missing or unexpected scientific definition bindings")
    for rel in review:
        if not _relative(rel):
            raise buyer.BuyerError("unsafe review input path")
    return obj


def verified_bytes(ws: Path, metadata: dict, key: str) -> bytes:
    """Read a single byte snapshot with the canonical manifest content hash.

    Callers must still verify the complete package. This prevents a concurrent replacement from changing the semantic content
    being interpreted. Equivalent JSON formatting is intentionally accepted.
    """
    if metadata.get("manifest_hash") != manifest.fingerprint(metadata):
        raise buyer.BuyerError("manifest fingerprint mismatch")
    if key not in manifest.ARTIFACTS:
        raise buyer.BuyerError("unknown package artifact")
    relative, _ = manifest.ARTIFACTS[key]
    entry = metadata["artifacts"][key]
    if entry.get("path") != relative or not entry.get("present") or not entry.get("sha256"):
        raise buyer.BuyerError("required analysis artifact was absent")
    path = ws / relative
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise buyer.BuyerError("invalid analysis artifact")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or manifest.hash_content(raw, path.suffix) != entry["sha256"]:
        raise buyer.BuyerError("analysis artifact changed since verification")
    return raw


def inspect_run(root, run_id: str) -> dict:
    if not buyer.IDENTIFIER.fullmatch(run_id or ""):
        raise buyer.BuyerError("invalid research run identifier")
    runs = Path(root).expanduser().resolve() / "research-runs"
    ws = runs / run_id
    if runs.is_symlink() or ws.is_symlink() or not ws.is_dir():
        raise buyer.BuyerError("research run not staged locally")
    row = {"run_id": run_id, "verdict": "INVALID", "decision_grade": False}
    try:
        obj = _preflight(ws)
        verification = manifest.verify_manifest(ws)
        verdict = verification.get("verdict")
        if verdict not in (manifest.VALID, manifest.INVALID, manifest.INCOMPLETE):
            verdict = manifest.INVALID
        row.update(family_id=obj["family_id"], verdict=verdict,
                   manifest_hash=obj.get("manifest_hash"),
                   tool_version_recorded=obj.get("tool_version"),
                   n_ok=verification.get("n_ok", 0),
                   n_failed=verification.get("n_failed", 0),
                   n_missing=verification.get("n_missing", 0))
        # Raw clinical content and verifier diagnostics (which may contain paths)
        # never appear in this read-only dashboard.
        if verdict == manifest.VALID:
            analysis = json.loads(verified_bytes(ws, obj, "analysis"))
            from . import claim
            recorded = analysis.get("claim_authority") or {}
            derived = claim.compute(
                recorded.get("project_mode"), recorded.get("run_conformance"),
                recorded.get("family_maturity"), recorded.get("case_pack_authority", "unknown"),
                recorded.get("target_provenance", "unknown"))
            if analysis.get("family_id") != obj["family_id"] or derived.effective_claim != recorded.get("effective_claim"):
                row.update(verdict="INVALID", reason="claim authority or family mismatch")
            else:
                row["recorded_claim"] = derived.effective_claim
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, RecursionError):
        row.update(verdict="INVALID", reason="package preflight or verification failed")
    return row


def overview(directory) -> dict:
    root = Path(directory).expanduser().resolve()
    buyer.load(root)
    runs = root / "research-runs"
    if runs.is_symlink():
        raise buyer.BuyerError("research-runs must not be a symbolic link")
    rows = []
    if runs.is_dir():
        for path in sorted(runs.iterdir()):
            if len(rows) >= MAX_RUNS:
                break
            if path.is_symlink() or not path.is_dir() or not buyer.IDENTIFIER.fullmatch(path.name):
                continue
            rows.append(inspect_run(root, path.name))
    return {"capabilities": capabilities.table(), "runs": rows,
            "limit": MAX_RUNS, "network_calls": 0, "read_only": True,
            "note": "Package verification checks integrity, not authenticity or clinical validity."}
