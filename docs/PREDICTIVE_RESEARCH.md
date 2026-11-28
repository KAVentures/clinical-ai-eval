# Local paired predictive research workflow

This experimental workflow composes the risk probability calibration and
clinical decision curve engines without changing their definitions. Both
analyses receive the same prospective cohort, model versions, endpoint,
horizon and reference labels. It does not validate a clinical product,
authenticate a model execution, prove clinical utility or authorize use.

## 1. Freeze a study before observing results

Example spec.json (illustrative identifiers and thresholds only):

    {
      "study": "hospital-study-1",
      "eligibility_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "reference_protocol_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "endpoint": "event",
      "horizon": "day30",
      "baseline_version": "v1",
      "candidate_version": "v2",
      "unit_ids": ["unit-a", "unit-b", "unit-c"],
      "bin_edges_bp": [0, 5000, 10000],
      "action": "refer",
      "thresholds_bp": [2000, 5000]
    }

Use independently approved eligibility and reference protocol documents
and substitute their actual SHA-256 digests. Declare a fixed eligible
inventory of pseudonymous unit IDs (up to 100,000), model versions,
calibration bins, and meaningful action thresholds. The example thresholds
are not clinical recommendations.

    python -m caeval.predictive_research freeze --spec spec.json --output plan.json

Retain the printed plan digest separately in the approved study record.
The plan incorporates the two child plan digests. The freeze does not prove
that it occurred before outcomes were inspected; institutional records must
establish timing, authorization, blinding and actual provenance.

## 2. Seal the three input inventories separately

Provide one JSON list per source, for example baseline-rows.json:

    [{"unit_id":"unit-a","probability_bp":2500},
     {"unit_id":"unit-b","probability_bp":null}]

Use the same schema for candidate probabilities. Reference rows use a
boolean label (true for the defined event, false for verified non-event,
null for unknown):

    [{"unit_id":"unit-a","label":true},
     {"unit_id":"unit-b","label":null}]

Probabilities are integer basis points in 0..10000, inclusive. An absent
row or null value means unknown. Duplicate and undeclared unit IDs are
rejected; unknown outcomes do not reduce the planned denominator.

    python -m caeval.predictive_research seal --plan plan.json \
      --expected-plan PLAN_DIGEST --role baseline --source-receipt SOURCE_DIGEST \
      --rows baseline-rows.json --output baseline.json

Repeat with roles candidate and reference and the corresponding input
files and source receipt digests. The printed bundle digest must be
retained independently for each role. Scope includes the exact plan and
version (or reference protocol). A source receipt digest is an external
provenance pointer supplied by the operator, not a verification of source
bytes. Verify those sources outside this workflow.

## 3. Analyze and replay

    python -m caeval.predictive_research analyze --plan plan.json \
      --expected-plan PLAN_DIGEST \
      --baseline baseline.json --expected-baseline BASELINE_DIGEST \
      --candidate candidate.json --expected-candidate CANDIDATE_DIGEST \
      --reference reference.json --expected-reference REFERENCE_DIGEST \
      --output report.json

Retain the printed report digest. The report includes coverage for all three
sources, paired Brier and calibration-bin results, decision curves,
sensitivity and specificity, and missing-data bounds, with no case-level
rows. All three source bundle checkpoints are verified by recomputing the
canonical content and checking their independently retained digests.

For reproducible replay, use the same arguments with the verify subcommand,
remove --output, and add --report report.json --expected-report REPORT_DIGEST.
It recomputes both analyses, not merely the report checksum. Exit status 0
means successful replay, 1 means report mismatch, and 2 means invalid input
or failed operation. The other commands exit 0 on success and 2 on failure.

## Boundaries

Read RISK_PROBABILITY.md and CLINICAL_DECISION_CURVE.md for statistical
assumptions and limitations. Results are descriptive finite-cohort evidence,
not confidence intervals, causal benefit, or clinical acceptance. Missingness
bounds across thresholds need not share one extremizing completion.

No patient identifiers, credentials or notes should appear in inputs.
Even pseudonymous rosters, hashes and aggregate counts can be sensitive.
Keep all files restricted; use a private working directory and ACLs.
Outputs are created exclusively, with owner-only POSIX permissions. The
CLI limits each input and output to 64 MiB. There is no network transport,
identity service, persistent institutional audit trail, automatic evaluation
execution or source-record verification. Independently retain the expected
digests; a party controlling all inputs and checkpoints can replace them.

This integration preserves the original engines from PRs #119 and #120
and does not change central application entrypoints or scientific maturity.
Further institutional integration must bind the local collection receipts
and permissions to the same study scope.

Synthetic integration tests:

    python -m pytest -q tests_unit/test_predictive_research.py
