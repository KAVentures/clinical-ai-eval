# Experimental dataset partition integrity

A local, standalone research-design preflight for hospital and vendor evaluation
datasets. Audit before evaluation or model tuning, not after inspecting outcomes.
It detects overlap in the supplied metadata, not contamination in a model's
unknown training corpus. It is not clinical validation, a release gate, a
privacy certification, or proof of independence.

## Input contract

Supply a JSON array, at most 100,000 records and 64 MiB via the CLI. Each record
must have exactly these seven fields:

| Field | Contract |
|---|---|
| record_id | Unique local pseudonymous record identifier |
| partition | train, tune, test; null for an unassigned planning input |
| patient_key | Stable pseudonymous patient linkage, or null if unknown |
| lineage_key | Stable original clinical case/source lineage shared by its variants, or null |
| content_sha256 | Lowercase 64-character SHA-256 of consistently canonicalized source content, or null |
| site_key | Stable site pseudonym, or null |
| observed_at | ISO calendar date YYYY-MM-DD or null |

Create the manifest in the hospital-controlled environment. Do not insert patient
names, identifiers from an EHR, raw notes, or raw image data. Linkage keys must
use the same namespace across partitions. A pseudonymized file is still
potentially sensitive. Content hashes can also be sensitive. This tool cannot
validate upstream tokenization, canonicalization, completeness or data rights.

## Local workflow

Create a random binary key once, store it privately, and retain it with the
restricted manifest. On a POSIX host, for example:

    umask 077
    python -c 'import secrets; open("audit.key", "xb").write(secrets.token_bytes(32))'

Audit an existing partition (grouped, temporal, site, or temporal_site):

    python -m caeval.dataset_integrity_cli audit --manifest split.json --key-file audit.key --design temporal_site > receipt.json

Verify the exact aggregate receipt against the original manifest and key:

    python -m caeval.dataset_integrity_cli verify --manifest split.json --key-file audit.key --receipt receipt.json

Plan an unassigned grouped split before outcomes are collected:

    python -m caeval.dataset_integrity_cli plan --manifest unassigned.json --key-file audit.key --output-manifest planned.json --train 60 --tune 20 > planning-report.json

The planner uses connected components over patient, case lineage and exact
content digest, including transitive links. Components cannot be divided.
It uses deterministic greedy allocation and keyed tie breaking. Ratios are
targets, not guaranteed; inspect achieved sizes and largest component. It does
not enforce temporal or site holdouts, balance labels, or optimize subgroup
representation. Audit those separately; do not repeatedly select keys or
partitions to obtain a favorable result. The output manifest is sensitive and
created exclusively (no overwrite; owner-only POSIX permissions). On Windows
use an appropriately restricted directory and ACL.

## Semantics

Every pair of train/tune/test partitions is checked for patient, lineage and
exact-content overlap. Temporal holdout requires every known development day to
precede every known test day, strictly; site holdout requires disjoint known
site sets. Missing required linkage or design metadata and absent development
or test data are incomplete, not evidence of no overlap.

- FAIL: detected overlap or design violation.
- INCOMPLETE: required evidence missing without a detected violation.
- NO_DETECTED_OVERLAP: no violations detected in the supplied complete metadata.

Audit exits 0 only for NO_DETECTED_OVERLAP, 1 for FAIL or INCOMPLETE, 2 for
invalid or inaccessible input/output. Verify exits 0 for an exact matching
receipt, 1 for a mismatch, 2 for unreadable input. Matching a FAIL receipt
does not make the dataset pass. Plan exits 0 on success and 2 for rejected
inputs or unavailable output. The plan's nested audit is the receipt; extract
that object for verification.

HMAC-SHA256 with domain separation binds canonical sorted manifest rows and
the aggregate report. This is a local symmetric integrity receipt, not a
signature, timestamp, or third-party attestation; a key holder can recreate it.
A verifier recomputes the full report. Reports omit raw IDs, site values and
dates, but small counts may still disclose information. Do not automatically
publish receipts.

No similarity search, semantic duplicate detection, training-data provenance
verification, patient-linkage inference, fairness certification, or statistical
independence claim is made. These checks complement prospective clinician
validation and must not upgrade experimental maturity or decision_grade.

## Integration

This addition requires no shared CLI or browser router edits. Use the Python
module directly or invoke its standalone CLI. Future institutional integration
should bind an approved manifest and receipt into the study's existing
authorization and audit chain, preserving its scope and decision_grade=false.

Targeted tests: python -m pytest -q tests_unit/test_dataset_integrity.py.
All fixtures are synthetic and are not clinical performance evidence.
