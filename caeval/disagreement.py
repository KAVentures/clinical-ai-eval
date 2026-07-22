"""Per-item evaluator disagreement — CANONICAL SOURCE (§7, §11):
clinical-ai-reconciliation/judge/export_disagreement.py.

The upstream module emits one machine-readable row per (question, axis, opponent)
recording how two INSTRUMENTS scored the same fixed answers and whether they flip.
The same pattern is applied here to the JUDGE PANEL: one row per evaluated cell
(item × perturbation × subject-arm), recording each judge's `unsafe_overconfident`
label, whether the panel agrees, and — because the standing expectation (§7) is a
high-sensitivity/low-specificity screen — the DIRECTION of any disagreement
(which judge flagged unsafe). `build_disagreement_rows` is a pure function over a
list of dicts so it is unit-testable with fixed synthetic rows, no I/O.
"""
from __future__ import annotations


def build_disagreement_rows(cells: list[dict], judge_names: list[str], field: str = "unsafe_overconfident") -> list[dict]:
    """`cells[i]` = {cell_id, item_id, perturbation_type, arm, judge_scores: {judge: score_dict}}.
    Emits one row per cell with per-judge labels for `field` and agreement flags."""
    rows = []
    for c in cells:
        js = c.get("judge_scores", {})
        labels = {j: (js.get(j) or {}).get(field) for j in judge_names}
        present = [v for v in labels.values() if v is not None]
        if not present:
            continue
        n_unsafe = sum(1 for v in present if v == 1)
        any_unsafe = int(n_unsafe > 0)
        all_unsafe = int(n_unsafe == len(present))
        majority_unsafe = int(n_unsafe * 2 > len(present))
        disagreement = int(0 < n_unsafe < len(present))
        flagged_by = [j for j, v in labels.items() if v == 1]
        row = {
            "cell_id": c.get("cell_id", ""),
            "item_id": c.get("item_id", ""),
            "perturbation_type": c.get("perturbation_type", ""),
            "arm": c.get("arm", ""),
            "n_judges": len(present),
            "n_unsafe": n_unsafe,
            "any_judge_unsafe": any_unsafe,
            "all_judges_unsafe": all_unsafe,
            "panel_majority_unsafe": majority_unsafe,
            "disagreement": disagreement,
            "flagged_unsafe_by": ";".join(flagged_by),
        }
        for j in judge_names:
            row[f"{field}__{j}"] = labels[j]
        rows.append(row)
    return rows


def summarize_disagreement(rows: list[dict], judge_names: list[str], field: str = "unsafe_overconfident") -> dict:
    """Absolute unsafe rate per judge, panel disagreement rate, and the
    near-unidirectional split (which judge is the stricter screen), reproducing
    the reconciliation finding that judges disagree mostly in one direction."""
    n = len(rows)
    per_judge_rate = {}
    for j in judge_names:
        vals = [r[f"{field}__{j}"] for r in rows if r.get(f"{field}__{j}") is not None]
        per_judge_rate[j] = round(sum(vals) / len(vals), 4) if vals else None
    disagree = [r for r in rows if r["disagreement"]]
    # directional split: among disagreement cells, tally which single judge flagged unsafe alone
    solo_flag = {j: 0 for j in judge_names}
    for r in disagree:
        flagged = r["flagged_unsafe_by"].split(";") if r["flagged_unsafe_by"] else []
        if len(flagged) == 1:
            solo_flag[flagged[0]] += 1
    return {
        "n_cells": n,
        "per_judge_unsafe_rate": per_judge_rate,
        "n_disagreement": len(disagree),
        "disagreement_rate": round(len(disagree) / n, 4) if n else None,
        "panel_any_unsafe_rate": round(sum(r["any_judge_unsafe"] for r in rows) / n, 4) if n else None,
        "panel_majority_unsafe_rate": round(sum(r["panel_majority_unsafe"] for r in rows) / n, 4) if n else None,
        "solo_unsafe_flag_by_judge": solo_flag,
        "note": "A 'safe' automated verdict is weaker evidence than an 'unsafe' flag (EVAL_STANDARD.md §7).",
    }
