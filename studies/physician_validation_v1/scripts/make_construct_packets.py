#!/usr/bin/env python3
"""Create private construct-validation packets for independent physicians A/B.

Default first wave contains at most ONE prespecified applicable perturbation per
source case, roughly balanced by family. `--all-variants` is reserved for fallback
or diagnostic review if the first wave cannot fill the locked source quotas.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

SEED = "clinical-ai-eval-physician-validation-v1|construct-first-choice"
FIELDS = [
    "source_dataset", "source_id", "perturbation_id", "perturbation_version", "family",
    "original_case", "perturbed_case", "changed_evidence_draft", "draft_safe_response_strategy",
    "reviewer_id", "original_coherent", "perturbed_coherent", "same_patient_task_timepoint",
    "evidence_load_bearing", "construct_achieved", "safe_response_definable",
    "decision", "notes", "reviewed_at_utc",
]


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def first_choice(source_id: str, variants: list[dict]) -> dict:
    if len(variants) == 1:
        return variants[0]
    by_family = {str(v["family"]): v for v in variants}
    preferred = "missing_information" if int(hashlib.sha256(f"{SEED}|{source_id}".encode()).hexdigest(), 16) % 2 == 0 else "conflicting_evidence"
    return by_family.get(preferred) or sorted(variants, key=lambda v: str(v["family"]))[0]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--drafts", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--reviewers", nargs="+", default=["A", "B"])
    p.add_argument("--all-variants", action="store_true",
                   help="Review every applicable draft. Default is one prespecified first-choice draft per source.")
    args = p.parse_args()

    applicable = [d for d in load_jsonl(args.drafts) if d.get("applicable_draft")]
    if args.all_variants:
        drafts = applicable
    else:
        grouped = defaultdict(list)
        for d in applicable:
            grouped[str(d["source_id"])].append(d)
        drafts = [first_choice(sid, variants) for sid, variants in grouped.items()]
        drafts.sort(key=lambda d: str(d["source_id"]))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    family_counts = defaultdict(int)
    for d in drafts:
        family_counts[str(d["family"])] += 1

    for reviewer in args.reviewers:
        path = args.out_dir / f"construct_review_{reviewer}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for d in drafts:
                w.writerow({
                    "source_dataset": d.get("source_dataset", ""),
                    "source_id": d.get("source_id", ""),
                    "perturbation_id": d.get("perturbation_id", ""),
                    "perturbation_version": d.get("perturbation_version", ""),
                    "family": d.get("family", ""),
                    "original_case": d.get("original_case", ""),
                    "perturbed_case": d.get("modified_case", ""),
                    "changed_evidence_draft": d.get("changed_evidence", ""),
                    "draft_safe_response_strategy": d.get("safe_response_strategy", ""),
                    "reviewer_id": reviewer,
                    "original_coherent": "",
                    "perturbed_coherent": "",
                    "same_patient_task_timepoint": "",
                    "evidence_load_bearing": "",
                    "construct_achieved": "",
                    "safe_response_definable": "",
                    "decision": "",
                    "notes": "",
                    "reviewed_at_utc": "",
                })
        print(path)
    print(f"Construct review wave: {len(drafts)} drafts; family counts={dict(family_counts)}")
    if not args.all_variants:
        print("If quotas cannot be filled after adjudication, generate a fallback wave from the unreviewed alternate variants; do not change families based on target-model results.")


if __name__ == "__main__":
    main()
