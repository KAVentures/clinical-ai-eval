"""Small shared utilities. `stable_hash_text` and `utc_now_iso` reproduce the
helpers that clinical-evidence-sufficiency-llm/src/utils.py provides to the
canonical perturbation module (§11), so the inherited transforms keep producing
the SAME stable content-hash manifest rows. `read_toml` is a minimal TOML reader
(stdlib only; Python 3.9 has no tomllib) sufficient for configs/judge_panel.toml.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path


def stable_hash_text(text: str) -> str:
    """Deterministic content hash (matches the upstream manifest hash contract)."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_root() -> Path:
    """clinical_ai_eval/ directory (parent of the caeval package). Data files
    (tests/, prompts/, configs/, selection_rules.yaml) are resolved relative to
    this, so the harness runs from the checkout. Override with CAEVAL_HOME if the
    package is installed elsewhere than its data."""
    env = os.environ.get("CAEVAL_HOME")
    return Path(env).resolve() if env else Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Minimal TOML reader: bare key=value, quoted strings, ints, floats, bools,
# inline nothing fancy, and [[array.of.tables]]. Enough for judge_panel.toml.
# --------------------------------------------------------------------------
def _coerce(v: str):
    v = v.strip()
    if v.startswith('"') and v.endswith('"'):
        return v[1:-1]
    if v.startswith("'") and v.endswith("'"):
        return v[1:-1]
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def read_toml(path: str | os.PathLike) -> dict:
    """Parse the small subset of TOML the harness config uses.

    Supports top-level `key = value` and repeated `[[name]]` array-of-tables.
    Comments (`#`) and blank lines are ignored. Not a general TOML parser.
    """
    result: dict = {}
    current_array_key = None
    current_table: dict | None = None
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[[") and line.endswith("]]"):
            current_array_key = line[2:-2].strip()
            current_table = {}
            result.setdefault(current_array_key, []).append(current_table)
            continue
        if line.startswith("[") and line.endswith("]"):
            # single table [name] -> nested dict
            name = line[1:-1].strip()
            current_array_key = None
            current_table = {}
            result[name] = current_table
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        # strip trailing inline comment (only outside quotes; our values are simple)
        val = val.split("#", 1)[0] if not (val.strip().startswith(('"', "'"))) else val
        target = current_table if current_table is not None else result
        target[key.strip()] = _coerce(val)
    return result
