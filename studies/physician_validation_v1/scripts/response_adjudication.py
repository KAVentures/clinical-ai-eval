#!/usr/bin/env python3
"""Prepare discordant-response adjudication and finalize physician reference labels.

Modes:
  prepare  -> private C packet containing only A/B primary-endpoint discordances.
  finalize -> combine locked A/B reviews and C adjudication into a private
              physician reference table while preserving individual labels.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

BINARY = {"0", "1"}
PRIMARY = "unsafe_overconfident"
SECONDARY = [
    "potentially_harmful_treatment", "recognizes_information_problem",
    "guideline_concordant_next_step", "excessive_abstention", "clinically_helpful",
]


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def review_map(rows: list[dict], reviewer: str) -> dict[str, dict]:
    out = {}
    for r in rows:
        if str(r.get("reviewer_id", "")) != reviewer:
            raise ValueError(f"expected reviewer {reviewer}, got {r.get('reviewer_id')}")
        uid = str(r.get("review_unit_id", ""))
        if not uid or uid in out:
            raise ValueError(f"missing/duplicate review_unit_id {uid!r}")
        val = str(r.get(PRIMARY, "")).strip()
        if val not in BINARY:
            raise ValueError(f"{reviewer} primary label for {uid} must be 0 or 1")
        out[uid] = r
    return out


def prepare(args) -> None:
    units = {str(u["review_unit_id"]): u for u in read_jsonl(args.units)}
    a = review_map(read_csv(args.review_a), "A")
    b = review_map(read_csv(args.review_b), "B")
    if set(a) != set(units) or set(b) != set(units):
        raise RuntimeError("A/B response reviews must exactly cover the locked calibration units")

    fields = ["review_unit_id", "case_text", "response_text", "reviewer_id", PRIMARY, "rationale", "reviewed_at_utc"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for uid in sorted(units):
            if str(a[uid][PRIMARY]) == str(b[uid][PRIMARY]):
                continue
            u = units[uid]
            w.writerow({
                "review_unit_id": uid,
                "case_text": u["case_text"],
                "response_text": u["response_text"],
                "reviewer_id": "C",
                PRIMARY: "",
                "rationale": "",
                "reviewed_at_utc": "",
            })
            n += 1
    print(f"Prepared {n} primary-endpoint adjudication units: {args.out}")


def finalize(args) -> None:
    units = {str(u["review_unit_id"]): u for u in read_jsonl(args.units)}
    a = review_map(read_csv(args.review_a), "A")
    b = review_map(read_csv(args.review_b), "B")
    c_rows = read_csv(args.review_c) if args.review_c and args.review_c.exists() else []
    c = {}
    for r in c_rows:
        if str(r.get("reviewer_id", "")) != "C":
            raise ValueError("adjudication sheet contains non-C reviewer")
        uid = str(r.get("review_unit_id", ""))
        val = str(r.get(PRIMARY, "")).strip()
        if val not in BINARY:
            raise ValueError(f"C primary label for {uid} must be 0 or 1")
        c[uid] = r

    out_rows = []
    for uid in sorted(units):
        ua, ub = a[uid], b[uid]
        pa, pb = str(ua[PRIMARY]), str(ub[PRIMARY])
        if pa == pb:
            pref = pa
            adjudicated = "false"
            pc = ""
        else:
            if uid not in c:
                raise RuntimeError(f"primary disagreement {uid} has no C adjudication")
            pc = str(c[uid][PRIMARY])
            pref = pc
            adjudicated = "true"

        u = units[uid]
        row = {
            "review_unit_id": uid,
            "source_id": u["source_id_internal"],
            "case_id": u["case_id_internal"],
            "primary_family": u["primary_family_internal"],
            "presentation": u["presentation_internal"],
            "target_id": u["target_id_internal"],
            "response_id": u["response_id_internal"],
            "physician_a_unsafe_overconfident": pa,
            "physician_b_unsafe_overconfident": pb,
            "physician_c_unsafe_overconfident": pc,
            "unsafe_overconfident_reference": pref,
            "primary_adjudicated": adjudicated,
        }
        for endpoint in SECONDARY:
            va = str(ua.get(endpoint, "")).strip()
            vb = str(ub.get(endpoint, "")).strip()
            # Secondary endpoints preserve consensus only. Discordance remains NA
            # unless separately adjudicated in a prespecified amendment.
            row[f"physician_a_{endpoint}"] = va
            row[f"physician_b_{endpoint}"] = vb
            row[f"{endpoint}_reference"] = va if va in BINARY and va == vb else "NA"
        out_rows.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = list(out_rows[0].keys()) if out_rows else []
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Finalized {len(out_rows)} physician reference rows: {args.out}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("prepare", "finalize"):
        s = sub.add_parser(name)
        s.add_argument("--units", required=True, type=Path)
        s.add_argument("--review-a", required=True, type=Path)
        s.add_argument("--review-b", required=True, type=Path)
        s.add_argument("--out", required=True, type=Path)
        if name == "finalize":
            s.add_argument("--review-c", type=Path)
    args = p.parse_args()
    prepare(args) if args.command == "prepare" else finalize(args)


if __name__ == "__main__":
    main()
