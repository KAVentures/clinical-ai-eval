"""Inferential quantities for the evidence package (§9 confidence intervals; §7
agreement). Wilson score CIs (the evidence-sufficiency study used Wilson via
statsmodels; reimplemented here in numpy so the harness carries no statsmodels
dependency), an exact McNemar test on the paired safe<->unsafe counts, and panel
agreement (Cohen κ for a pair, Krippendorff α for >2) via caeval.reliability.
"""
from __future__ import annotations

import math
from typing import Sequence

from . import reliability

_Z = 1.959963984540054  # 97.5th percentile of the standard normal (95% CI)


def wilson_ci(k: int, n: int, z: float = _Z) -> tuple[float, float, float]:
    """Return (point, low, high) Wilson score interval for k successes of n."""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def rate_ci(values: Sequence[int], z: float = _Z) -> dict:
    vals = [v for v in values if v is not None]
    k = sum(1 for v in vals if v)
    p, lo, hi = wilson_ci(k, len(vals), z)
    return {"n": len(vals), "k": k, "rate": None if len(vals) == 0 else round(p, 4),
            "ci95": [None, None] if len(vals) == 0 else [round(lo, 4), round(hi, 4)]}


def cluster_bootstrap_ci(pairs: Sequence, n_boot: int = 2000, seed: int = 62, alpha: float = 0.05) -> dict:
    """Case-clustered bootstrap CI for a proportion. `pairs` = [(cluster_id, 0/1), ...].
    Multiple variants share a base case (cluster) and the same original is reused
    across variants, so cell-level CIs (Wilson) understate uncertainty. We resample
    CLUSTERS (item_ids) with replacement and recompute the rate, giving a
    percentile CI that respects the correlation. This is the PRIMARY headline CI;
    Wilson is reported alongside, explicitly labelled 'unadjusted (ignores case
    clustering)'."""
    import numpy as np
    pairs = [(c, int(v)) for c, v in pairs if v is not None]
    if not pairs:
        return {"n_cells": 0, "n_clusters": 0, "rate": None, "ci95": [None, None], "method": "cluster_bootstrap"}
    by_cluster: dict = {}
    for cid, v in pairs:
        by_cluster.setdefault(cid, []).append(v)
    clusters = list(by_cluster)
    point = sum(v for _, v in pairs) / len(pairs)
    rng = np.random.default_rng(seed)
    boots = []
    idx = np.arange(len(clusters))
    for _ in range(n_boot):
        draw = rng.choice(idx, size=len(clusters), replace=True)
        num = den = 0
        for i in draw:
            vals = by_cluster[clusters[i]]
            num += sum(vals)
            den += len(vals)
        if den:
            boots.append(num / den)
    lo, hi = np.percentile(boots, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return {"n_cells": len(pairs), "n_clusters": len(clusters), "rate": round(point, 4),
            "ci95": [round(float(lo), 4), round(float(hi), 4)], "method": "cluster_bootstrap_by_case"}


def mcnemar_exact_p(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value from discordant counts b and c
    (binomial against 0.5). b = safe->unsafe, c = unsafe->safe."""
    n = b + c
    if n == 0:
        return 1.0
    m = min(b, c)
    # two-sided exact: 2 * sum_{i=0}^{m} C(n,i) 0.5^n, capped at 1
    tail = sum(math.comb(n, i) for i in range(m + 1)) * (0.5 ** n)
    return min(1.0, 2 * tail)


def panel_agreement(per_judge_labels: dict[str, list]) -> dict:
    """per_judge_labels: {judge_name: [label|None per aligned cell]}. Returns
    pairwise Cohen κ and, for >2 judges, Krippendorff α (nominal)."""
    names = list(per_judge_labels)
    out: dict = {"pairwise_cohen_kappa": {}, "krippendorff_alpha": None,
                 "mean_pairwise_percent_agreement": None}
    agrees = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = per_judge_labels[names[i]], per_judge_labels[names[j]]
            k = reliability.cohen_kappa(a, b)
            pa = reliability.percent_agreement(a, b)
            out["pairwise_cohen_kappa"][f"{names[i]}__vs__{names[j]}"] = None if k != k else round(k, 4)
            if pa == pa:
                agrees.append(pa)
    if agrees:
        out["mean_pairwise_percent_agreement"] = round(sum(agrees) / len(agrees), 4)
    if len(names) > 2:
        n_items = max((len(v) for v in per_judge_labels.values()), default=0)
        rows = [[per_judge_labels[nm][i] if i < len(per_judge_labels[nm]) else None for nm in names]
                for i in range(n_items)]
        a = reliability.krippendorff_alpha_nominal(rows)
        out["krippendorff_alpha"] = None if a != a else round(a, 4)
    return out
