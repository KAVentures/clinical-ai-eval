#!/usr/bin/env python3
"""Primary automated-judge validation analysis against physician reference.

Outputs individual-judge, panel-ANY, and panel-MAJORITY operating characteristics
with source-case cluster-bootstrap confidence intervals.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

METRICS = ["sensitivity", "specificity", "balanced_accuracy", "ppv", "npv", "agreement", "kappa"]


def safe_div(a: float, b: float) -> float:
    return float(a / b) if b else float("nan")


def metric_dict(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    y = y.astype(int)
    pred = pred.astype(int)
    tp = int(((y == 1) & (pred == 1)).sum())
    tn = int(((y == 0) & (pred == 0)).sum())
    fp = int(((y == 0) & (pred == 1)).sum())
    fn = int(((y == 1) & (pred == 0)).sum())
    sens = safe_div(tp, tp + fn)
    spec = safe_div(tn, tn + fp)
    ppv = safe_div(tp, tp + fp)
    npv = safe_div(tn, tn + fn)
    agreement = safe_div(tp + tn, len(y))
    bal = np.nanmean([sens, spec]) if not (np.isnan(sens) and np.isnan(spec)) else float("nan")
    p_yes_y = float(y.mean()) if len(y) else float("nan")
    p_yes_p = float(pred.mean()) if len(pred) else float("nan")
    pe = p_yes_y * p_yes_p + (1 - p_yes_y) * (1 - p_yes_p)
    kappa = safe_div(agreement - pe, 1 - pe) if not np.isnan(agreement) else float("nan")
    return {
        "sensitivity": sens, "specificity": spec, "balanced_accuracy": bal,
        "ppv": ppv, "npv": npv, "agreement": agreement, "kappa": kappa,
        "tp": tp, "tn": tn, "fp": fp, "fn": fn, "n": len(y),
        "reference_prevalence": float(y.mean()) if len(y) else float("nan"),
        "positive_rate": float(pred.mean()) if len(pred) else float("nan"),
    }


def bootstrap(df: pd.DataFrame, pred_col: str, n_boot: int, seed: int) -> dict[str, tuple[float, float, int]]:
    rng = np.random.default_rng(seed)
    clusters = np.array(sorted(df["source_id"].astype(str).unique()))
    vals = {m: [] for m in METRICS}
    grouped = {cid: g for cid, g in df.groupby(df["source_id"].astype(str), sort=False)}
    for _ in range(n_boot):
        sampled = rng.choice(clusters, size=len(clusters), replace=True)
        boot = pd.concat([grouped[c] for c in sampled], ignore_index=True)
        m = metric_dict(boot["reference"].to_numpy(), boot[pred_col].to_numpy())
        for key in METRICS:
            if np.isfinite(m[key]):
                vals[key].append(m[key])
    out = {}
    for key, arr in vals.items():
        if arr:
            lo, hi = np.percentile(np.asarray(arr), [2.5, 97.5])
            out[key] = (float(lo), float(hi), len(arr))
        else:
            out[key] = (float("nan"), float("nan"), 0)
    return out


def load_judges(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--physician-reference", required=True, type=Path)
    p.add_argument("--judge-scores", required=True, type=Path)
    p.add_argument("--models", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--bootstrap", type=int, default=10000)
    p.add_argument("--seed", type=int, default=20260903)
    args = p.parse_args()

    ref = pd.read_csv(args.physician_reference, dtype=str)
    ref = ref[ref["unsafe_overconfident_reference"].isin(["0", "1"])].copy()
    ref["reference"] = ref["unsafe_overconfident_reference"].astype(int)

    cfg = yaml.safe_load(args.models.read_text(encoding="utf-8"))
    primary_ids = [j["judge_id"] for j in cfg.get("primary_judges", [])]
    if len(primary_ids) != 3:
        raise RuntimeError(f"primary analysis expects 3 blinded judges, got {primary_ids}")

    raw = load_judges(args.judge_scores)
    judge_rows = []
    for r in raw:
        if r.get("judge_id") not in primary_ids or r.get("judge_mode") != "blinded" or r.get("status") != "ok":
            continue
        score = r.get("score") or {}
        val = score.get("unsafe_overconfident")
        if val not in (0, 1, False, True):
            continue
        judge_rows.append({
            "response_id": str(r["response_id"]),
            "judge_id": str(r["judge_id"]),
            "judge_label": int(val),
        })
    jdf = pd.DataFrame(judge_rows)
    if jdf.empty:
        raise RuntimeError("no valid primary blinded judge scores")

    wide = jdf.pivot(index="response_id", columns="judge_id", values="judge_label").reset_index()
    merged = ref.merge(wide, on="response_id", how="left", validate="one_to_one")
    if merged[primary_ids].isna().any().any():
        missing = int(merged[primary_ids].isna().any(axis=1).sum())
        raise RuntimeError(f"{missing} physician calibration responses lack a complete primary judge trio")
    for jid in primary_ids:
        merged[jid] = merged[jid].astype(int)
    merged["panel_any"] = (merged[primary_ids].sum(axis=1) >= 1).astype(int)
    merged["panel_majority"] = (merged[primary_ids].sum(axis=1) >= 2).astype(int)

    endpoints = [*primary_ids, "panel_any", "panel_majority"]
    results = []
    for i, endpoint in enumerate(endpoints):
        obs = metric_dict(merged["reference"].to_numpy(), merged[endpoint].to_numpy())
        ci = bootstrap(merged, endpoint, args.bootstrap, args.seed + i * 1009)
        base = {
            "endpoint": endpoint,
            "n": obs["n"],
            "tp": obs["tp"], "tn": obs["tn"], "fp": obs["fp"], "fn": obs["fn"],
            "reference_prevalence": obs["reference_prevalence"],
            "positive_rate": obs["positive_rate"],
        }
        for metric in METRICS:
            lo, hi, valid_draws = ci[metric]
            base[metric] = obs[metric]
            base[f"{metric}_ci_low"] = lo
            base[f"{metric}_ci_high"] = hi
            base[f"{metric}_bootstrap_valid_draws"] = valid_draws
        results.append(base)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(args.out, index=False)
    print(pd.DataFrame(results).to_string(index=False))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
