# Experimental risk probability calibration study

A standalone research API for comparing binary clinical risk probabilities
from two versions on a fixed eligible cohort. Use the same endpoint, prediction
time and observation horizon for both arms. Not an intervention recommendation,
validated safety gate, population confidence interval or regulatory claim.

## Prospective plan

Freeze a protocol before seeing predictions or outcomes:

    from caeval.risk_probability import freeze, analyze
    frozen = freeze({
        "endpoint": "event",
        "horizon": "day30",
        "baseline_version": "v1",
        "candidate_version": "v2",
        "unit_ids": ["unit-a", "unit-b", "unit-c"],
        "bin_edges_bp": [0, 2500, 5000, 7500, 10000]
    })

Retain the plan digest independently in a controlled prospective study record.
The digest binds the declared plan, not actual execution, consent, model
provenance, clinical validity or the time it was created. IDs and protocol
tokens must be pseudonymous and contain no patient identifiers. Independently
validate eligibility, reference ascertainment, follow-up, and version identity.

## Analysis

    rows = [
        {"unit_id": "unit-a", "label": True, "baseline_bp": 4000, "candidate_bp": 8000},
        {"unit_id": "unit-b", "label": False, "baseline_bp": 3000, "candidate_bp": None}
    ]
    report = analyze(frozen, independently_retained_digest, rows)

Probabilities are integer basis points, 0 through 10,000 inclusive. Establish
a consistent rounding policy upstream. A label is a binary reference outcome
or null; an absent prediction is null. An omitted unit means unknown in both
arms and unknown reference. Duplicate and undeclared units are rejected.
Do not treat absence of follow-up as a negative outcome.

## Interpretation

Each arm includes observed scoring and reference counts, the complete-case
Brier score, and the sharp finite-cohort range of Brier scores across all
compatible missing values. Brier score is the mean squared difference between
predicted probability and binary outcome; lower is better. If a probability
is missing, its loss can range from zero to one. If the label is missing and
the probability is known, the range is the smaller and larger squared loss
under labels zero and one.

The paired delta is candidate minus baseline Brier score, not a clinical
outcome difference. A negative value favors candidate prediction accuracy by
this metric. Both arms share a single reference outcome, including when it
is unknown. The paired bounds minimize and maximize the loss difference
jointly for each unit and then average, avoiding the looser bounds obtained
by independently varying the reference in each arm. The extrema are attainable
for the specified unconstrained missing-value completions. A bound that spans
zero is inconclusive about the direction. Complete-case estimates can be
biased by selective missingness.

Calibration bins are fixed in the plan: left-inclusive, right-exclusive
except the final bin includes probability one. Each reports the mean
probability of scored units, the observed reference event rate, and the
possible event-rate range among those scored units given unknown labels.
Units missing a probability cannot be assigned a bin; the report preserves
the overall count of scored units. Empty bins return null estimates.
A Brier score combines calibration and discrimination properties; it is
not itself a pure calibration measure. No ranking or deployment decision
is generated.

## Limits

This is a finite-cohort descriptive sensitivity analysis, not a population
inference procedure. Repeated encounters may be dependent. No censoring
model, competing-risk analysis, calibration fitting, transportability
claim, confidence bands, automatic threshold optimization or clinical utility
analysis is provided. Binned rates do not establish calibration for missing
scores. Do not select bins after seeing results. Reports have
decision_grade=false and publication_safe=false; small counts can be
sensitive. Keep all data and results local under institutional controls.

The feature is independent of the existing imaging assessment and shared
CLI/web integration. It does not duplicate image handling or alter
existing scientific maturity. Future coordinated integration should
verify the plan, prediction receipts and reference provenance together.

Run isolated synthetic tests:
    python -m pytest -q tests_unit/test_risk_probability.py
