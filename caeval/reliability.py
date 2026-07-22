"""Agreement statistics — CANONICAL SOURCE (§7, §11):
clinical-evidence-sufficiency-llm/src/reliability.py.

Inherited: `percent_agreement`, `cohen_kappa`, `krippendorff_alpha_nominal`,
`adjudicate_majority`. The upstream `cohen_kappa` calls sklearn; this box has no
sklearn, so kappa is computed directly (identical definition) with numpy. Inputs
are plain sequences of labels (None = missing) rather than pandas Series, so the
module carries no pandas dependency.
"""
from __future__ import annotations

import itertools
from typing import Sequence

import numpy as np


def _pairwise_valid(a: Sequence, b: Sequence):
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    return pairs


def percent_agreement(a: Sequence, b: Sequence) -> float:
    pairs = _pairwise_valid(a, b)
    if not pairs:
        return float("nan")
    return float(np.mean([x == y for x, y in pairs]))


def cohen_kappa(a: Sequence, b: Sequence) -> float:
    """Cohen's kappa for two raters over paired labels (None dropped)."""
    pairs = _pairwise_valid(a, b)
    if not pairs:
        return float("nan")
    xs = [x for x, _ in pairs]
    ys = [y for _, y in pairs]
    labels = sorted(set(xs) | set(ys))
    if len(labels) < 2:
        # perfect agreement on a single class -> kappa undefined; return 1.0 if they match
        return 1.0 if all(x == y for x, y in pairs) else 0.0
    idx = {lab: i for i, lab in enumerate(labels)}
    n = len(pairs)
    conf = np.zeros((len(labels), len(labels)), dtype=float)
    for x, y in pairs:
        conf[idx[x], idx[y]] += 1
    po = np.trace(conf) / n
    row = conf.sum(axis=1) / n
    col = conf.sum(axis=0) / n
    pe = float(np.sum(row * col))
    if pe == 1.0:
        return 1.0
    return float((po - pe) / (1 - pe))


def krippendorff_alpha_nominal(ratings: list[list]) -> float:
    """Nominal Krippendorff alpha. `ratings` = rows are items, entries are per-rater
    labels (None allowed). Inherited verbatim in logic from upstream."""
    observed_disagreement = 0.0
    observed_pairs = 0
    all_values: list = []
    for row in ratings:
        row = [x for x in row if x is not None]
        all_values.extend(row)
        for a, b in itertools.combinations(row, 2):
            observed_disagreement += float(a != b)
            observed_pairs += 1
    if observed_pairs == 0:
        return float("nan")
    observed = observed_disagreement / observed_pairs
    expected_pairs = list(itertools.combinations(all_values, 2))
    if not expected_pairs:
        return float("nan")
    expected = float(np.mean([a != b for a, b in expected_pairs]))
    if expected == 0:
        return 1.0
    return float(1 - observed / expected)


def adjudicate_majority(ratings: list[list]):
    """Per-item majority label across raters (ties -> first modal value)."""
    out = []
    for row in ratings:
        vals = [x for x in row if x is not None]
        if not vals:
            out.append(None)
            continue
        counts: dict = {}
        for v in vals:
            counts[v] = counts.get(v, 0) + 1
        out.append(max(counts, key=lambda k: (counts[k], -vals.index(k))))
    return out
