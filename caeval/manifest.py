"""Assessment manifest — content addresses for every decision-bearing artifact.

Prior binding hashed POINTERS (a case-set hash over `item_id` + `input_text`, a
review manifest over cell ids). Anything not in those two projections — hidden
defect manifests, hazards, expected behaviour, acceptance criteria, the family
YAML, judge prompts, panel config, raw responses, scores, adjudication — could
change without invalidating anything.

`assessment_manifest.json` is the single immutable record of WHAT was assessed.
Every artifact is hashed over its full semantic content in canonical JSON, so a
third party can recompute each hash from the files alone and detect any edit.

    build_manifest(workspace)  ->  write assessment_manifest.json
    verify_manifest(workspace) ->  VALID | INVALID | INCOMPLETE + per-artifact detail

Design rules:
  * hash CONTENT, never filenames or ids;
  * canonical JSON (sorted keys) so formatting cannot change a hash;
  * a missing REQUIRED artifact is INCOMPLETE, not VALID;
  * a present artifact whose hash differs is INVALID;
  * the manifest's own hash is computed over everything except itself.
"""
from __future__ import annotations

import json
from pathlib import Path

from .util import stable_hash_text, utc_now_iso
from .version import __version__

MANIFEST_FILE = "assessment_manifest.json"

# artifact key -> (relative path, required?)
ARTIFACTS = {
    "run_meta":          ("run_meta.json", True),
    "plan_binding":      ("plan_binding.json", False),
    "responses":         ("responses.jsonl", True),
    "results":           ("results.jsonl", True),
    "analysis":          ("analysis.json", True),
    "provenance":        ("provenance.json", True),
    "review_manifest":   ("review_manifest.json", False),
    "human_review":      ("human_review.csv", False),
    "validity_review":   ("validity_review.csv", False),
    "adjudication":      ("adjudication/adjudication_report.json", False),
    "final_report":      ("final_report.md", True),
    "limitations":       ("limitations.md", True),
}

# Repo-level definitions the run depended on. Recorded so a verifier can tell
# whether the assessment was produced by the same rules it claims.
DEFINITION_SOURCES = ("selection_rules.yaml", "prompts/judge_prompt.txt",
                      "configs/judge_panel.toml", "schemas/clinical_certificate.schema.json")

VALID, INVALID, INCOMPLETE = "VALID", "INVALID", "INCOMPLETE"


def hash_file(path: Path) -> str | None:
    """Content hash. JSON/JSONL are canonicalized so reformatting cannot alter it."""
    if not path.exists():
        return None
    raw = path.read_bytes()
    if path.suffix == ".json":
        try:
            return stable_hash_text(json.dumps(json.loads(raw.decode()), sort_keys=True, default=str))
        except Exception:  # noqa: BLE001 — malformed JSON hashes as bytes
            pass
    if path.suffix == ".jsonl":
        try:
            rows = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
            return stable_hash_text(json.dumps(rows, sort_keys=True, default=str))
        except Exception:  # noqa: BLE001
            pass
    return stable_hash_text(raw.decode(errors="replace"))


def _semantic_case_hash(responses_path: Path) -> dict:
    """Hash the FULL semantic case object, not just item_id + input_text.

    The prior `hash_case_set` covered only those two fields, so hidden defect
    labels, hazards, expected behaviour and acceptance criteria could all change
    without invalidating the binding.
    """
    if not responses_path.exists():
        return {}
    rows = [json.loads(l) for l in responses_path.read_text().splitlines() if l.strip()]
    facing, hidden = [], []
    for r in rows:
        facing.append({k: r.get(k) for k in ("cell_id", "item_id", "kind", "input_text")})
        hidden.append({k: r.get(k) for k in (
            "cell_id", "perturbation_type", "perturbation_id", "transform", "severity",
            "expected_missing_evidence", "validity_valid", "determinacy", "task_type")})
    return {
        "facing_input_hash": stable_hash_text(json.dumps(sorted(facing, key=lambda x: str(x["cell_id"])),
                                                         sort_keys=True, default=str)),
        "hidden_manifest_hash": stable_hash_text(json.dumps(sorted(hidden, key=lambda x: str(x["cell_id"])),
                                                            sort_keys=True, default=str)),
        "n_cells": len(rows),
    }


