#!/usr/bin/env python3
"""Analyze physician-rated target-model robustness on the shared 60-case cohort."""
from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


def ci_mean(values: np.ndarray, n_boot: int, seed: int) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(values)
    draws = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        draws[i] = rng.choice(values, size=n, replace=True).mean()
    return tuple(float(x) for x in np.percentile(draws, [2.5, 97.5]))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--physician-reference", required=True, type=Path)
    p.add_argument("--out-models", required=True, type=Path)
    p.add_argument("--out-contrasts", required=True, type=Path)
    p.add_argument("--bootstrap", type=int, default=10000)
    p.add_argument("--seed", type=int, default=20260903)
    args = p.parse_args()

    df = pd.read_csv(args.physician_reference, dtype=str)
    df = df[df["unsafe_overconfident_reference"].isin(["0", "1"])].copy()
    df["y"] = df["unsafe_overconfident_reference"].astype(int)

    pivot = df.pivot_table(
        index=["source_id", "case_id", "primary_family", "target_id"],
        columns="presentation", values="y", aggfunc="first",
    ).reset_index()
    if not {"original", "perturbed"}.issubset(pivot.columns):
        raise RuntimeError("physician reference lacks paired original/perturbed labels")
    pivot = pivot.dropna(subset=["original", "perturbed"]).copy()
    pivot["original"] = pivot["original"].astype(int)
    pivot["perturbed"] = pivot["perturbed"].astype(int)
    pivot["delta"] = pivot["perturbed"] - pivot["original"]

    model_rows = []
    for i, (target, g) in enumerate(pivot.groupby("target_id", sort=True)):
        vals = g["delta"].to_numpy(dtype=float)
        lo, hi = ci_mean(vals, args.bootstrap, args.seed + i * 1009)
        o = g["original"].to_numpy()
        q = g["perturbed"].to_numpy()
        model_rows.append({
            "target_id": target,
            "n_paired_cases": len(g),
            "unsafe_original_rate": float(o.mean()),
            "unsafe_perturbed_rate": float(q.mean()),
            "paired_risk_difference": float(vals.mean()),
            "paired_risk_difference_ci_low": lo,
            "paired_risk_difference_ci_high": hi,
            "transition_0_to_0": int(((o == 0) & (q == 0)).sum()),
            "transition_0_to_1": int(((o == 0) & (q == 1)).sum()),
            "transition_1_to_0": int(((o == 1) & (q == 0)).sum()),
            "transition_1_to_1": int(((o == 1) & (q == 1)).sum()),
        })

    # Directly paired target-vs-target contrast because the same 60 cases were
    # physician-reviewed for all four targets.
    wide = pivot.pivot(index="case_id", columns="target_id", values="delta")
    contrast_rows = []
    for i, (a, b) in enumerate(combinations(sorted(wide.columns), 2)):
        pair = wide[[a, b]].dropna()
        diff = (pair[a] - pair[b]).to_numpy(dtype=float)
        lo, hi = ci_mean(diff, args.bootstrap, args.seed + 50000 + i * 1013)
        contrast_rows.append({
            "target_a": a,
            "target_b": b,
            "n_shared_cases": len(pair),
            "difference_in_perturbation_risk_difference_a_minus_b": float(diff.mean()),
            "ci_low": lo,
            "ci_high": hi,
        })

    args.out_models.parent.mkdir(parents=True, exist_ok=True)
    args.out_contrasts.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(model_rows).to_csv(args.out_models, index=False)
    pd.DataFrame(contrast_rows).to_csv(args.out_contrasts, index=False)
    print(pd.DataFrame(model_rows).to_string(index=False))
    print("\nPairwise contrasts")
    print(pd.DataFrame(contrast_rows).to_string(index=False))


if __name__ == "__main__":
    main()
