#!/usr/bin/env python3
"""Run the locked target-model panel on the private 150-case pack.

Raw clinical inputs and target responses stay private. Public output contains only
stable IDs, model IDs, hashes, status, and usage metadata safe for reproducibility.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import yaml

from caeval.providers import call, load_keys


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--casepack", required=True, type=Path)
    p.add_argument("--models", required=True, type=Path)
    p.add_argument("--vault", required=True, type=Path)
    p.add_argument("--keys", type=Path)
    p.add_argument("--public-manifest", required=True, type=Path)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--allow-unfrozen", action="store_true", help="Dry-run only; do not use for study results")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()

    cfg = yaml.safe_load(args.models.read_text(encoding="utf-8"))
    if not cfg.get("frozen") and not args.allow_unfrozen:
        raise RuntimeError("model panel is not frozen; dry-run first, then set frozen: true before study execution")

    targets = cfg.get("target_models") or []
    if len({t.get("provider") for t in targets}) != len(targets):
        raise RuntimeError("primary target panel must contain one target per provider family")
    if len(targets) != 4:
        raise RuntimeError(f"protocol expects exactly four primary targets, got {len(targets)}")

    keys = load_keys(str(args.keys)) if args.keys else load_keys()
    cases = read_jsonl(args.casepack)
    if args.limit:
        cases = cases[: args.limit]
    system = (Path(__file__).resolve().parents[1] / "prompts" / "target_system_prompt.txt").read_text(encoding="utf-8")

    private_out = args.vault / "responses" / "target_responses.private.jsonl"
    private_out.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)

    completed = set()
    existing_public = []
    if args.resume and private_out.exists():
        with private_out.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    completed.add(str(r["response_id"]))
    if args.resume and args.public_manifest.exists():
        with args.public_manifest.open(newline="", encoding="utf-8") as f:
            existing_public = list(csv.DictReader(f))

    mode = "a" if args.resume else "w"
    new_public = []
    with private_out.open(mode, encoding="utf-8") as pf:
        for case in cases:
            for presentation in ("original", "perturbed"):
                input_text = case["original_case"] if presentation == "original" else case["perturbed_case"]
                for target in targets:
                    response_id = f"{case['case_id']}::{presentation}::{target['target_id']}"
                    if response_id in completed:
                        continue
                    effort = str(target.get("reasoning_effort", "provider_default")).lower()
                    high = effort in {"high", "xhigh", "max"}
                    text, meta = call(
                        target["provider"], target["model"], system, input_text, keys,
                        high=high, max_tokens=3500,
                    )
                    status = "ok" if text is not None and str(text).strip() else "api_or_empty_failure"
                    record = {
                        "response_id": response_id,
                        "case_id": case["case_id"],
                        "source_id": case["source_id"],
                        "primary_family": case["primary_family"],
                        "presentation": presentation,
                        "target_id": target["target_id"],
                        "target_provider": target["provider"],
                        "target_model": target["model"],
                        "input_text": input_text,
                        "response_text": text or "",
                        "status": status,
                        "provider_meta": meta,
                    }
                    pf.write(json.dumps(record, ensure_ascii=False) + "\n")
                    new_public.append({
                        "response_id": response_id,
                        "case_id": case["case_id"],
                        "source_id": case["source_id"],
                        "primary_family": case["primary_family"],
                        "presentation": presentation,
                        "target_id": target["target_id"],
                        "target_provider": target["provider"],
                        "target_model": target["model"],
                        "input_sha256": sha(input_text),
                        "response_sha256": sha(text or ""),
                        "status": status,
                        "usage_json": json.dumps((meta or {}).get("usage", {}), sort_keys=True),
                    })
                    print(response_id, status)

    fields = [
        "response_id", "case_id", "source_id", "primary_family", "presentation",
        "target_id", "target_provider", "target_model", "input_sha256",
        "response_sha256", "status", "usage_json",
    ]
    dedup = {r["response_id"]: r for r in [*existing_public, *new_public]}
    with args.public_manifest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(dedup.values(), key=lambda r: r["response_id"]))

    print(f"Private responses: {private_out}")
    print(f"Public response manifest: {args.public_manifest}")


if __name__ == "__main__":
    main()
