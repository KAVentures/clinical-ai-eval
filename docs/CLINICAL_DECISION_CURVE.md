# Experimental clinical decision curves and operating points

A local fixed-cohort evaluation of score-triggered actions, with explicit
missingness sensitivity analysis. This is not a recommendation to act on an
individual patient, evidence of causal benefit, or a clinical release gate.

## Prospective specification

Agree the binary target, reference standard, eligibility, prediction time,
observation horizon, intended action, two model versions, and meaningful
thresholds with the clinical/statistical team before inspecting outcomes.
Thresholds represent assumed relative harm/benefit trade-offs. The module
does not determine clinically appropriate thresholds.

Example spec.json (synthetic identifiers and illustrative thresholds only):

    {"endpoint":"event","horizon":"day30","action":"refer",
     "baseline_version":"v1","candidate_version":"v2",
     "unit_ids":["a","b","c"],"thresholds_bp":[2000,5000]}

    python -m caeval.clinical_decision_curve_cli freeze --spec spec.json --output plan.json

Retain the printed digest independently in the approved prospective record.
It binds the plan but does not prove timing, identity, consent, or actual
execution. Integer probabilities and thresholds use basis points; thresholds
must be strictly between zero and one. Maximum 50 thresholds and 100,000
units. The score triggers the hypothetical action when it is at least the
threshold. Both versions use the same reference outcome and threshold.

Example outcomes.json:

    [{"unit_id":"a","label":true,"baseline_bp":1000,"candidate_bp":8000},
     {"unit_id":"b","label":null,"baseline_bp":3000,"candidate_bp":null}]

    python -m caeval.clinical_decision_curve_cli analyze --plan plan.json --expected-digest DIGEST --outcomes outcomes.json --output report.json

Use boolean reference labels or null, and integer scores or null. An absent
row leaves both scores and the reference unknown. Never code a failed
prediction or unascertained reference as a negative. Duplicate and undeclared
units are rejected. The Python API provides freeze and analyze directly.

## Net benefit and operating points

For a threshold p, the standard decision-curve net benefit is
TP/N - FP/N * p/(1-p). See Vickers and Elkin, Decision curve analysis:
a novel method for evaluating prediction models (Medical Decision Making,
2006;26:565-574). Here action denotes a prespecified hypothetical referral,
investigation or other binary decision, not a necessarily beneficial treatment.
The threshold fixes the relative cost of a false-positive action. Net benefit
is expressed in true-positive-equivalent units per eligible unit. It is not
a predicted reduction in morbidity, mortality, or expenditure.

The four policies are baseline, candidate, act on all, and act on none.
For each policy, report known action count, complete confusion counts,
complete-case sensitivity/specificity and net benefit. Complete-case
estimates for different policies can have different denominators. Compare
only the paired contrasts or full-cohort bounds for the same cohort.

At each threshold, candidate-minus-comparator net benefit is computed for
baseline, act-all and act-none. The full-cohort bounds evaluate all
compatible binary completions of missing reference and unknown score-based
actions. A missing score can imply either action. Candidate and comparator
share the same unknown reference, so their extrema are calculated jointly
rather than by subtracting independently varying endpoint ranges. The
fixed denominator is the entire prospective cohort. A positive lower bound
indicates a positive finite-cohort difference under the stipulated
completions and weight, not evidence that implementation is beneficial.

Sensitivity and specificity ranges are sharp conditional on completions
where the relevant positive or negative class exists. If no such class is
possible, the bounds are null. Unknown class membership is allowed to vary;
unknown actions are not counted as negative. The ranges are not confidence
intervals. Each threshold is analyzed independently: extrema at different
thresholds need not occur under the same underlying missing scores.

## Limits and safety

Assumptions include a fixed binary endpoint and common reference, with
comparable score interpretation, threshold weights and observation horizons.
Do not use this descriptive analysis as a replacement for causal treatment
effect estimation, trial evidence, prospective deployment studies, or
clinical judgment. It does not account for treatment-dependent outcomes,
censoring, competing risks, sample selection, clustering, uncertainty in
reference validity, or population confidence intervals. It does not optimize
thresholds, rank products, or prescribe an operating point. Thresholds must
be prespecified; do not choose a favorable one after viewing results.
Net benefit is not a pure discrimination or calibration metric.

Keep pseudonymous inputs and restricted reports inside the hospital
environment. The CLI writes exclusively to new output files, with owner-only
POSIX mode; use appropriately restricted directories and ACLs on Windows.
Reports mark decision_grade and publication_safe false. No maturity change
or browser/central CLI integration is included. Future integration must
verify prospective authorization, reference provenance and model receipts.

Synthetic tests include exhaustive enumeration of conditional operating
point bounds across all three-unit missingness patterns, paired utility
extrema, plan tampering, boundary thresholds, invalid inputs and CLI
roundtrip. Run:

    python -m pytest -q tests_unit/test_clinical_decision_curve.py
