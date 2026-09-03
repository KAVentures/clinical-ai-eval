#!/usr/bin/env python3
"""Create private construct-validation packets for independent physicians A/B.

Packets contain clinical text and draft rationale but no other reviewer's labels.
They are private study artifacts and must not be committed publicly.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--drafts", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--reviewers", nargs="+", default=["A", "B"])
    args = p.parse_args()

    drafts = [d for d in load_jsonl(args.drafts) if d.get("applicable_draft")]
    args.out_dir.mkdir(parents=True, exist_ok=True)

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


if __name__ == "__main__":
    main()
