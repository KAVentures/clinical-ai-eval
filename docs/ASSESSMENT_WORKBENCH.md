# Coordinated local hospital assessment workbench

The assessment workbench joins the three local workbenches without changing
their engines or elevating the scientific maturity of their results.
It provides a common operator CLI and restricted, replayable JSON export.

## Prepare the three component workflows

Follow VENDOR_WORKBENCH.md, PREDICTIVE_RESEARCH.md and
PROSPECTIVE_RESEARCH_WORKBENCH.md. Set up the buyer request and procurement
database, freeze the predictive study and binary research plan, and retain
their independent checkpoints. Both research plans must identify the same
study, baseline version, candidate version and exact pseudonymous eligible
unit inventory. The candidate version must equal the buyer request version.
Endpoints may differ and remain explicitly separate.

These checks establish matching declared scope, not that version strings
uniquely identify a real model or that pseudonymous IDs represent the same
individuals in the source systems. The institution must verify source
provenance and model identity, including underlying configurations.

Before seeing results, freeze the combined plan:

    python -m caeval.assessment_workbench freeze --request request.json --expected-request REQUEST_DIGEST --root VENDOR_ROOT --predictive-plan predictive-plan.json --binary-plan binary-plan.json --output assessment-plan.json

Retain the printed digest independently. This binds the two research plan
digests, buyer request, version, roster and procurement workspace root.
The binary research plan and actual procurement database are fully
revalidated during analysis. A plan alone is not proof of prospectivity.

## Local operator configuration

Prepare a trusted local config.json with the following fields. Paths are
resolved relative to the operator's working directory; absolute paths
also work. Do not execute configurations received from vendors.

    {
      "db": "procurement-local/workbench.db",
      "head": "CURRENT_VENDOR_HEAD",
      "as_of": "2026-12-06",
      "predictive_plan": "predictive-plan.json",
      "bundles": {
        "baseline": "baseline.json",
        "candidate": "candidate.json",
        "reference": "reference.json"
      },
      "expected_bundles": {
        "baseline": "BASELINE_DIGEST",
        "candidate": "CANDIDATE_DIGEST",
        "reference": "REFERENCE_DIGEST"
      },
      "binary_plan": "binary-plan.json",
      "manifest": "manifest.json",
      "keys": "keys.json",
      "outcomes": "outcomes.json",
      "reference_counts": "development-counts.json",
      "target_counts": "holdout-counts.json"
    }

Replace placeholders with independently retained digests. The key file
uses the restricted format described in the prospective workbench manual;
never put secrets in the configuration or command line. The as-of date is
an administrative reporting date, not a trusted timestamp. Preserve the
original outcomes, feature counts and input checkpoints for replay.

    python -m caeval.assessment_workbench analyze --plan assessment-plan.json --expected-plan PLAN_DIGEST --config config.json --output assessment-report.json

This replays the procurement database at the exact expected head, rechecks
sealed prediction/reference bundles, and reruns binary and population
analyses against the audited holdout. The report includes all three native
reports, their digests, mandatory document gaps, clarification counts,
prediction coverage and marginal population-shift flags. The two clinical
endpoints are not combined into a score or release decision.

## Reproduce the report

Retain the printed report digest independently. Run:

    python -m caeval.assessment_workbench verify --plan assessment-plan.json --expected-plan PLAN_DIGEST --config config.json --report assessment-report.json --expected-report REPORT_DIGEST

Verification recomputes the report using the original input data and
checkpoints. Exit 0 means replay succeeded; exit 1 means a report mismatch;
exit 2 means invalid input or failed operation. Freeze and analyze return
0 on success or 2 on failure. Outputs are exclusive, owner-only files on
POSIX; use appropriate restricted directories and ACLs on Windows.

If the live procurement database has advanced beyond the requested head,
replay refuses the stale checkpoint. To reproduce an older report, retain
a consistent private database snapshot at the original head and the
corresponding external checkpoint. This is not a historical snapshot
service. Back up according to the procurement workbench instructions.

## Limitations and integration

This is a trusted single-operator offline CLI/API, not an authenticated
multi-user web application or a public evidence exchange. The report is
restricted, includes site and subgroup aggregates, and is not automatically
safe to publish or send to a vendor. The tool does not verify external
source records, independent hospital execution, clinical benefit,
regulatory conformity, or deployment eligibility. It cannot replace
physician review, governance or independently retained original evidence.
The binary and predictive sections retain their distinct missing-data
and statistical limitations. No clinical pass/fail, ranking, aggregate
safety score or deployment approval is generated.

The integration retains the original commits and files from workbench PRs
#125, #126 and #127; no central application files are changed. All three
workbenches remain separately usable. Use a merge preserving ancestry
after full repository CI, and verify inclusion before closing source PRs.

Run all targeted workbench suites and their dependency tests, including
tests_unit/test_assessment_workbench.py, before integration. Tests use
synthetic data only.
