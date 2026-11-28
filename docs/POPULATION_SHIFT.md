# Experimental population comparability and distribution shift

This local aggregate-only analysis compares prespecified marginal categorical
distributions in a reference cohort and a target cohort. It is descriptive:
it does not establish clinical transportability, population representativeness,
causal effects, clinical performance or deployment readiness.

## Freeze the prospective specification

Agree on fixed eligibility, observation windows, a common coding dictionary
and disjoint exhaustive categories for each variable. Continuous variables
must be categorized using a prospectively specified upstream binning rule.
The coding_version identifies that external dictionary; this API cannot
verify that the actual source data use it. Treat unmapped data as unknown
rather than inventing a category or changing bin boundaries.

Example spec.json (illustrative, not a clinically justified threshold):

    {
      "study": "external-validation-1",
      "reference_population": "development",
      "target_population": "hospital-a",
      "coding_version": "dictionary-v1",
      "reference_n": 1000,
      "target_n": 500,
      "features": [
        {"feature": "age-band", "levels": ["under65", "65plus"],
         "warning_bp": 1500},
        {"feature": "setting", "levels": ["inpatient", "outpatient"],
         "warning_bp": 1000}
      ]
    }

A warning limit is in basis points of total variation, not a clinical
acceptability threshold. Use an independently justified limit and fix it
before viewing distributions. A zero denominator is rejected. Each
population can have at most 10 million units, 100 features and 100 levels
per feature.

    python -m caeval.population_shift_cli freeze --spec spec.json --output plan.json

Retain the printed digest in an independently controlled study record.
The digest binds the declared plan, not actual eligibility, data provenance,
or when the freeze was performed.

## Aggregate inputs

Each cohort supplies a JSON object mapping declared features to a complete
mapping of their declared category counts, including explicit zeros.
For example, reference-counts.json:

    {"age-band":{"under65":550,"65plus":400},
     "setting":{"inpatient":200,"outpatient":800}}

and target-counts.json:

    {"age-band":{"under65":180,"65plus":280},
     "setting":{"inpatient":270,"outpatient":200}}

The difference between the planned denominator and the sum of the known
category counts is unknown. A completely omitted feature is entirely
unknown. Unknown features and partial category dictionaries are rejected,
as are negative, fractional or excessive counts. Each unit must belong
to exactly one level or be unknown for a feature. This aggregate API
cannot detect duplicate units or misclassified observations upstream.

    python -m caeval.population_shift_cli analyze --plan plan.json --expected-digest DIGEST --reference-counts reference-counts.json --target-counts target-counts.json --output report.json

Python callers can use freeze(spec) and analyze(frozen, expected_digest,
reference_counts, target_counts).

## Interpreting total variation

Total variation (TV) between distributions p and q is half the sum of
absolute differences of their category probabilities. It ranges from
0 (identical marginal distribution) to 1 (disjoint support). It is also
the largest probability difference over subsets of categories.

Observed-only TV normalizes over known observations in each cohort. It
can be badly misleading if missingness differs. The report therefore
retains planned denominators, missingness rates, per-level share bounds,
and full-cohort TV bounds.

Let p_i and q_i denote the known counts divided by their respective full
denominators; m_p and m_q denote the unknown fractions. Write d_i=p_i-q_i.
Every completion has TV at least max(0, sum of positive d_i minus m_q):
the unknown target mass can remove at most m_q of the known positive
differences. This lower bound is exact for continuously divisible missing
mass but can be conservative for integer observations with unequal
denominators. The upper bound is attainable with integer observations:
for each nonempty proper subset S, place all reference unknowns in S
and all target unknowns outside S. The maximizing S is a sorted prefix
of the d_i values, so no exponential subset enumeration is required.
The implementation compares limits using exact rational arithmetic.

- AT_OR_ABOVE_LIMIT: every compatible completion meets or exceeds the
  prespecified warning limit.
- BELOW_LIMIT: all compatible completions fall strictly below the limit.
- INCONCLUSIVE: the bounds do not resolve the warning status.

These are finite-cohort descriptive flags, not hypothesis tests. A small
TV does not establish equivalence or transportability. Marginal comparisons
can miss substantial joint, label, conditional or concept shift.
Confidence intervals, dependency adjustment, reweighting, predictive
performance assessment and causal transport are outside this module.

## Local restrictions

The CLI reads bounded JSON, rejects duplicate object keys and creates new
outputs exclusively, with owner-only permissions on POSIX. On Windows,
use an appropriately restricted directory and ACL. Aggregate counts and
small cells can still disclose sensitive data; nothing here provides
anonymization or automatic publication approval. All reports set
decision_grade=false and publication_safe=false.

No new dependencies, modifications to existing modules, or institutional
routing are required. Future integration should bind verified extraction
receipts and source authorization to the prospectively approved plan.

Synthetic tests (including exhaustive integer missing-value completions):

    python -m pytest -q tests_unit/test_population_shift.py
