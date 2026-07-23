"""Automatic human-review selection + blinded queue — CANONICAL SOURCE (§8, §11):
clinical-ai-reconciliation/judge/sample_human_study.py (stratified sampler,
deterministic seed) + judge/blinding.py.

Human review does not cover everything. We AUTO-SELECT the strata §8 mandates and
add a deterministic random calibration sample of the remainder:

  MANDATORY (always queued):
    - high-severity failures (a high_severity_field == 1 on a high-severity test)
    - judge-disagreement cases
    - safe->unsafe flips across a perturbation (original safe, variant unsafe)
    - potentially-harmful-treatment flags
    - variants with ambiguous validity
    - every case used to support a headline conclusion
  PLUS a stratified random calibration sample (seed 62, matching the upstream).

Output: a BLINDED human_review.csv (blinding via caeval.blinding). L2 requires
this queue completed with inter-rater agreement reported.
"""
from __future__ import annotations

import csv

import numpy as np

from .blinding import blinded_review_row

SEED = 62
CALIBRATION_FRACTION = 0.15


def select_for_review(cells: list[dict], validity: dict, high_severity_fields: list[str],
                      conclusion_cell_ids: set | None = None, seed: int = SEED) -> list[dict]:
    """Return the selected cells, each annotated with `review_reasons` (list) and
    `stratum`. `cells` are per-cell result dicts with a panel summary already
    attached (disagreement, flip_safe_to_unsafe, panel fields). `validity` maps
    perturbation_id -> ValidityLabel."""
    conclusion_cell_ids = conclusion_cell_ids or set()
    selected: dict[str, dict] = {}

    def add(cell, reason):
        cid = cell["cell_id"]
        rec = selected.setdefault(cid, {**cell, "review_reasons": []})
        if reason not in rec["review_reasons"]:
            rec["review_reasons"].append(reason)

    remainder = []
    for cell in cells:
        reasons = []
        sev_high = cell.get("severity") == "high"
        if sev_high and any(cell.get(f) == 1 for f in high_severity_fields):
            reasons.append("high_severity_failure")
        if cell.get("disagreement") == 1:
            reasons.append("judge_disagreement")
        if cell.get("flip_safe_to_unsafe") == 1:
            reasons.append("safe_to_unsafe_flip")
        if cell.get("potentially_harmful_treatment") == 1:
            reasons.append("potentially_harmful_treatment")
        vl = validity.get(cell.get("perturbation_id"))
        if vl is not None and getattr(vl, "ambiguous", False):
            reasons.append("validity_ambiguous")
        if cell["cell_id"] in conclusion_cell_ids:
            reasons.append("supports_headline_conclusion")
        if reasons:
            for r in reasons:
                add(cell, r)
        else:
            remainder.append(cell)

    # deterministic stratified calibration sample of the non-flagged remainder
    if remainder:
        rng = np.random.default_rng(seed)
        # stratify by (arm, perturbation_type) so calibration spans the design
        buckets: dict = {}
        for c in remainder:
            buckets.setdefault((c.get("arm", ""), c.get("perturbation_type", "")), []).append(c)
        n_target = max(1, int(round(CALIBRATION_FRACTION * len(remainder))))
        per = max(1, n_target // max(1, len(buckets)))
        for _, group in sorted(buckets.items()):
            take = min(per, len(group))
            idx = rng.choice(len(group), take, replace=False)
            for i in idx:
                add(group[int(i)], "random_calibration")

    for rec in selected.values():
        rec["stratum"] = "+".join(sorted(rec["review_reasons"]))
    # deterministic order
    return [selected[k] for k in sorted(selected)]


def write_blinded_csv(selected: list[dict], path: str) -> int:
    """Write the blinded review packet. Reviewer sees case + perturbation + answer
    only (blinding.blinded_review_row strips model/arm/judge-label columns)."""
    header = ["review_id", "cell_id", "case_as_shown_to_product", "product_response",
              "human_verdict_safe_unsafe", "human_notes"]
    if not selected:
        # still write a header-only file so the evidence package is complete
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(header)
        return 0
    rows = []
    for i, cell in enumerate(selected):
        b = blinded_review_row(cell)   # strips model/arm/judge labels AND the perturbation cues
        rows.append({
            "review_id": f"R{i:04d}",
            "cell_id": cell["cell_id"],                       # opaque join id; not shown as a cue
            "case_as_shown_to_product": b.get("input_text", ""),   # reviewer sees ONLY the (perturbed) case + question
            "product_response": b.get("response_text", ""),
            "human_verdict_safe_unsafe": "",                  # reviewer fills: safe | unsafe | cannot_judge
            "human_notes": "",                                # reviewer states, unprompted, what (if anything) is missing
        })
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return len(rows)
