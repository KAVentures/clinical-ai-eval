#!/usr/bin/env python3
"""Score frozen target responses with the prespecified automated judge panel.

Primary mode is blinded. Cued/rubric-aware judges are run only when explicitly
requested and are stored as separate endpoints, never extra votes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import yaml

from caeval.providers import load_keys, score_response


def read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def sha_obj(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--responses", required=True, type=Path)
    p.add_argument("--casepack", required=True, type=Path)
    p.add_argument("--models", required=True, type=Path)
    p.add_argument("--vault", required=True, type=Path)
    p.add_argument("--keys", type=Path)
    p.add_argument("--public-manifest", required=True, type=Path)
    p.add_argument("--include-secondary", action="store_true")
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()

    cfg = yaml.safe_load(args.models.read_text(encoding="utf-8"))
    if not cfg.get("frozen"):
        raise RuntimeError("model panel must be frozen before judge execution")
    judges = list(cfg.get("primary_judges") or [])
    if args.include_secondary:
        judges.extend(cfg.get("secondary_judges") or [])
    if len({j["judge_id"] for j in judges}) != len(judges):
        raise RuntimeError("duplicate judge_id")

    keys = load_keys(str(args.keys)) if args.keys else load_keys()
    responses = read_jsonl(args.responses)
    cases = {str(c["case_id"]): c for c in read_jsonl(args.casepack)}

    private_out = args.vault / "judges" / "judge_scores.private.jsonl"
    private_out.parent.mkdir(parents=True, exist_ok=True)
    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)

    completed = set()
    existing_public = []
    if args.resume and private_out.exists():
        with private_out.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    completed.add(str(r["judge_evaluation_id"]))
    if args.resume and args.public_manifest.exists():
        with args.public_manifest.open(newline="", encoding="utf-8") as f:
            existing_public = list(csv.DictReader(f))

    new_public = []
    mode = "a" if args.resume else "w"
    with private_out.open(mode, encoding="utf-8") as pf:
        for r in responses:
            case = cases[str(r["case_id"])]
            if r["presentation"] == "original":
                perturbation_type = "original"
                changed_evidence = ""
            else:
                perturbation_type = case["primary_family"]
                changed_evidence = case.get("changed_evidence", "")

            judge_record = {
                "input_text": r["input_text"],
                "response_text": r["response_text"],
                "perturbation_type": perturbation_type,
                "expected_missing_evidence": changed_evidence,
            }
            for j in judges:
                eid = f"{r['response_id']}::{j['judge_id']}"
                if eid in completed:
                    continue
                jcfg = {
                    "name": j["judge_id"],
                    "provider": j["provider"],
                    "model": j["model"],
                    "mode": j.get("mode", "blinded"),
                    "mock": False,
                }
                score, meta = score_response(jcfg, judge_record, keys)
                status = "ok" if score is not None else "judge_failure"
                private = {
                    "judge_evaluation_id": eid,
                    "response_id": r["response_id"],
                    "case_id": r["case_id"],
                    "source_id": r["source_id"],
                    "presentation": r["presentation"],
                    "primary_family": r["primary_family"],
                    "target_id": r["target_id"],
                    "target_provider": r["target_provider"],
                    "judge_id": j["judge_id"],
                    "judge_provider": j["provider"],
                    "judge_model": j["model"],
                    "judge_mode": j.get("mode", "blinded"),
                    "score": score,
                    "status": status,
                    "provider_meta": meta,
                }
                pf.write(json.dumps(private, ensure_ascii=False) + "\n")
                new_public.append({
                    "judge_evaluation_id": eid,
                    "response_id": r["response_id"],
                    "case_id": r["case_id"],
                    "presentation": r["presentation"],
                    "primary_family": r["primary_family"],
                    "target_id": r["target_id"],
                    "target_provider": r["target_provider"],
                    "judge_id": j["judge_id"],
                    "judge_provider": j["provider"],
                    "judge_model": j["model"],
                    "judge_mode": j.get("mode", "blinded"),
                    "score_sha256": sha_obj(score) if score is not None else "",
                    "status": status,
                })
                print(eid, status)

    fields = [
        "judge_evaluation_id", "response_id", "case_id", "presentation", "primary_family",
        "target_id", "target_provider", "judge_id", "judge_provider", "judge_model",
        "judge_mode", "score_sha256", "status",
    ]
    dedup = {r["judge_evaluation_id"]: r for r in [*existing_public, *new_public]}
    with args.public_manifest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(dedup.values(), key=lambda r: r["judge_evaluation_id"]))

    print(f"Private judge scores: {private_out}")
    print(f"Public judge manifest: {args.public_manifest}")


if __name__ == "__main__":
    main()
