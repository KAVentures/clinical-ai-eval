"""Version-to-version regression — did this product release get safer or worse?

The loop a health-AI team can run weekly, before any of this is procurement-grade:

    run v1.3  ->  inspect failures  ->  repair  ->  run v1.4  ->  compare

THE PRECONDITION THAT MAKES A COMPARISON MEAN ANYTHING
------------------------------------------------------
A delta is attributable to the PRODUCT only if nothing else moved. If the case
pack, family definition, judge prompt, panel or selection rules changed between the
two runs, the difference confounds product change with environment change — and
"we fixed it" is exactly the conclusion someone will draw from a number that
actually reflects a swapped judge.

So the comparison is gated on the assessment manifests (v0.12): differing
environments yield `ENVIRONMENT_CHANGED`, never a silent product claim. Comparing
anyway is possible but must be explicitly requested and is labelled unattributable.

WHAT IT REPORTS (never one number)
----------------------------------
    newly_failing     safe in baseline, unsafe in candidate   <- the regression
    repaired          unsafe in baseline, safe in candidate
    still_failing     unsafe in both
    still_passing     safe in both
plus helpfulness and over-abstention deltas, per-hazard movement, and the
response-level diffs for the cells that changed — because "what exactly changed"
is the question a developer actually needs answered.

Safety and helpfulness stay separate here too: a release that cut unsafe answers by
refusing everything shows up as repaired-with-helpfulness-collapse, not as a win.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import stats
from .util import utc_now_iso

COMPARABLE, ENVIRONMENT_CHANGED, INCOMPARABLE = "COMPARABLE", "ENVIRONMENT_CHANGED", "INCOMPARABLE"

# Manifest fields that MUST match for a delta to be attributable to the product.
ENVIRONMENT_KEYS = ("definitions", "family_id")
CASE_KEYS = ("facing_input_hash", "hidden_manifest_hash")


class RegressionError(RuntimeError):
    """The two runs cannot be compared."""


def _load(ws: Path) -> dict:
    ws = Path(ws)
    rows = {}
    rp = ws / "results.jsonl"
    if not rp.exists():
        raise RegressionError(f"no results.jsonl in {ws}")
    with open(rp) as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                rows[r["cell_id"]] = r
    man_p = ws / "assessment_manifest.json"
    manifest = json.loads(man_p.read_text()) if man_p.exists() else {}
    meta_p = ws / "run_meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    return {"path": str(ws), "cells": rows, "manifest": manifest, "meta": meta}


def compare_environments(base: dict, cand: dict) -> dict:
    """Is a product-attributable comparison legitimate?"""
    diffs = []
    bm, cm = base["manifest"], cand["manifest"]
    if not bm or not cm:
        return {"status": INCOMPARABLE,
                "differences": ["one or both runs lack assessment_manifest.json; the "
                                "environment cannot be established, so no delta is attributable"],
                "attributable": False}

    if bm.get("family_id") != cm.get("family_id"):
        diffs.append(f"family_id: {bm.get('family_id')} -> {cm.get('family_id')}")

    for key, bh in (bm.get("definitions") or {}).items():
        ch = (cm.get("definitions") or {}).get(key)
        if ch != bh:
            diffs.append(f"definition {key} changed ({str(bh)[:8]} -> {str(ch)[:8]})")

    bc, cc = bm.get("case_content", {}), cm.get("case_content", {})
    for k in CASE_KEYS:
        if bc.get(k) != cc.get(k):
            diffs.append(f"case content {k} changed — the products were not asked the same thing")

    bp = [j.get("name") for j in (base["meta"].get("panel", {}).get("judges") or [])]
    cp = [j.get("name") for j in (cand["meta"].get("panel", {}).get("judges") or [])]
    if sorted(bp) != sorted(cp):
        diffs.append(f"judge panel changed: {bp} -> {cp}")

    return {"status": COMPARABLE if not diffs else ENVIRONMENT_CHANGED,
            "differences": diffs, "attributable": not diffs}


def _unsafe(cell) -> int | None:
    """None when the cell had no complete evaluation — never silently 'safe'."""
    if cell is None or not cell.get("evaluation_complete", True):
        return None
    return cell.get("panel_any_unsafe")


def _mean(cells, field) -> float | None:
    vals = []
    for c in cells:
        for s in (c.get("judge_scores") or {}).values():
            if field in s:
                vals.append(s[field])
    return round(sum(vals) / len(vals), 4) if vals else None


def compare_runs(baseline_ws, candidate_ws, allow_environment_change: bool = False) -> dict:
    """Compare two evidence packages cell by cell."""
    base, cand = _load(Path(baseline_ws)), _load(Path(candidate_ws))
    env = compare_environments(base, cand)
    if env["status"] != COMPARABLE and not allow_environment_change:
        return {"status": env["status"], "environment": env,
                "note": ("Refusing to attribute a delta to the product: the test environment "
                         "changed. Re-run the baseline under the current environment, or pass "
                         "allow_environment_change=True to see an explicitly UNATTRIBUTABLE diff."),
                "generated_at": utc_now_iso()}

    shared = sorted(set(base["cells"]) & set(cand["cells"]))
    only_base = sorted(set(base["cells"]) - set(cand["cells"]))
    only_cand = sorted(set(cand["cells"]) - set(base["cells"]))

    buckets = {"newly_failing": [], "repaired": [], "still_failing": [],
               "still_passing": [], "indeterminate": []}
    for cid in shared:
        b, c = _unsafe(base["cells"][cid]), _unsafe(cand["cells"][cid])
        if b is None or c is None:
            buckets["indeterminate"].append(cid)
        elif b == 0 and c == 1:
            buckets["newly_failing"].append(cid)
        elif b == 1 and c == 0:
            buckets["repaired"].append(cid)
        elif b == 1 and c == 1:
            buckets["still_failing"].append(cid)
        else:
            buckets["still_passing"].append(cid)

    # response-level diffs for cells that MOVED — what a developer actually needs
    diffs = []
    for cid in buckets["newly_failing"] + buckets["repaired"]:
        diffs.append({
            "cell_id": cid,
            "direction": "REGRESSED" if cid in buckets["newly_failing"] else "repaired",
            "perturbation": base["cells"][cid].get("perturbation_type"),
            "severity": base["cells"][cid].get("severity"),
            "case_as_shown": (base["cells"][cid].get("input_text") or "")[:400],
            "baseline_response": (base["cells"][cid].get("response_text") or "")[:400],
            "candidate_response": (cand["cells"][cid].get("response_text") or "")[:400],
        })

    bcells = [base["cells"][c] for c in shared]
    ccells = [cand["cells"][c] for c in shared]

    # SAFETY and HELPFULNESS reported separately (§0) — a release that "fixed"
    # safety by refusing everything must not read as an improvement.
    dims = {}
    for field in ("unsafe_overconfident", "potentially_harmful_treatment",
                  "guideline_concordant_next_step", "identifies_removed_evidence",
                  "excessive_abstention", "correct_abstention"):
        b, c = _mean(bcells, field), _mean(ccells, field)
        dims[field] = {"baseline": b, "candidate": c,
                       "delta_pp": (None if b is None or c is None else round((c - b) * 100, 1))}

    # per-hazard movement, keyed by the perturbation that produced the cell
    by_probe = {}
    for cid in shared:
        probe = base["cells"][cid].get("perturbation_type", "?")
        d = by_probe.setdefault(probe, {"newly_failing": 0, "repaired": 0,
                                        "still_failing": 0, "still_passing": 0})
        for k, ids in buckets.items():
            if k in d and cid in ids:
                d[k] += 1

    n_shared = len(shared)
    pairs_b = [(base["cells"][c].get("item_id"), _unsafe(base["cells"][c])) for c in shared]
    pairs_c = [(cand["cells"][c].get("item_id"), _unsafe(cand["cells"][c])) for c in shared]
    mcnemar = stats.mcnemar_exact_p(len(buckets["newly_failing"]), len(buckets["repaired"]))

    return {
        "status": COMPARABLE if env["attributable"] else ENVIRONMENT_CHANGED,
        "attributable_to_product": env["attributable"],
        "generated_at": utc_now_iso(),
        "baseline": {"path": base["path"],
                     "version": base["meta"].get("subject_spec", {}).get("version"),
                     "name": base["meta"].get("subject_spec", {}).get("name")},
        "candidate": {"path": cand["path"],
                      "version": cand["meta"].get("subject_spec", {}).get("version"),
                      "name": cand["meta"].get("subject_spec", {}).get("name")},
        "environment": env,
        "n_shared_cells": n_shared,
        "cells_only_in_baseline": only_base,
        "cells_only_in_candidate": only_cand,
        "counts": {k: len(v) for k, v in buckets.items()},
        "buckets": buckets,
        "dimensions": dims,
        "by_probe": by_probe,
        "response_diffs": diffs,
        "paired_test": {"newly_failing": len(buckets["newly_failing"]),
                        "repaired": len(buckets["repaired"]),
                        "mcnemar_exact_p_unclustered": round(mcnemar, 6)},
        "safety_ci_baseline": stats.cluster_bootstrap_ci(pairs_b),
        "safety_ci_candidate": stats.cluster_bootstrap_ci(pairs_c),
    }


def render_markdown(cmp: dict) -> str:
    if cmp.get("status") in (ENVIRONMENT_CHANGED, INCOMPARABLE) and "counts" not in cmp:
        L = [f"# Version comparison — {cmp['status']}", "", cmp.get("note", ""), "",
             "## Environment differences"]
        L += [f"- {d}" for d in cmp["environment"]["differences"]]
        return "\n".join(L) + "\n"

    c = cmp["counts"]
    b, cand = cmp["baseline"], cmp["candidate"]
    L = [f"# Version comparison — {b.get('name')} {b.get('version')} → {cand.get('version')}", ""]
    if not cmp["attributable_to_product"]:
        L += ["> ⚠️ **UNATTRIBUTABLE.** The test environment changed between these runs, so a "
              "difference cannot be assigned to the product:", ""]
        L += [f"> - {d}" for d in cmp["environment"]["differences"]] + [""]
    else:
        L += ["_Environment identical across both runs (same cases, family, prompt, panel, "
              "rules), so differences are attributable to the product._", ""]

    L += [f"Compared **{cmp['n_shared_cells']}** shared cells.", "",
          "| movement | n |", "|---|---|",
          f"| 🔴 **newly failing** (regression) | **{c['newly_failing']}** |",
          f"| 🟢 repaired | {c['repaired']} |",
          f"| ⚪ still failing | {c['still_failing']} |",
          f"| ⚪ still passing | {c['still_passing']} |",
          f"| ❓ indeterminate (incomplete eval) | {c['indeterminate']} |", ""]

    pt = cmp["paired_test"]
    L += [f"McNemar exact p (unclustered, exploratory): **{pt['mcnemar_exact_p_unclustered']}** "
          f"({pt['newly_failing']} newly failing vs {pt['repaired']} repaired)", ""]

    L += ["## Dimensions — safety and helpfulness never collapsed", "",
          "| dimension | baseline | candidate | Δ pp |", "|---|---|---|---|"]
    for k, v in cmp["dimensions"].items():
        fmt = lambda x: "n/a" if x is None else f"{x:.0%}"
        d = "n/a" if v["delta_pp"] is None else f"{v['delta_pp']:+}"
        L.append(f"| `{k}` | {fmt(v['baseline'])} | {fmt(v['candidate'])} | {d} |")
    L += ["", "_Read `excessive_abstention` beside the safety rows: a release that cut unsafe "
          "answers by refusing more has not improved, it has traded one harm for another._", ""]

    L += ["## Movement by probe", "", "| probe | newly failing | repaired | still failing |",
          "|---|---|---|---|"]
    for probe, d in sorted(cmp["by_probe"].items()):
        L.append(f"| `{probe}` | {d['newly_failing']} | {d['repaired']} | {d['still_failing']} |")
    L.append("")

    regressions = [d for d in cmp["response_diffs"] if d["direction"] == "REGRESSED"]
    if regressions:
        L += ["## Regressions — what changed, response by response", ""]
        for d in regressions[:10]:
            L += [f"### `{d['cell_id']}`  ({d['perturbation']}, severity {d['severity']})",
                  "**Case as shown**", "```", d["case_as_shown"], "```",
                  "**Baseline (was safe)**", "```", d["baseline_response"], "```",
                  "**Candidate (now unsafe)**", "```", d["candidate_response"], "```", ""]
        if len(regressions) > 10:
            L.append(f"_…and {len(regressions) - 10} more._")
    return "\n".join(L) + "\n"
