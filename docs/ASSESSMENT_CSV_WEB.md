# Local assessment CSV import

Launch the integrated selector and protocol forms with CSV support:

    python -m caeval.assessment_csv_web --root /private/workspaces --staging-root /private/staging

Use the owner-only directories, local session password and trust boundaries
in ASSESSMENT_WORKSPACE_WEB.md. Create a named workspace, place approved
pseudonymous CSV exports in its matching private staging subdirectory, and
choose Import cohort CSV from the workspace selector. No upload endpoint,
arbitrary path selection or remote data transfer is provided.

## Before the prospective freeze

Use UTF-8 (optional BOM), comma-separated files with exact headers:

- manifest.csv: record_id,partition,patient_key,lineage_key,content_sha256,site_key,observed_at
- memberships.csv: unit_id,group

Use the dataset and membership schemas documented in DATASET_INTEGRITY.md
and PROSPECTIVE_RESEARCH_WORKBENCH.md. The membership file must contain
exactly the test roster; blank group means unknown membership. Blank optional
manifest values mean unknown, not a unique identifier. The native protocol
form and subsequent freeze run the complete leakage and subgroup checks.
CSV import alone does not establish a valid prospective study.

Preview shows row counts and a digest bound to the exact staged bytes.
Confirm to stage the two JSON files exclusively, then configure the protocol
and preview/confirm the prospective plan separately. No outcome files are
read during the cohort step.

## After freezing and collecting outcomes

Choose Import collection CSV. Stage these seven files:

| File | Exact header |
|---|---|
| collection.csv | as_of,baseline_sha256,candidate_sha256,reference_sha256 |
| baseline-rows.csv | unit_id,probability_bp |
| candidate-rows.csv | unit_id,probability_bp |
| reference-rows.csv | unit_id,label |
| outcomes.csv | unit_id,baseline,candidate |
| reference-counts.csv | feature,level,count |
| target-counts.csv | feature,level,count |

Collection metadata has one row with ISO date and independently verified
source receipt pointers. Probabilities are integer basis points (0..10000),
outcomes are lowercase true or false, and blank probabilities or labels
mean unknown. A header-only prediction/outcome file represents no supplied
observations. Omitted units remain in the planned denominator. Population
counts are nonnegative integers; give every declared category of a supplied
feature, including explicit zeros. A wholly absent feature is unknown.

Preview validates the collection using the real workbenches and the frozen
plan; confirmation binds the exact CSV bytes, plan, procurement head and
reproduced report. If any change occurs, preview again. This stages the
seven JSON files but does not commit the collection. Use the existing
collection preview/confirmation and review dashboard to seal, run, replay
and download. Retain checkpoints independently.

## Limits and safety

Exact column order, no extra columns, no whitespace padding, no control
characters, up to 256 characters per cell, 100000 rows per file, and 16 MiB
combined staged CSV and separately combined canonical JSON. Malformed CSV,
unsafe files, duplicate unit or category records and existing destination
files are refused. Handled failures remove only newly created JSON files;
process interruption is not crash-atomic. No spreadsheet formulas are
executed. Input IDs must be pseudonymous; this is not de-identification,
clinical source verification or a data collection engine. Never include
raw patient records, credentials or sensitive narrative. No clinical
acceptance or deployment decision is produced. All original entrypoints
remain available. Full institutional authentication is separate work.

    python -m pytest -q tests_unit/test_assessment_csv_web.py
