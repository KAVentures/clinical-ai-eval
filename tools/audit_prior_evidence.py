#!/usr/bin/env python3
"""Offline provenance/consistency audit of TWO ALREADY PUBLISHED clinical-AI studies.

This does not conduct a new clinical validation or recover unreleased raw responses.
Pass local checkouts of the published study repositories. No APIs or network calls.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_labels(path: Path) -> dict[str, int]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    labels: dict[str, int] = {}
    for row in rows:
        key = (row.get("annotation_id") or "").strip()
        value = (row.get("human_label") or "").strip()
        if not key or key in labels or value not in ("0", "1"):
            raise ValueError(f"Invalid or duplicate binary label in {path}: {row}")
        labels[key] = int(value)
    return labels


def kappa(y: list[int], z: list[int]) -> float | None:
    if not y or len(y) != len(z):
        raise ValueError("Cannot compare empty or misaligned ratings")
    n = len(y)
    observed = sum(int(a == b) for a, b in zip(y, z)) / n
    py = sum(y) / n
    pz = sum(z) / n
    chance = py * pz + (1 - py) * (1 - pz)
    return (observed - chance) / (1 - chance) if chance < 1 else None


def agreement(left: dict[str, int], right: dict[str, int]) -> dict:
    if left.keys() != right.keys():
        raise ValueError("Raters have different IDs")
    keys = sorted(left)
    a = [left[k] for k in keys]
    b = [right[k] for k in keys]
    return {
        "n": len(keys),
        "raw_agreement": sum(x == y for x, y in zip(a, b)) / len(keys),
        "cohen_kappa": kappa(a, b),
    }


def audit_readiness(root: Path) -> dict:
    directory = root / "runs/human_eval"
    files = {
        "author_R1": directory / "human_labels.csv",
        "independent_O": directory / "labels_O.csv",
        "independent_G": directory / "labels_G.csv",
    }
    ratings = {name: read_labels(file) for name, file in files.items()}
    ids = set(ratings["author_R1"])
    if len(ids) != 50 or any(set(x) != ids for x in ratings.values()):
        raise ValueError("Expected three complete independent 50-item rater files")

    keys_path = directory / "key.json"
    mapping = json.loads(keys_path.read_text(encoding="utf-8"))
    if set(mapping) != ids:
        raise ValueError("The human item keys do not cover the same 50 IDs")

    votes_path = root / "runs/judge_panel/votes.jsonl"
    votes = []
    with votes_path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                votes.append(json.loads(line))
    if len(votes) != 200:
        raise ValueError(f"Expected 200 model-item vote rows, found {len(votes)}")
    vote_index = {}
    for record in votes:
        key = (record["subject_model"], record["item"], record["prompt_id"])
        if key in vote_index:
            raise ValueError("Duplicate model-item-prompt judge vote row")
        if set(record["votes"]) != {"openai", "anthropic", "xai", "google"}:
            raise ValueError("Expected four named judge votes per row")
        if not all(type(v) is bool for v in record["votes"].values()):
            raise ValueError("Judge votes must be booleans")
        vote_index[key] = record["votes"]

    for item_id, metadata in mapping.items():
        key = (metadata["subject_model"], metadata["item"], metadata["prompt_id"])
        if key not in vote_index:
            raise ValueError(f"No matching judge votes for {item_id}")

    pairwise = {}
    names = list(ratings)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            pairwise[f"{left}_vs_{right}"] = agreement(ratings[left], ratings[right])

    result = {
        "source_repo": "KAVentures/health-ai-readiness-robustness",
        "source_paper": "arXiv:2607.18828",
        "n_human_items": 50,
        "n_human_raters": 3,
        "n_judged_model_item_rows": len(votes),
        "n_provider_judge_votes": len(votes) * 4,
        "human_positive_rates": {name: sum(labels.values()) / len(labels) for name, labels in ratings.items()},
        "human_pairwise_agreement": pairwise,
        "source_sha256": {
            **{name: digest(file) for name, file in files.items()},
            "human_item_key": digest(keys_path),
            "judge_votes": digest(votes_path),
        },
        "interpretation": "Reanalysis of the previously published clinician labels and judge votes; NOT new independent clinical validation.",
    }

    stored_path = directory / "human_validity_multi.json"
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    if stored.get("n_common") != 50:
        raise ValueError("Published physician-report n_common disagrees")
    for name, stored_id in [("author_R1", "R1"), ("independent_O", "O"), ("independent_G", "G")]:
        if not math.isclose(result["human_positive_rates"][name], stored["per_annotator"][stored_id]["rate"], abs_tol=1e-10):
            raise ValueError(f"Published physician rate differs for {name}")
    result["source_sha256"]["published_human_summary"] = digest(stored_path)
    return result


def audit_sufficiency(root: Path) -> dict:
    path = root / "outputs/tables/panel_table3_primary_outcome.csv"
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = {r["prompt_condition"]: r for r in csv.DictReader(f)}
    if set(rows) != {"standard", "evidence_sufficiency"}:
        raise ValueError("Missing or unexpected main-panel condition")
    totals = {name: int(row["total_n"]) for name, row in rows.items()}
    positives = {name: int(row["unsafe_overconfident_n"]) for name, row in rows.items()}
    if totals != {"standard": 1200, "evidence_sufficiency": 1200}:
        raise ValueError("Previously published paired common panel requires 1200 cells per arm")
    rates = {name: positives[name] / totals[name] for name in rows}
    delta = rates["standard"] - rates["evidence_sufficiency"]
    for name, row in rows.items():
        if not math.isclose(float(row["rate"]), rates[name], abs_tol=1e-10):
            raise ValueError(f"Published summary rate inconsistent for {name}")
        if not math.isclose(float(row["absolute_risk_difference_standard_minus_wrapper"]), delta, abs_tol=1e-10):
            raise ValueError("Published risk difference inconsistent with marginal counts")
        if int(row["n_paired_cells"]) != 1200:
            raise ValueError("Published summary has inconsistent paired denominator")
    published = root / "outputs/tables/panel_analysis_summary.json"
    doc = json.loads(published.read_text(encoding="utf-8"))
    if not math.isclose(doc["primary_common_panel"]["risk_difference"], delta, abs_tol=1e-10):
        raise ValueError("Summary JSON disagrees with main results table")
    return {
        "source_repo": "KAVentures/clinical-evidence-sufficiency-llm",
        "source_paper": "arXiv:2607.18086",
        "previously_published_unsafe_rates": rates,
        "previously_published_absolute_difference": delta,
        "n_paired_model_item_cells": 1200,
        "source_sha256": {
            "published_main_table": digest(path),
            "published_analysis_summary": digest(published),
        },
        "interpretation": "Cross-checks ALREADY PUBLISHED aggregate tables only. Raw model-response records are not in this public repository; this cannot independently reproduce the experiment.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness-root", required=True, type=Path)
    parser.add_argument("--sufficiency-root", required=True, type=Path)
    parser.add_argument("--out", type=Path, help="Write deterministic ID-free JSON; default stdout")
    args = parser.parse_args()
    report = {
        "scope": "Retrospective software-evidence provenance audit, not a new evaluation study",
        "new_provider_calls": 0,
        "new_physician_ratings": 0,
        "readiness": audit_readiness(args.readiness_root),
        "sufficiency": audit_sufficiency(args.sufficiency_root),
        "prohibition": "Do not relabel earlier cases as independent validation of a new cohort or claim novel empirical findings from these reused results.",
    }
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote retrospective evidence report: {args.out}")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
