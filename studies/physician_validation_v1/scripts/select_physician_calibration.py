#!/usr/bin/env python3
"""Select the physician calibration sample before automated judge scoring.

For each of four target models, choose 60/150 source cases by deterministic hash
and include both original and perturbed responses. Selection depends only on IDs,
never on response content or judge labels. Reviewer-facing unit IDs are opaque and
do not encode target identity, source ID, or presentation.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

SEED = "clinical-ai-eval-physician-validation-v1|physician-calibration"
N_CASES_PER_TARGET = 60


def digest(*parts: str) -> str:
    return hashlib.sha256((SEED + "|" + "|".join(parts)).encode()).hexdigest()


def rank(target_id: str, case_id: str) -> str:
    return digest("sample-rank", target_id, case_id)


def opaque_review_id(response_id: str) -> str:
    return "cal-" + digest("review-unit", response_id)[:20]


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
    seen_opaque = set()
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
                review_unit_id = opaque_review_id(str(r["response_id"]))
                if review_unit_id in seen_opaque:
                    raise RuntimeError("opaque review-unit collision")
                seen_opaque.add(review_unit_id)
                selected_private.append({
                    "review_unit_id": review_unit_id,
                    "case_text": r["input_text"],
                    "response_text": r["response_text"],
                    # Internal mapping below is never copied into physician packets.
                    "source_id_internal": r["source_id"],
                    "case_id_internal": cid,
                    "primary_family_internal": r["primary_family"],
                    "presentation_internal": presentation,
                    "target_id_internal": target_id,
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

    print(f"Selected {len(selected_private)} physician-calibration units with opaque IDs")
    print(f"Private units + internal map: {private_path}")
    print(f"Public selection manifest: {args.public_manifest}")


if __name__ == "__main__":
    main()
