#!/usr/bin/env python3
"""Select the random-equivalent physician calibration sample before judge scoring.

For each of four target models, choose 60/150 source cases by deterministic hash
and include both original and perturbed responses. Selection depends only on IDs,
never on model response content or automated judge labels.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

SEED = "clinical-ai-eval-physician-validation-v1|physician-calibration"
N_CASES_PER_TARGET = 60


def rank(target_id: str, case_id: str) -> str:
    return hashlib.sha256(f"{SEED}|{target_id}|{case_id}".encode()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--responses", required=True, type=Path, help="Private target_responses JSONL")
    p.add_argument("--vault", required=True, type=Path)
    p.add_argument("--public-manifest", required=True, type=Path)
    args = p.parse_args()

    rows = read_jsonl(args.responses)
    by_target_case = {}
    for r in rows:
        key = (str(r["target_id"]), str(r["case_id"]), str(r["presentation"]))
        if key in by_target_case:
            raise RuntimeError(f"duplicate target response {key}")
        by_target_case[key] = r

    targets = sorted({str(r["target_id"]) for r in rows})
    if len(targets) != 4:
        raise RuntimeError(f"expected 4 target models, found {targets}")

    selected_private = []
    public = []
    for target_id in targets:
        cases = sorted(
            {str(r["case_id"]) for r in rows if str(r["target_id"]) == target_id},
            key=lambda cid: rank(target_id, cid),
        )
        if len(cases) < 150:
            raise RuntimeError(f"target {target_id} has only {len(cases)} source cases; expected 150")
        chosen = cases[:N_CASES_PER_TARGET]
        for cid in chosen:
            for presentation in ("original", "perturbed"):
                key = (target_id, cid, presentation)
                if key not in by_target_case:
                    raise RuntimeError(f"missing response {key}")
                r = by_target_case[key]
                review_unit_id = f"cal::{r['response_id']}"
                selected_private.append({
                    "review_unit_id": review_unit_id,
                    "source_id": r["source_id"],
                    "case_id": cid,
                    "primary_family": r["primary_family"],
                    "presentation": presentation,
                    "target_id_blinded": "",
                    "case_text": r["input_text"],
                    "response_text": r["response_text"],
                    "response_id_internal": r["response_id"],
                })
                public.append({
                    "review_unit_id": review_unit_id,
                    "source_id": r["source_id"],
                    "case_id": cid,
                    "primary_family": r["primary_family"],
                    "presentation": presentation,
                    "target_id_internal": target_id,
                    "response_id": r["response_id"],
                    "selection_rank_sha256": rank(target_id, cid),
                    "sampling_frame": "60_source_cases_per_target_both_presentations",
                })

    if len(selected_private) != 480:
        raise AssertionError(f"expected 480 review units, got {len(selected_private)}")

    private_path = args.vault / "review" / "physician_calibration_units.private.jsonl"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    with private_path.open("w", encoding="utf-8") as f:
        for r in selected_private:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    args.public_manifest.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "review_unit_id", "source_id", "case_id", "primary_family", "presentation",
        "target_id_internal", "response_id", "selection_rank_sha256", "sampling_frame",
    ]
    with args.public_manifest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(public, key=lambda r: r["review_unit_id"]))

    print(f"Selected {len(selected_private)} blinded physician-calibration units")
    print(f"Private units: {private_path}")
    print(f"Public selection manifest: {args.public_manifest}")


if __name__ == "__main__":
    main()