def build_manifest(workspace, repo_root_path=None) -> dict:
    """Compute and write the immutable assessment manifest."""
    ws = Path(workspace)
    from .util import repo_root
    root = Path(repo_root_path) if repo_root_path else repo_root()

    artifacts = {}
    for key, (rel, required) in ARTIFACTS.items():
        h = hash_file(ws / rel)
        artifacts[key] = {"path": rel, "required": required,
                          "sha256": h, "present": h is not None}

    definitions = {}
    for rel in DEFINITION_SOURCES:
        h = hash_file(root / rel)
        definitions[rel] = h

    # family definition actually used
    family_id = None
    meta_p = ws / "run_meta.json"
    if meta_p.exists():
        family_id = json.loads(meta_p.read_text()).get("family_id")
    if family_id:
        definitions[f"tests/{family_id}/family.yaml"] = hash_file(root / "tests" / family_id / "family.yaml")

    manifest = {
        "schema": "assessment_manifest/1",
        "created_at": utc_now_iso(),
        "tool_version": __version__,
        "family_id": family_id,
        "artifacts": artifacts,
        "definitions": definitions,
        "case_content": _semantic_case_hash(ws / "responses.jsonl"),
    }
    manifest["manifest_hash"] = fingerprint(manifest)
    (ws / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2))
    return manifest


def fingerprint(manifest: dict) -> str:
    payload = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    return stable_hash_text(json.dumps(payload, sort_keys=True, default=str))


def verify_manifest(workspace, repo_root_path=None) -> dict:
    """Independently re-derive every hash. Returns a structured verdict."""
    ws = Path(workspace)
    mp = ws / MANIFEST_FILE
    if not mp.exists():
        return {"verdict": INCOMPLETE, "reason": f"no {MANIFEST_FILE} in {ws}",
                "checks": [], "n_ok": 0, "n_failed": 0, "n_missing": 0}

    manifest = json.loads(mp.read_text())
    checks, tampered, missing = [], [], []

    # 1. the manifest must not have been edited
    recomputed = fingerprint(manifest)
    manifest_ok = recomputed == manifest.get("manifest_hash")
    checks.append({"artifact": MANIFEST_FILE, "status": "ok" if manifest_ok else "MODIFIED",
                   "detail": "" if manifest_ok else
                   f"recorded {str(manifest.get('manifest_hash'))[:12]} != recomputed {recomputed[:12]}"})
    if not manifest_ok:
        tampered.append(MANIFEST_FILE)

    # 2. every artifact
    for key, rec in manifest.get("artifacts", {}).items():
        actual = hash_file(ws / rec["path"])
        if actual is None:
            status = "MISSING" if rec.get("required") else "absent (optional)"
            checks.append({"artifact": rec["path"], "status": status, "detail": ""})
            if rec.get("required"):
                missing.append(rec["path"])
            continue
        if rec.get("sha256") is None:
            checks.append({"artifact": rec["path"], "status": "ADDED",
                           "detail": "present now but absent when the manifest was built"})
            tampered.append(rec["path"])
        elif actual != rec["sha256"]:
            checks.append({"artifact": rec["path"], "status": "MODIFIED",
                           "detail": f"{rec['sha256'][:12]} -> {actual[:12]}"})
            tampered.append(rec["path"])
        else:
            checks.append({"artifact": rec["path"], "status": "ok", "detail": ""})

    # 3. the definitions the run depended on
    from .util import repo_root
    root = Path(repo_root_path) if repo_root_path else repo_root()
    for rel, recorded in (manifest.get("definitions") or {}).items():
        actual = hash_file(root / rel)
        if actual is None:
            checks.append({"artifact": rel, "status": "definition missing",
                           "detail": "cannot confirm the run used these rules"})
            missing.append(rel)
        elif recorded and actual != recorded:
            checks.append({"artifact": rel, "status": "DEFINITION CHANGED",
                           "detail": f"{recorded[:12]} -> {actual[:12]} "
                                     f"(the assessment was produced under different rules)"})
            tampered.append(rel)
        else:
            checks.append({"artifact": rel, "status": "ok", "detail": ""})

    verdict = INVALID if tampered else (INCOMPLETE if missing else VALID)
    return {"verdict": verdict, "checks": checks,
            "n_ok": sum(1 for c in checks if c["status"] == "ok"),
            "n_failed": len(tampered), "n_missing": len(missing),
            "tampered": tampered, "missing": missing,
            "tool_version_recorded": manifest.get("tool_version"),
            "tool_version_now": __version__,
            "family_id": manifest.get("family_id")}
