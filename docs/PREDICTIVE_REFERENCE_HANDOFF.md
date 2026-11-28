# Source-bound predictive reference coordinator

This standalone coordinator connects the prospective reference panel and
private physician review browser to the hospital assessment collection. It
uses the existing frozen predictive protocol and a separate authorized source
inventory; it does not determine clinical ground truth automatically.

## Prepare and freeze the panel

Use the unfinished assessment's private staging directory. Prepare two files:

- reference-protocol.txt: exact approved UTF-8 bytes corresponding to the
  frozen predictive study's reference protocol SHA-256.
- reference-sources.json: object mapping every planned unit token to its
  approved, pseudonymous reference source text. No model predictions or
  other readers' ratings should be included. Screen evidence independently.

Each source and protocol is limited to 64 KiB; the source JSON is limited
to 16 MiB. Use the private owner-only directories described in
ASSESSMENT_WORKSPACE_WEB.md. Prepare actors.json outside the reviewer folders
with distinct declared coordinator, reader_a, reader_b and adjudicator tokens.
All coordinator CLI commands take:

    --directory /private/staging --plan /private/workspace/predictive-plan.json --expected-plan PLAN_DIGEST

Run `python -m caeval.predictive_reference_handoff preview` with those
arguments and `--actors /private/actors.json`. Independently approve and retain
the printed panel digest before `freeze` with the same arguments plus
`--expected-panel PANEL_DIGEST`. This writes reference-panel.json exclusively.
Source bytes and the complete planned roster are checked; a clinical source
being authentic or sufficient is not established by its digest.

## Distribute isolated review folders

Use `export --expected-panel PANEL_DIGEST --output-root /private/reviewers
--role reader_a` with the common arguments, and repeat for reader_b. The
output root must already be private and separate from the coordinator staging
directory (neither may be nested within the other). Each new role directory
contains only packet.json, protocol.txt and sources.json. No existing directory
is overwritten. Give each reader only their own folder and the independently
retained packet digest. Never give a reader the shared parent directory or
coordinator files; copy through an approved restricted handoff. The local
reviewer browser and exact launch command are documented in
PREDICTIVE_REFERENCE_REVIEW_WEB.md. Restrict access using institutional ACLs.

Collect only the sealed submission.json from each reviewer, copy it into
staging as reader_a-submission.json or reader_b-submission.json, and retain
its submission digest independently. Run `export --role adjudicator` with the
common arguments, `--expected-panel`, `--output-root`, `--expected-reader-a`
and `--expected-reader-b`. This folder contains only known-disagreement
source items, without primary ratings. Review and return the sealed submission
as adjudicator-submission.json, retaining its digest. An empty disagreement
roster is supported. If adjudication is not completed, leave it absent and
report unresolved labels as unknown rather than inventing consensus.

## Review and stage results

Run `result` with the common arguments, `--expected-panel`, both primary
submission digests and (if present) `--expected-adjudication`. This rechecks
source bytes, packet scope and all declared-reader submissions and prints
resolution counts, primary agreement, kappa and the native receipt/bundle
digests. A present adjudicator submission cannot be silently ignored.
Independently approve the resulting receipt digest. Run `stage` with those
arguments and `--expected-receipt RECEIPT_DIGEST` to exclusively create
reference-rows.json and reference-panel-package.json. Existing reference or
collection metadata files are never overwritten. Handled partial writes are
rolled back; process interruption can leave partial files and requires
supervised reconciliation.

For the coordinated workbench, use the reference receipt digest as the
reference pointer in collection metadata. The `link` Python API additionally
replays the panel and both completed prediction jobs at their retained heads,
compares staged reference rows and all packages exactly, and exclusively
stages collection.json. This API takes the active trusted setup/resume child
and the retained panel, primary, optional adjudication, receipt and both job
heads, plus the administrative as-of date. It does not expose a new browser
route or replace the separate collection preview/approval. Complete the
remaining adverse-outcome and population count files, seal, run, replay and
export using the existing assessment browser. Source verification here is at
handoff time; later report replay binds staged values rather than reopening
live source files or job databases. Preserve all approved inputs, submissions,
packages, databases and checkpoints in restricted storage for future audit.

This is a trusted coordinator workflow, not an authenticated reviewer service,
professional credential check, proof of external blinding, proof of prospective
chronology, de-identification tool or clinical validation. No clinical deployment
or publication authorization is supplied. All original entrypoints remain
available. Full repository integration CI is required.

    python -m pytest -q tests_unit/test_predictive_reference_handoff.py
