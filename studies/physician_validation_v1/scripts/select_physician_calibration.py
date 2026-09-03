#!/usr/bin/env python3
"""Select the shared physician calibration cohort before automated judge scoring.

Choose the SAME 60/150 source cases for all four target models, balanced 30/30
across the two primary perturbation families. Include both original and perturbed
responses for every target. Selection depends only on locked case IDs/family and
never on response content or judge labels. Reviewer-facing unit IDs are opaque.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

SEED = "clinical-ai-eval-physician-validation-v1|physician-calibration"
N_CASES_PER_FAMILY = 30
FAMILIES = ("missing_information", "conflicting_evidence")


def digest(*parts: str) -> str:
    return hashlib.sha256((SEED + "|" + "|".join(parts)).encode()).hexdigest()


def case_rank(case_id: str) -> str:
    return digest("shared-case-rank", case_id)


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
    case_family = {}
    for r in rows:
        key = (str(r["target_id"]), str(r["case_id"]), str(r["presentation"]))
        if key in by_target_case:
            raise RuntimeError(f"duplicate target response {key}")
        by_target_case[key] = r
        cid = str(r["case_id"])
        fam = str(r["primary_family"])
        if cid in case_family and case_family[cid] != fam:
            raise RuntimeError(f"case {cid} has inconsistent primary family")
        case_family[cid] = fam

    targets = sorted({str(r["target_id"]) for r in rows})
    if len(targets) != 4:
        raise RuntimeError(f"expected 4 target models, found {targets}")

    all_cases = sorted(case_family)
    if len(all_cases) != 150:
        raise RuntimeError(f"expected 150 locked source cases, found {len(all_cases)}")

    chosen_cases = []
    for family in FAMILIES:
        pool = sorted([cid for cid in all_cases if case_family[cid] == family], key=case_rank)
        if len(pool) < N_CASES_PER_FAMILY:
            raise RuntimeError(f"family {family} has only {len(pool)} cases; needs {N_CASES_PER_FAMILY}")
        chosen_cases.extend(pool[:N_CASES_PER_FAMILY])
    chosen_cases = sorted(chosen_cases, key=case_rank)
    if len(chosen_cases) != 60 or len(set(chosen_cases)) != 60:
        raise AssertionError("shared calibration cohort must contain exactly 60 unique cases")

    selected_private = []
    public = []
    seen_opaque = set()
    for cid in chosen_cases:
        for target_id in targets:
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
                    "shared_case_selection_rank_sha256": case_rank(cid),
                    "sampling_frame": "shared_60_cases_30_per_family_x_4_targets_x_2_presentations",
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
        "target_id_internal", "response_id", "shared_case_selection_rank_sha256", "sampling_frame",
    ]
    with args.public_manifest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(public, key=lambda r: r["review_unit_id"]))

    print("Selected shared 60-case physician calibration cohort: 30 missing-information + 30 conflict")
    print(f"Review units: {len(selected_private)} = 60 cases x 4 targets x 2 presentations")
    print(f"Private units + internal map: {private_path}")
    print(f"Public selection manifest: {args.public_manifest}")


if __name__ == "__main__":
    main()
