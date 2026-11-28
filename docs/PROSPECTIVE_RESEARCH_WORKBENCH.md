# Prospective binary research workbench

A restricted local workflow integrating dataset integrity, statistical design,
subgroup analysis, multisite outcome analysis and population-shift analysis.
It evaluates an already curated cohort and externally collected paired binary
adverse-event outcomes. It is not a clinical trial system, a source extraction
engine, or a clinical release gate.

## Freeze before inspecting outcomes

Prepare the partition manifest using DATASET_INTEGRITY.md. Every test record
must have a valid pseudonymous record ID and site token. Linkage metadata must
be complete for the selected holdout design; detected leakage, an incomplete
audit, missing test sites and incomplete subgroup membership all prevent
freezing. An audit with no detected overlap does not prove that the underlying
identities or development data are complete or correct.

Example spec.json (illustrative, not recommended clinical thresholds):

    {
      "study": "hospital-study-1",
      "endpoint": "adverse-event",
      "horizon": "day30",
      "reference_protocol": "reference-v1",
      "baseline_version": "v1",
      "candidate_version": "v2",
      "holdout_design": "temporal_site",
      "subgroup_levels": ["group-a", "group-b"],
      "minimum_group": 20,
      "coding_version": "dictionary-v1",
      "shift_features": [
        {"feature":"setting","levels":["inpatient","outpatient"],"warning_bp":1500}
      ],
      "binary_design": null
    }

Set binary_design to null unless independent, identically distributed
Bernoulli units and a fixed-sample plan are justified. If justified, use
an object with iid_assumed=true, unacceptable_rate, anticipated_rate,
and alpha. These values must be approved prospectively. Repeated declared
patient keys within the test cohort are refused for IID planning; unique
keys alone do not prove independence, particularly within hospitals.
The optional exact-design calculation evaluates the fixed cohort size.
It does not perform prospective recruitment or choose a clinical threshold.

Prepare memberships.json mapping every test record ID to one declared
subgroup or null for unknown membership. Development IDs must not appear.
Choose adequate subgroup definitions and minimum sizes before evaluation.

Generate distinct cryptographically random 32-byte keys using approved
local tooling. Store keys.json as a restricted file with two fields:
audit (64 lowercase hexadecimal characters) and sites (a mapping from
each test site to its own distinct 64-character hexadecimal key). Do
not put keys in command-line arguments, logs or repositories. On POSIX
the key file must be owned by the operator and inaccessible to group
and other users; on Windows use a restricted ACL.

    python -m caeval.prospective_research freeze --spec spec.json --manifest manifest.json --memberships memberships.json --keys keys.json --output plan.json

Retain the printed plan digest independently in the approved study record.
The plan incorporates the authenticated dataset audit and all child plans.
It contains pseudonymous membership records; keep it private.

## Analyze

Use paired binary adverse outcomes as described in SUBGROUP_EVIDENCE.md:
unit_id, baseline and candidate. The two outcomes are boolean or null.
An omitted eligible record stays unknown; no development record may enter
the evaluation. Use independently verified reference outcomes and model
versions. This workflow does not establish that references are accurate.

Reference feature counts represent the combined train/tune population;
target counts represent the entire test population. Use the same frozen
coding definitions and the aggregate format in POPULATION_SHIFT.md.
The denominators come from the audited manifest; aggregate count provenance
and membership are external assumptions, not verified by this tool.

    python -m caeval.prospective_research analyze --plan plan.json --expected-plan PLAN_DIGEST --manifest manifest.json --keys keys.json --outcomes outcomes.json --reference-counts development-counts.json --target-counts holdout-counts.json --output report.json

The report contains separate dataset audit, study-design (if selected),
subgroup, multisite, and population-shift sections. No combined score,
pass/fail clinical decision, ranking or deployment recommendation is
generated. The site receipts are created by this single trusted local
process from its own cohort; they are not independent hospital attestations
and are not a federated data collection workflow. Use MULTISITE_VALIDATION.md
for separate-site receipt exchange. Outcome values are not copied as rows
into the report, but small groups and aggregate receipts can reveal
sensitive information. The input HMAC binds the supplied outcome rows and
feature counts to this plan without publishing an unkeyed outcome hash.

## Reproduce

Retain the printed report digest independently. Run the same command with
verify instead of analyze, remove --output, and add --report report.json
--expected-report REPORT_DIGEST. The verifier recomputes the original plan,
dataset audit, child analyses, authenticated inputs and canonical report.
It returns 0 for successful replay, 1 for a report mismatch and 2 for
invalid inputs. Freeze and analyze return 0 on success and 2 on failure.
Output files are created exclusively, with owner-only permissions on POSIX.
Each CLI JSON file is limited to 64 MiB (keys to 64 KiB).

The exact-design upper bounds are conditional on IID assumptions and do
not adjust for clustered encounters, multiple endpoints or outcome
selection. Population-shift flags only compare prespecified categorical
marginals. Read the underlying five module documents for their individual
statistical and security limitations. No clinical validation, authorization,
external source verification, privacy guarantee, or proof of prospectivity
is claimed. Reports remain restricted. Keep checkpoints and keys under
independent control; possession of both data and keys permits rewriting
history. No central application files, external services or database
migrations are required.

## Tests

    python -m pytest -q tests_unit/test_dataset_integrity.py tests_unit/test_study_design_exact.py tests_unit/test_subgroup_evidence.py tests_unit/test_multisite_validation.py tests_unit/test_population_shift.py tests_unit/test_prospective_research.py

Source PRs #116, #117, #118, #121 and #122 are included unchanged with their
original Git ancestry. Full repository integration CI is required before
merging the combined candidate.
