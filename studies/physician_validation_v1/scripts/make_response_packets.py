#!/usr/bin/env python3
"""Create reviewer-facing CSVs with no target/model/perturbation identifiers."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

FIELDS = [
    "review_unit_id", "case_text", "response_text", "reviewer_id",
    "unsafe_overconfident", "potentially_harmful_treatment",
    "recognizes_information_problem", "guideline_concordant_next_step",
    "excessive_abstention", "clinically_helpful", "cannot_determine_reason",
    "notes", "reviewed_at_utc",
]
SEED = "clinical-ai-eval-physician-validation-v1|response-review-order"


def order_key(reviewer: str, unit_id: str) -> str:
    return hashlib.sha256(f"{SEED}|{reviewer}|{unit_id}".encode()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--units", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--reviewers", nargs="+", default=["A", "B"])
    args = p.parse_args()

    units = read_jsonl(args.units)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for reviewer in args.reviewers:
        ordered = sorted(units, key=lambda r: order_key(reviewer, str(r["review_unit_id"])))
        path = args.out_dir / f"response_review_{reviewer}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for u in ordered:
                # Deliberately whitelist fields. Never pass through internal mapping.
                w.writerow({
                    "review_unit_id": u["review_unit_id"],
                    "case_text": u["case_text"],
                    "response_text": u["response_text"],
                    "reviewer_id": reviewer,
                    "unsafe_overconfident": "",
                    "potentially_harmful_treatment": "",
                    "recognizes_information_problem": "",
                    "guideline_concordant_next_step": "",
                    "excessive_abstention": "",
                    "clinically_helpful": "",
                    "cannot_determine_reason": "",
                    "notes": "",
                    "reviewed_at_utc": "",
                })
        print(path)


if __name__ == "__main__":
    main()
