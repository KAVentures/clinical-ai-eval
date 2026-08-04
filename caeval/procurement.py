"""Side-by-side comparison of several products for a buyer.

TWO HARD CONSTRAINTS, enforced in code and in tests, not merely documented:

  1. **No combined safety score.** Products are never ranked by one number. A
     single score requires weights across incommensurable harms — is one missed
     red flag worth ten unnecessary referrals? — and that weighting is a clinical
     and organisational judgement belonging to the buyer, not to this harness.
     Emitting one would launder that judgement into an apparently objective rank.
  2. **No buy/no-buy recommendation.** The output is per-hazard evidence against
     the buyer's own predeclared thresholds, plus what is not known. The decision
     is the buyer's.

What this DOES give a buyer: for each declared hazard, each product's rate with a
case-clustered interval, whether that meets the threshold THEY declared, whether
the difference between two products is distinguishable from noise, and — the part
most comparisons omit — an explicit statement of the comparison's own validity.

Comparability is a precondition, not a footnote. Products evaluated on different
case packs, family versions, or judge panels are not comparable, and the report
says INCOMPARABLE rather than printing two numbers next to each other.
"""
from __future__ import annotations

from .stats import cluster_bootstrap_ci, mcnemar_exact_p

# Fields that must never appear in this module's output.
FORBIDDEN_OUTPUT_KEYS = {"overall_score", "safety_score", "combined_score", "score",
                         "rank", "winner", "recommendation", "verdict", "best",
                         "recommended_product", "buy"}

COMPARABLE = "COMPARABLE"
INCOMPARABLE = "INCOMPARABLE"

# Run properties that must match for two products' numbers to mean the same thing.
COMPARABILITY_FIELDS = ["case_pack_hash", "family_id", "family_version",
                        "judge_panel_hash", "judge_prompt_hash", "selection_rules_hash",
                        "eval_standard_version"]


def check_comparability(entries: list) -> dict:
    """Do these runs measure the same thing? Fail closed: unknown => incomparable."""
    diffs, unknown = {}, []
    for f in COMPARABILITY_FIELDS:
        vals = {}
        for e in entries:
            v = e.get("environment", {}).get(f)
            if v in (None, ""):
                unknown.append(f"{e['product_id']}:{f}")
            vals.setdefault(v, []).append(e["product_id"])
        if len(vals) > 1:
            diffs[f] = {str(k): v for k, v in vals.items()}
    status = COMPARABLE if not diffs and not unknown else INCOMPARABLE
    reasons = []
    if diffs:
        reasons.append(f"products were evaluated under different conditions: {sorted(diffs)}")
    if unknown:
        reasons.append(f"environment not recorded for: {sorted(set(unknown))}")
    return {
        "status": status,
        "differing_fields": diffs,
        "unknown_fields": sorted(set(unknown)),
        "reasons": reasons,
        "note": ("All products were evaluated under identical conditions." if status == COMPARABLE
                 else "These products were NOT evaluated under identical conditions. Their rates "
                      "are reported separately and MUST NOT be read as a comparison: a difference "
                      "here confounds the product with the evaluation environment."),
    }


def _rate(cells: list, metric: str):
    vals = [c[metric] for c in cells if c.get(metric) is not None]
    if not vals:
        return None, 0
    return sum(vals) / len(vals), len(vals)


def hazard_evidence(entry: dict, hazard: dict) -> dict:
    """One product against one hazard's PREDECLARED threshold."""
    metric = hazard["acceptance_criterion"]["metric"]
    op = hazard["acceptance_criterion"]["operator"]
    threshold = hazard["acceptance_criterion"]["threshold"]
    cells = [c for c in entry["cells"] if hazard.get("tests") in (None, ["ALL"])
             or c.get("test_id") in hazard.get("tests", [])]
    rate, n = _rate(cells, metric)
    if rate is None:
        # No evidence is NOT a pass. This is the fail-open a buyer would never catch.
        return {"product_id": entry["product_id"], "hazard_id": hazard["hazard_id"],
                "metric": metric, "rate": None, "n": 0, "ci": None,
                "meets_threshold": None,
                "status": "NO_EVIDENCE",
                "note": "This hazard was not exercised by this run. Absence of a failure "
                        "rate is not a passing rate."}
    boot = cluster_bootstrap_ci([(c.get("case_id") or c.get("item_id") or i, c[metric])
                                 for i, c in enumerate(cells) if c.get(metric) is not None])
    lo, hi = boot["ci95"]
    meets = {"<=": rate <= threshold, "<": rate < threshold,
             "==": rate == threshold, ">=": rate >= threshold, ">": rate > threshold}[op]
    # An interval straddling the threshold means the run cannot settle it either way.
    settled = (hi <= threshold) if op in ("<=", "<", "==") else (lo >= threshold)
    return {
        "product_id": entry["product_id"], "hazard_id": hazard["hazard_id"],
        "description": hazard.get("description", ""),
        "severity": hazard.get("severity", "moderate"),
        "metric": metric, "operator": op, "threshold": threshold,
        "rate": round(rate, 4), "n": n, "ci": [round(lo, 4), round(hi, 4)],
        "meets_threshold": bool(meets),
        "status": ("MEETS" if meets and settled else
                   "MEETS_BUT_UNDERPOWERED" if meets else "DOES_NOT_MEET"),
        "note": ("" if settled else
                 "The confidence interval straddles the threshold: this run does not "
                 "have the precision to settle this hazard either way."),
    }


