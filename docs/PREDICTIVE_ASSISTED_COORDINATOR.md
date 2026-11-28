# Local assisted-trial coordinator

This research workflow connects the randomized clinician-plus-AI trial, its
private clinician decision browser and the independent reference outcome panel.
It is not a clinical trial management system, an authenticated reviewer service
or a tool for deciding treatment. All tests use synthetic material.

## Prepare a prospective design

Use the unfinished predictive study's approved private staging directory and
already frozen independent reference panel (PREDICTIVE_REFERENCE_HANDOFF.md).
Prepare the following independently screened, pseudonymous materials:

- `decision-protocol.txt`: exact UTF-8 protocol describing decision-time
  information, the action and rules for missing or uncertain evidence.
- `assistance-protocol.txt`: exact UTF-8 protocol governing AI assistance.
- `decisions.json`: mapping of every planned unit token to its decision-time text.
- `assistance.json`: mapping of every planned unit token to the version-specific
  assistance to be used if assigned to the assisted arm.

Exclude future reference outcomes, other readers' decisions and control-case
assistance from distributed materials. The coordinator is trusted and has
access to the complete source inventory. Digests do not demonstrate clinical
source validity, leakage prevention, model identity, or appropriate assistance.
Each text and protocol is limited to 64 KiB; each source JSON is limited to
16 MiB. On POSIX, use owner-only files and directories; enforce approved ACLs
on other platforms. Do not include raw identifiable records or credentials.

Prepare a settings JSON with the exact fields `clinicians`, `cluster_by_unit`,
`n_assisted`, and `randomization_test`. Cluster mapping covers the entire frozen
case roster; assign repeated encounters for a patient to the same cluster.
The clinician roster must be distinct from the reference panel actors. See
PREDICTIVE_ASSISTED_TRIAL.md for restrictions and statistical assumptions.

All commands except offline `verify` take:

    --directory /private/staging --plan /private/predictive-plan.json
    --expected-plan PLAN_DIGEST --expected-panel PANEL_DIGEST

Run `python -m caeval.predictive_assisted_coordinator preview` with those
arguments plus `--settings /private/settings.json`. The coordinator derives
source and protocol digests and prints the design. Independently approve and
retain its digest before `freeze` with the same arguments and
`--expected-design DESIGN_DIGEST`. This writes `assisted-design.json`
exclusively. Freeze before any comparator decisions or allocation exposure.

## One approved allocation and private reader packets

Run `draw` with the common arguments, `--expected-design DESIGN_DIGEST` and
`--confirm-single-draw`. The allocation slot is created exclusively before
calling the uniform allocation engine, and the slot and completed contents
are flushed to disk. A second draw in the same directory is refused, even if
an interruption left the slot empty or partial. Never delete or replace an
uncertain allocation to rerandomize: report the protocol deviation and obtain
independent study oversight. Retain the successful allocation digest separately.
This local reservation guards the normal workflow, not an operator controlling
all files, copied directories, storage failures or external allocation concealment.

For each declared clinician run `export` with the common arguments,
`--expected-design`, `--expected-allocation ALLOCATION_DIGEST`,
`--clinician CLINICIAN_TOKEN` and `--output-root /private/trial-readers`.
The separate, existing private output root must not be nested within staging
or vice versa. Each exclusive reader directory contains only that clinician's
packet and approved decision-time sources plus assisted-arm material, never
reference outcomes or other readers' decisions. Distribute individual folders
through a restricted institutional handoff; do not share their parent or the
coordinator directory. Retain packet digests independently. Use the private
reader browser in PREDICTIVE_ASSISTED_READER.md to collect decisions and return
only the sealed submission. Copy each submission to staging under
`trial-reader-CLINICIAN_TOKEN-submission.json`.

## Seal and replay the research report

After independent reference review and adjudication, prepare an independently
retained checkpoints JSON with exactly these fields:

- `expected_primary`: mapping of `reader_a` and `reader_b` to reference submission digests.
- `expected_adjudication`: the third-reader digest or null if no submission exists.
- `expected_reference`: the retained reference receipt digest.
- `expected_submissions`: exact mapping of every trial clinician to their submission digest.
- `randomization_assumptions_declared`: explicit boolean; true only after independent
  review of allocation, interference, missingness and design assumptions.

Run `result` with the common arguments, `--expected-design`,
`--expected-allocation`, and `--checkpoints /private/checkpoints.json`. The
coordinator rechecks trial source bytes, allocation, reference source bytes,
all submitted decisions, reference labels and retained digests. It returns a
report and package digest. Missing decisions remain unknown; the optional
sharp-null test is withheld unless its prespecified conditions hold.
Independently approve the digest and run `seal` with those arguments plus
`--expected-package PACKAGE_DIGEST` to create `assisted-trial-package.json`
exclusively. The package contains native plans, allocation, sealed submissions,
checkpoints and report, but no raw source text. It still includes sensitive
unit tokens, decisions and reference labels: keep it in restricted storage.

    python -m caeval.predictive_assisted_coordinator verify --package /private/assisted-trial-package.json --expected-package PACKAGE_DIGEST

Offline verification checks the pinned package and recomputes the trial report
from native inputs. It does not reopen original source files or authenticate
clinicians, true chronology, allocation concealment, lack of interference or
clinical validity. Keep the approved source records, checkpoints and design
under independent custody. The package is a separate research artifact, not
an automatic procurement approval or a publication-ready claim. No new browser
coordinator, centralized permissions, or integration into the institution
committee dossier is supplied. Full repository integration CI is required.

    python -m pytest -q tests_unit/test_predictive_assisted_coordinator.py
