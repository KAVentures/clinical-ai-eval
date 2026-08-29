"""Authoring utilities for the Branch-Intersection Safety study.

Separates physician review from YAML editing and expands the preallocated 120-case
confirmatory design matrix into a casepack skeleton. This keeps the audit trail
explicit and prevents a shared YAML anchor or accidental bulk edit from silently
validating multiple cases.
"""
from __future__ import annotations

import argparse
import csv
from copy import deepcopy
from pathlib import Path

import yaml

REVIEW_FIELDS = (
    "branch_A_coherent",
    "branch_B_coherent",
    "unresolved_preserves_both",
    "same_timepoint",
    "same_action_semantics",
    "branch_coverage_adequate",
)


def _bool(v: str) -> bool:
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y"}:
        return True
    if s in {"0", "false", "no", "n", ""}:
        return False
    raise ValueError(f"not a boolean value: {v!r}")


def make_review_sheet(casepack_path: Path, out: Path) -> int:
    data = yaml.safe_load(casepack_path.read_text())
    fields = ["case_id", "status", "validator", *REVIEW_FIELDS, "rationale"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for case in data["cases"]:
            w.writerow({
                "case_id": case["case_id"],
                "status": "candidate",
                "validator": "",
                **{x: "false" for x in REVIEW_FIELDS},
                "rationale": "",
            })
    return len(data["cases"])


def apply_review(casepack_path: Path, review_csv: Path, out: Path) -> dict:
    data = yaml.safe_load(casepack_path.read_text())
    rows = {}
    with review_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            cid = row["case_id"].strip()
            if cid in rows:
                raise ValueError(f"duplicate review row {cid}")
            rows[cid] = row
    case_ids = {c["case_id"] for c in data["cases"]}
    unknown = set(rows) - case_ids
    if unknown:
        raise ValueError(f"review contains unknown case ids: {sorted(unknown)}")
    result = deepcopy(data)
    counts = {"valid": 0, "candidate": 0, "exclude": 0}
    for case in result["cases"]:
        row = rows.get(case["case_id"])
        if row is None:
            review = {"status": "candidate", "validator": "", **{x: False for x in REVIEW_FIELDS}, "rationale": ""}
        else:
            status = row.get("status", "candidate").strip().lower()
            if status not in counts:
                raise ValueError(f"{case['case_id']}: status must be candidate, valid, or exclude")
            review = {
                "status": status,
                "validator": row.get("validator", "").strip(),
                **{x: _bool(row.get(x, "false")) for x in REVIEW_FIELDS},
                "rationale": row.get("rationale", "").strip(),
            }
            if status == "valid":
                if not review["validator"] or not review["rationale"] or not all(review[x] for x in REVIEW_FIELDS):
                    raise ValueError(f"{case['case_id']}: valid requires validator, rationale, and all six review checks=true")
        case["physician_validation"] = review
        counts[review["status"]] += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(result, sort_keys=False, allow_unicode=True))
    return counts


def scaffold_full(design_path: Path, out: Path) -> int:
    design = yaml.safe_load(design_path.read_text())
    cases = []
    idx = 1
    for domain, constructs in design["domains"].items():
        for construct in constructs:
            cases.append({
                "case_id": f"FULL-{idx:03d}",
                "stage": "confirmatory",
                "domain": domain,
                "construct": construct,
                "archetype_cluster": "",
                "candidate_actions": [],
                "presentations": {
                    "resolved_A": "",
                    "resolved_B": "",
                    "unresolved_AB": "",
                    "unresolved_BA": "",
                },
                "physician_validation": {
                    "status": "candidate",
                    "validator": "",
                    **{x: False for x in REVIEW_FIELDS},
                    "rationale": "",
                },
            })
            idx += 1
    if len(cases) != int(design["target_n"]):
        raise ValueError(f"design expands to {len(cases)} cases, expected {design['target_n']}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump({
        "casepack_name": "bisv_confirmatory_120",
        "stage": "confirmatory",
        "status": "authoring_required",
        "cases": cases,
    }, sort_keys=False, allow_unicode=True))
    return len(cases)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m caeval.branch_intersection_authoring")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("review-sheet")
    a.add_argument("--cases", required=True)
    a.add_argument("--out", required=True)
    b = sub.add_parser("apply-review")
    b.add_argument("--cases", required=True)
    b.add_argument("--review", required=True)
    b.add_argument("--out", required=True)
    c = sub.add_parser("scaffold-full")
    c.add_argument("--design", required=True)
    c.add_argument("--out", required=True)
    args = p.parse_args(argv)
    if args.cmd == "review-sheet":
        n = make_review_sheet(Path(args.cases), Path(args.out))
        print(f"wrote {n} review rows to {args.out}")
    elif args.cmd == "apply-review":
        print(apply_review(Path(args.cases), Path(args.review), Path(args.out)))
    else:
        n = scaffold_full(Path(args.design), Path(args.out))
        print(f"wrote {n} confirmatory case slots to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
