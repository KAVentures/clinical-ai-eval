# Experimental independent binary endpoint study design

This local Python API supports prospective design of a fixed-sample,
single-arm binary event-rate study. No statistical library or network access
is required. It does not establish a clinically acceptable rate, qualify a
judge, validate a model, provide clinical clearance, or permit deployment.

## Before collecting outcomes

Specify a clinically meaningful adverse event, population, eligible unit,
reference adjudication, fixed intended denominator, unacceptable event rate
p0, anticipated rate p1 below p0, one-sided alpha and target power. Freeze
these choices prospectively. A qualified clinical and statistical team must
justify them. Do not select thresholds after observing results.

Use the API directly without modifying the existing CLI:

    from caeval.study_design_exact import fixed_design, grid_design, zero_event_n
    design = fixed_design(300, 0.05, 0.01)
    grid = grid_design([100, 200, 300, 500], 0.05, 0.01,
                       alpha=0.05, target_power=0.8)
    n = zero_event_n(0.01, alpha=0.05)

The fixed design tests the composite null p >= p0 with a lower-tail binomial
test. It returns a critical event count (or null if rejection is impossible),
rejection probability at p0 and power at the specified alternative p1.
Calculations use the exact binomial distribution numerically, not a normal
approximation, and a small conservative numerical margin at alpha. Floating
point calculations are not arbitrary precision. The grid reports the smallest
tested sample size meeting target power, never an untested global minimum;
discrete binomial power need not increase monotonically at every sample size.

## Rare-event uncertainty

    from caeval.study_design_exact import upper_event_bound
    bound = upper_event_bound(0, 100, alpha=0.05)

One-sided Clopper-Pearson upper limits are obtained by binomial CDF inversion.
With zero events the formula is 1-alpha**(1/n). At one-sided 95% confidence,
zero events in 100 independent observations still give an upper rate of
approximately 2.95%. To reach an upper limit no greater than 1%, 299
independent zero-event observations would be required. This is conditional on
zero events and is not a power calculation or a guarantee of study success.

## Missing outcomes

    from caeval.study_design_exact import outcome_sensitivity
    report = outcome_sensitivity(n_planned=100, n_observed=90, events=1)

There are ten unknown outcomes, not ten safe outcomes. The finite-cohort rate
can range from 1% to 11%, regardless of the complete-case estimate. The API
also calculates exact-binomial upper limits under the best and worst binary
completions. This is a sensitivity envelope, not a missing-at-random model.
An entirely missing cohort returns no complete-case estimate. Eligibility
changes must be resolved separately, not silently deleted from denominators.

## Scope and limits

All statistical intervals and power calculations require independent,
identically distributed Bernoulli units for the stated target population.
Multiple outputs, variants, readers, encounters from one patient, or cases
clustered by institution are not necessarily independent units. Use the
existing cluster-aware or paired analyses and a separate design appropriate to
those studies. A finite-cohort range itself requires no IID assumption.

No sequential stopping, multiplicity adjustment, noninferiority comparison,
cluster power, subgroup guarantees, sample representativeness, or correction
for imperfect reference adjudication is implemented. No result upgrades
decision_grade or experimental maturity. Supported sample sizes are 1 to
100,000, with up to 200 candidate grid entries; large grids may be costly.

Method: standard exact binomial test and Clopper-Pearson (1934) binomial
confidence interval inversion; no novelty is claimed for the statistics.
Tests use rational small-case enumeration, high-n behavior, boundary cases,
inversion, minimal zero-event sizes and exhaustive missingness completions.
Run: python -m pytest -q tests_unit/test_study_design_exact.py.

Future institutional integration should preserve prospective design choices
and their versioned provenance, with clinical and statistical review. This PR
does not edit central CLI, UI, registries, or pending integration branches.
