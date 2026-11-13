"""Resumable API runner for prepared Branch-Intersection Safety requests.

Uses the repository's existing provider layer so credentials remain in the existing
local key file/environment workflow. Raw model text and non-secret usage metadata are
persisted; credentials are never written to outputs.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import providers


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run(requests_path: Path, out: Path, *, provider: str, model: str,
        high: bool, max_tokens: int, max_calls: int | None = None) -> dict:
    requests = _read_jsonl(requests_path)
    completed: set[str] = set()
    if out.exists():
        for row in _read_jsonl(out):
            if row.get("request_id"):
                completed.add(row["request_id"])
    keys = providers.load_keys()
    needed_key = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "xai": "XAI_API_KEY",
    }.get(provider)
    if not needed_key:
        raise ValueError("provider must be openai, anthropic, google, or xai")
    if needed_key not in keys:
        raise ValueError(f"missing {needed_key} in the configured local keys file")

    calls = failures = 0
    for req in requests:
        if req["request_id"] in completed:
            continue
        if max_calls is not None and calls >= max_calls:
            break
        text, meta = providers.call(
            provider, model, req["system"], req["user"], keys,
            high=high, max_tokens=max_tokens,
        )
        row = {
            **{k: req[k] for k in ("request_id", "case_id", "stage", "presentation", "arm", "repeat")},
            "provider": provider,
            "model": model,
            "reasoning_high": high,
            "max_tokens": max_tokens,
            "response_text": text if text is not None else "",
            "call_ok": text is not None,
            "meta": meta or {},
        }
        _append(out, row)
        calls += 1
        failures += text is None
    return {
        "requests_total": len(requests),
        "already_completed": len(completed),
        "calls_this_run": calls,
        "failures_this_run": failures,
        "out": str(out),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m caeval.branch_intersection_run")
    p.add_argument("--requests", required=True)
    p.add_argument("--provider", choices=["openai", "anthropic", "google", "xai"], required=True)
    p.add_argument("--model", required=True, help="Exact provider model identifier; record it verbatim for reproducibility.")
    p.add_argument("--out", required=True)
    p.add_argument("--max-tokens", type=int, default=800)
    p.add_argument("--high", action="store_true", help="Use provider high/adaptive reasoning where supported.")
    p.add_argument("--max-calls", type=int, help="Cost-control stop: run at most this many new calls.")
    args = p.parse_args(argv)
    result = run(
        Path(args.requests), Path(args.out), provider=args.provider, model=args.model,
        high=args.high, max_tokens=args.max_tokens, max_calls=args.max_calls,
    )
    print(json.dumps(result, indent=2))
    return 1 if result["failures_this_run"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