def pairwise_difference(a: dict, b: dict, metric: str) -> dict:
    """Is the difference between two products distinguishable from noise?

    Only meaningful when the two ran the SAME cells, so the pairing is by cell id.
    Unpaired cells are dropped and counted, never silently imputed.
    """
    a_cells = {c.get("cell_id", c.get("item_id")): c for c in a["cells"]}
    b_cells = {c.get("cell_id", c.get("item_id")): c for c in b["cells"]}
    shared = sorted(set(a_cells) & set(b_cells))
    dropped = len(set(a_cells) ^ set(b_cells))
    pairs = [(a_cells[k].get(metric), b_cells[k].get(metric)) for k in shared]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if not pairs:
        return {"metric": metric, "n_paired": 0, "p_value": None,
                "distinguishable": None, "dropped_unpaired_cells": dropped,
                "note": "No shared cells: these products cannot be compared cell-by-cell."}
    b01 = sum(1 for x, y in pairs if x == 1 and y == 0)
    b10 = sum(1 for x, y in pairs if x == 0 and y == 1)
    p = mcnemar_exact_p(b01, b10)
    return {
        "metric": metric, "n_paired": len(pairs),
        f"{a['product_id']}_only_failures": b01,
        f"{b['product_id']}_only_failures": b10,
        "p_value": p, "distinguishable": bool(p is not None and p < 0.05),
        "dropped_unpaired_cells": dropped,
        "note": ("" if p is not None and p < 0.05 else
                 "This run does not distinguish these products on this metric. That is "
                 "NOT evidence they are equivalent — it is absence of evidence."),
    }


def compare(entries: list, hazards: list) -> dict:
    """Build the buyer-facing comparison. No ranking, no recommendation."""
    comparability = check_comparability(entries)
    per_hazard = []
    for h in hazards:
        rows = [hazard_evidence(e, h) for e in entries]
        per_hazard.append({
            "hazard_id": h["hazard_id"],
            "description": h.get("description", ""),
            "severity": h.get("severity", "moderate"),
            "acceptance_criterion": h["acceptance_criterion"],
            "products": rows,
            # Deliberately a SET of products meeting the bar, not an ordering.
            "meets": sorted(r["product_id"] for r in rows if r["meets_threshold"]),
            "does_not_meet": sorted(r["product_id"] for r in rows
                                    if r["meets_threshold"] is False),
            "no_evidence": sorted(r["product_id"] for r in rows
                                  if r["meets_threshold"] is None),
        })
    return {
        "products": [e["product_id"] for e in entries],
        "comparability": comparability,
        "per_hazard": per_hazard,
        "maturity": sorted({e.get("family_maturity", "experimental") for e in entries}),
        "claim_authority": _claim_authority(entries, comparability),
        "what_this_does_not_tell_you": WHAT_THIS_DOES_NOT_TELL_YOU,
        # No combined score, no rank, no recommendation. Asserted by tests.
    }


def _claim_authority(entries, comparability) -> dict:
    maturities = {e.get("family_maturity", "experimental") for e in entries}
    experimental = "experimental" in maturities
    return {
        "decision_grade": False,
        "reason": ("Every test family involved is `experimental`: the measurement itself has "
                   "not been calibrated against clinician judgement, so these rates describe "
                   "the harness's behaviour as much as the products'."
                   if experimental else
                   "Family maturity permits reporting, but this comparison is still evidence "
                   "for a decision, not the decision.")
                  + ("" if comparability["status"] == COMPARABLE else
                     " In addition, the runs are not comparable to each other."),
    }


WHAT_THIS_DOES_NOT_TELL_YOU = [
    "Whether a product is safe in your setting. These are perturbation probes on a "
    "fixed case pack, not a clinical trial and not a deployment audit.",
    "How the products rank. There is deliberately no combined score: weighting a missed "
    "red flag against an unnecessary referral is your clinical and organisational "
    "judgement, and encoding it here would hide that judgement inside a number.",
    "Whether to buy anything. This report is evidence for your decision, not the decision.",
    "How a product behaves on cases unlike the pack, on other languages or populations, "
    "after a vendor update, or on any hazard marked NO_EVIDENCE.",
    "Whether products that look the same are the same. A non-significant difference is "
    "absence of evidence, not evidence of equivalence.",
]


def render_markdown(cmp_result: dict) -> str:
    L = ["# Product comparison", ""]
    L.append(f"**Products:** {', '.join(cmp_result['products'])}")
    c = cmp_result["comparability"]
    L.append(f"**Comparability:** {c['status']} — {c['note']}")
    ca = cmp_result["claim_authority"]
    L.append(f"**Decision grade:** {ca['decision_grade']} — {ca['reason']}")
    L.append("")
    L.append("> This report contains no combined score and no buy/no-buy recommendation, "
             "by design. See *What this does not tell you*.")
    L.append("")
    for h in cmp_result["per_hazard"]:
        crit = h["acceptance_criterion"]
        L.append(f"## {h['hazard_id']} ({h['severity']}) — {h['description']}")
        L.append(f"Your predeclared bar: `{crit['metric']} {crit['operator']} {crit['threshold']}`")
        L.append("")
        L.append("| product | rate | 95% CI | n | status |")
        L.append("|---|---|---|---|---|")
        for r in h["products"]:
            rate = "—" if r["rate"] is None else f"{r['rate']:.1%}"
            ci = "—" if not r.get("ci") else f"[{r['ci'][0]:.1%}, {r['ci'][1]:.1%}]"
            L.append(f"| {r['product_id']} | {rate} | {ci} | {r['n']} | {r['status']} |")
        notes = [f"- {r['product_id']}: {r['note']}" for r in h["products"] if r.get("note")]
        if notes:
            L += [""] + notes
        L.append("")
    L.append("## What this does not tell you")
    L += [f"- {x}" for x in cmp_result["what_this_does_not_tell_you"]]
    return "\n".join(L)
