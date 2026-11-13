"""Explicit, bounded and resumable provider execution for reference-action cases.

This is an opt-in NETWORK operation: clinical text is sent to the selected
provider. Never perform it during normal fixture tests or without the caller's
explicit authorization and a required maximum number of API calls.

Safety invariants: no reference labels in sent prompts, no provider error
mapped to an empty clinically-safe answer, no response from an older casepack
or different provider/model silently reused, atomic progress after each call.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import providers, reference_actions

PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
}
TERMINAL_STATES = {"complete", "provider_error", "invalid_schema"}


def _atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    """Persist progress after each call without partial/truncated JSONL files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_name = stream.name
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def _key(row: dict) -> tuple:
    if not isinstance(row, dict):
        raise ValueError("saved provider response must be a JSON object")
    repeat = row.get("repeat")
    if type(repeat) is not int:
        raise ValueError("saved provider response repeat must be an integer")
    return row.get("case_id"), row.get("condition_id"), repeat


def _validate_resumption(rows: list, expected: dict, provider: str,
                         model: str, pack_sha256: str) -> dict:
    indexed = {}
    for row in rows:
        key = _key(row)
        if key not in expected:
            raise ValueError(f"saved provider response has unknown case/condition/repeat: {key}")
        if key in indexed:
            raise ValueError(f"duplicate saved provider response: {key}")
        if row.get("pack_sha256") != pack_sha256:
            raise ValueError("saved provider responses are for a different casepack hash")
        if row.get("provider") != provider or row.get("model") != model:
            raise ValueError("saved provider responses were produced by another provider/model")
        if row.get("execution_status") not in TERMINAL_STATES:
            raise ValueError("saved provider response has unknown execution_status")
        indexed[key] = row
    return indexed


def run(pack: dict, output_path: str | Path, *, provider: str, model: str,
        max_calls: int, repeats: int = 1, max_tokens: int = 800,
        high: bool = False, confirm_remote: bool = False) -> dict:
    """Run at most max_calls explicit paid API requests and save/resume safely.

    Every completed response must pass the same structured-action parser as the
    downstream reference scorer. Failed/invalid attempts can be retried by
    another explicit invocation; previous successful attempts are never rerun.
    """
    if not confirm_remote:
        raise ValueError("remote inference requires explicit --confirm-send-to-provider")
    if provider not in PROVIDER_KEYS:
        raise ValueError(f"unsupported provider {provider!r}")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("exact provider model identifier is required")
    if type(max_calls) is not int or max_calls < 1:
        raise ValueError("--max-calls must be a positive integer")
    if type(max_tokens) is not int or not 1 <= max_tokens <= 12000:
        raise ValueError("--max-tokens must be an integer from 1 to 12000")
    if type(high) is not bool:
        raise ValueError("high reasoning flag must be boolean")

    expected_requests = reference_actions.make_requests(pack, repeats=repeats)
    cases = {c["case_id"]: {a["id"] for a in c["candidate_actions"]}
             for c in pack["cases"]}
    expected = {_key(r): r for r in expected_requests}
    descriptor = reference_actions.validate_pack(pack)
    path = Path(output_path)
    existing = reference_actions.read_jsonl(path) if path.exists() else []
    indexed = _validate_resumption(existing, expected, provider, model,
                                    descriptor["pack_sha256"])

    # Metadata alone cannot certify a complete result. Validate the actual
    # action partition again before skipping an already-completed API request.
    completed = set()
    for key, row in indexed.items():
        if row["execution_status"] != "complete":
            continue
        try:
            reference_actions.parse_response(row, cases[key[0]])
        except (ValueError, TypeError):
            continue
        completed.add(key)
    initial_completed = len(completed)

    # Validate all resume data BEFORE contacting any external provider.
    needed = [req for req in expected_requests if _key(req) not in completed]
    if not needed:
        return {
            "provider": provider, "model": model,
            "pack_sha256": descriptor["pack_sha256"],
            "total_requests": len(expected_requests),
            "previously_complete": initial_completed,
            "attempted_now": 0, "completed_now": 0, "failed_now": 0,
            "remaining_incomplete": 0, "out": str(path),
        }

    keys = providers.load_keys()
    if not keys.get(PROVIDER_KEYS[provider]):
        raise ValueError(f"Missing configured {PROVIDER_KEYS[provider]} credential")

    attempts = successes = failures = 0
    for req in needed:
        if attempts >= max_calls:
            break
        key = _key(req)
        try:
            text, _meta = providers.call(
                provider, model, req["system_prompt"], req["user_prompt"], keys,
                high=high, max_tokens=max_tokens,
            )
            if not isinstance(text, str) or not text.strip():
                raise ValueError("no provider model response")
            try:
                reference_actions.parse_response({"response_text": text}, cases[key[0]])
                status = "complete"
            except (ValueError, TypeError):
                status = "invalid_schema"
        except Exception:  # noqa: BLE001
            # Provider responses and exception messages might include secrets or
            # sensitive clinical content. No exception text is persisted.
            status, text = "provider_error", None

        indexed[key] = {
            "case_id": req["case_id"],
            "condition_id": req["condition_id"],
            "repeat": req["repeat"],
            "pack_sha256": descriptor["pack_sha256"],
            "provider": provider,
            "model": model,
            "execution_status": status,
            "response_text": text,
        }
        attempts += 1
        if status == "complete":
            successes += 1
        else:
            failures += 1
        # Save exactly one row per (case, condition, repeat), in the original
        # planned order. A crash loses at most the in-flight provider call.
        _atomic_write_jsonl(path, [indexed[_key(r)] for r in expected_requests
                                   if _key(r) in indexed])

    remaining = len(expected_requests) - initial_completed - successes
    return {
        "provider": provider, "model": model,
        "pack_sha256": descriptor["pack_sha256"],
        "total_requests": len(expected_requests),
        "previously_complete": initial_completed,
        "attempted_now": attempts,
        "completed_now": successes,
        "failed_now": failures,
        "remaining_incomplete": remaining,
        "out": str(path),
        "note": ("Paid requests require explicit invocation; remaining work is "
                 "incomplete, never treated as safely scored."),
    }
