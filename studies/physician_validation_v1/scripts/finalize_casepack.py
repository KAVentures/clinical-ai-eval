#!/usr/bin/env python3
"""Finalize the physician-valid primary casepack deterministically.

A/B may review a prespecified subset of all drafted perturbations (the default
first wave is one perturbation per source). Only jointly reviewed perturbations can
become valid. Discordant reviewed perturbations require C adjudication. If the
reviewed valid reservoir cannot fill a locked source-stratum quota, this script
fails closed so a prespecified fallback review wave can be run before model calls.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

SEED = "clinical-ai-eval-physician-validation-v1"
QUOTAS = {
    ("good_faith", "typical"): 53,
    ("good_faith", "difficult"): 38,
    ("red_teaming", "difficult"): 59,
}
YES = {"yes", "y", "1", "true"}
VALID = {"valid", "accept", "accepted"}


def stable_hash(*parts: str) -> str:
    return hashlib.sha256((SEED + "|" + "|".join(parts)).encode()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


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


def row_valid(row: dict) -> bool:
    checks = [
        "original_coherent", "perturbed_coherent", "same_patient_task_timepoint",
        "evidence_load_bearing", "construct_achieved", "safe_response_definable",
    ]
    return all(str(row.get(k, "")).strip().lower() in YES for k in checks) and (
        str(row.get("decision", "")).strip().lower() in VALID
    )


def normalize_decision(row: dict) -> str:
    return "valid" if row_valid(row) else "not_valid"


def review_map(rows: list[dict], expected_reviewer: str) -> dict[str, dict]:
    out = {}
    for r in rows:
        rid = str(r.get("reviewer_id", "")).strip()
        if rid != expected_reviewer:
            raise ValueError(f"expected reviewer_id={expected_reviewer!r}, got {rid!r}")
        pid = str(r.get("perturbation_id", ""))
        if not pid:
            raise ValueError("review row missing perturbation_id")
        if pid in out:
            raise ValueError(f"duplicate review for {pid} by {expected_reviewer}")
        out[pid] = r
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--drafts", required=True, type=Path)
    p.add_argument("--candidate-queue", required=True, type=Path)
    p.add_argument("--review-a", required=True, type=Path)
    p.add_argument("--review-b", required=True, type=Path)
    p.add_argument("--adjudication-c", type=Path)
    p.add_argument("--vault", required=True, type=Path)
    p.add_argument("--public-out", required=True, type=Path)
    args = p.parse_args()

    drafts = {str(d["perturbation_id"]): d for d in read_jsonl(args.drafts) if d.get("applicable_draft")}
    candidates = read_csv(args.candidate_queue)
    a = review_map(read_csv(args.review_a), "A")
    b = review_map(read_csv(args.review_b), "B")
    c = review_map(read_csv(args.adjudication_c), "C") if args.adjudication_c else {}

    if set(a) != set(b):
        only_a = sorted(set(a) - set(b))
        only_b = sorted(set(b) - set(a))
        raise RuntimeError(f"A/B must review the same locked perturbation set; only_a={only_a[:3]}, only_b={only_b[:3]}")
    reviewed = set(a)
    unknown = sorted(reviewed - set(drafts))
    if unknown:
        raise RuntimeError(f"review packet references unknown perturbation {unknown[0]}")
    if not reviewed:
        raise RuntimeError("no perturbations were reviewed")

    validity = {}
    audit_rows = []
    for pid in sorted(reviewed):
        d = drafts[pid]
        da, db = normalize_decision(a[pid]), normalize_decision(b[pid])
        if da == db:
            final = da
            adjudicated = False
        else:
            if pid not in c:
                raise RuntimeError(f"A/B disagree on {pid}; reviewer C adjudication required")
            final = normalize_decision(c[pid])
            adjudicated = True
        validity[pid] = final == "valid"
        audit_rows.append({
            "perturbation_id": pid,
            "source_id": d["source_id"],
            "family": d["family"],
            "reviewer_a": da,
            "reviewer_b": db,
            "adjudicated": adjudicated,
            "reviewer_c": normalize_decision(c[pid]) if adjudicated else "",
            "final_construct_valid": final == "valid",
        })
    audit_by_pid = {str(r["perturbation_id"]): r for r in audit_rows}

    valid_by_source: dict[str, dict[str, dict]] = defaultdict(dict)
    for pid in reviewed:
        d = drafts[pid]
        if validity[pid]:
            valid_by_source[str(d["source_id"])][str(d["family"])] = d

    selected = []
    for stratum, quota in QUOTAS.items():
        kind, difficulty = stratum
        pool = [r for r in candidates if r.get("type") == kind and r.get("difficulty") == difficulty]
        pool.sort(key=lambda r: int(r["stratum_priority"]))
        accepted = [r for r in pool if str(r["source_id"]) in valid_by_source]
        if len(accepted) < quota:
            deficit = quota - len(accepted)
            raise RuntimeError(
                f"NEEDS_FALLBACK_REVIEW: stratum {stratum} has {len(accepted)} physician-valid reviewed sources; "
                f"needs {quota} (deficit {deficit}). Review prespecified alternate/unreviewed candidates before any target call."
            )
        selected.extend(accepted[:quota])

    if len(selected) != 150:
        raise AssertionError(f"expected 150 selected source cases, got {len(selected)}")

    assigned = {}
    forced_missing = 0
    forced_conflict = 0
    flexible = []
    for r in selected:
        sid = str(r["source_id"])
        fams = set(valid_by_source[sid])
        if fams == {"missing_information"}:
            assigned[sid] = "missing_information"
            forced_missing += 1
        elif fams == {"conflicting_evidence"}:
            assigned[sid] = "conflicting_evidence"
            forced_conflict += 1
        elif fams == {"missing_information", "conflicting_evidence"}:
            flexible.append(sid)
        else:
            raise AssertionError(f"selected source {sid} has unexpected valid families {fams}")

    flexible.sort(key=lambda sid: stable_hash("family-assignment", sid))
    need_missing = max(0, min(len(flexible), 75 - forced_missing))
    if forced_conflict > 75:
        need_missing = len(flexible)
    for i, sid in enumerate(flexible):
        assigned[sid] = "missing_information" if i < need_missing else "conflicting_evidence"

    final_missing = sum(v == "missing_information" for v in assigned.values())
    final_conflict = sum(v == "conflicting_evidence" for v in assigned.values())
    if min(final_missing, final_conflict) < 30:
        raise RuntimeError(
            f"primary family distribution {final_missing}/{final_conflict} cannot support the prespecified 30/30 physician calibration cohort"
        )

    private_out = args.vault / "casepack" / "primary_hbp_150.private.jsonl"
    private_out.parent.mkdir(parents=True, exist_ok=True)
    args.public_out.parent.mkdir(parents=True, exist_ok=True)

    public_fields = [
        "case_id", "source_dataset", "source_id", "type", "difficulty", "specialty",
        "source_content_sha256", "primary_family", "primary_perturbation_id",
        "original_case_sha256", "perturbed_case_sha256", "construct_review_a",
        "construct_review_b", "construct_adjudicated", "construct_review_c", "casepack_status",
    ]
    public_rows = []
    with private_out.open("w", encoding="utf-8") as pf:
        for r in sorted(selected, key=lambda x: (x["type"], x["difficulty"], int(x["stratum_priority"]))):
            sid = str(r["source_id"])
            family = assigned[sid]
            d = valid_by_source[sid][family]
            pid = str(d["perturbation_id"])
            case_id = "hbpv1-" + stable_hash("case-id", sid)[:12]
            ra, rb = normalize_decision(a[pid]), normalize_decision(b[pid])
            adjudicated = ra != rb
            rc = normalize_decision(c[pid]) if adjudicated else ""
            private_record = {
                "case_id": case_id,
                "source_dataset": r["source_dataset"],
                "source_id": sid,
                "source_metadata": {"type": r.get("type"), "difficulty": r.get("difficulty"), "specialty": r.get("specialty")},
                "primary_family": family,
                "primary_perturbation_id": pid,
                "original_case": d["original_case"],
                "perturbed_case": d["modified_case"],
                "changed_evidence": d.get("changed_evidence", ""),
                "draft_safe_response_strategy": d.get("safe_response_strategy", ""),
                "construct_validation": audit_by_pid[pid],
            }
            pf.write(json.dumps(private_record, ensure_ascii=False) + "\n")
            public_rows.append({
                "case_id": case_id,
                "source_dataset": r["source_dataset"],
                "source_id": sid,
                "type": r.get("type", ""),
                "difficulty": r.get("difficulty", ""),
                "specialty": r.get("specialty", ""),
                "source_content_sha256": r.get("source_content_sha256", ""),
                "primary_family": family,
                "primary_perturbation_id": pid,
                "original_case_sha256": sha256_text(d["original_case"]),
                "perturbed_case_sha256": sha256_text(d["modified_case"]),
                "construct_review_a": ra,
                "construct_review_b": rb,
                "construct_adjudicated": str(adjudicated).lower(),
                "construct_review_c": rc,
                "casepack_status": "physician_construct_valid",
            })

    with args.public_out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=public_fields)
        w.writeheader()
        w.writerows(public_rows)

    audit_path = args.vault / "casepack" / "construct_review_audit.private.json"
    audit_path.write_text(json.dumps(audit_rows, indent=2), encoding="utf-8")

    print(f"Final primary casepack: 150 cases ({final_missing} missing-information, {final_conflict} conflict)")
    print(f"Private casepack: {private_out}")
    print(f"Public manifest: {args.public_out}")


if __name__ == "__main__":
    main()
