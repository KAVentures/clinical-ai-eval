# Resume an existing local assessment workspace

The resume browser restores an existing workspace created by the staged setup
browser. It is a trusted single-operator local tool, not a recovery service for
untrusted databases, institutional authentication or a clinical release gate.
It does not automatically repair or accept partial writes.

## Retain checkpoints before stopping

Keep these digests in a separately controlled record, not only in the workspace:

- Assessment plan digest from the confirmed prospective setup.
- Current procurement head (initial root, then the new head after each write).
- For a completed collection, the preview report digest from the confirmed
  collection, or the matching independently retained verified report digest.

If a checkpoint is lost or a process stops after a database commit but before
its head is recorded, stop and reconcile with the institution's authorized
backup and evidence records. Do not recover by blindly trusting a digest
calculated from the suspect workspace. Keep keys and backups private.

## Resume an unfinished collection

Use the same private staging directory (or another approved private staging
folder) and the existing workspace at its original resolved absolute path.
Only one setup or review process should operate on that workspace at a time.
Stop the prior process before starting the resume console.

    python -m caeval.assessment_resume_web --staging /private/staging --destination /private/workspace --expected-plan PLAN_DIGEST --expected-head CURRENT_HEAD

Open the printed loopback address and use username `operator` and the newly
printed session password. The tool revalidates the frozen buyer request,
research plans, manifest, cryptographic keys, and procurement event history
against the independently supplied plan and head. It never creates a new
study or regenerates the keys on resume. The operator can continue importing
vendor evidence, recording clarifications and sealing a collection using the
original setup interface. Uncommitted previews are intentionally discarded;
preview and confirm again. Continue retaining the new head after each write.

## Resume a completed collection

When the seven collection files are present, the independently retained
report digest is required in addition to the plan and head:

    python -m caeval.assessment_resume_web --staging /private/staging --destination /private/workspace --expected-plan PLAN_DIGEST --expected-head CURRENT_HEAD --expected-report REPORT_DIGEST

The tool refuses any configuration paths other than the original fixed
workspace file paths. It replays all three native workbenches and matches the
retained report digest before offering the review dashboard. Use Run
assessment, Replay and verify, then Download verified JSON. The restored
browser pins the expected report on every action; changed inputs cannot be
accepted by merely running a new report. Completed workspaces are read-only
through this interface; procurement or collection changes require the existing
governed workflow and new independently approved checkpoints. This applies
also after a resumed unfinished collection is committed.

## Refusal and recovery

A mismatched plan, stale head, corrupted source, partial collection inventory,
unsafe local file, or existing SQLite journal/WAL/SHM sidecar prevents resume.
No sidecars are removed, missing files synthesized, or checkpoints advanced
by resume. Treat these refusals as a reason for supervised reconciliation,
not a reason to bypass checks. A handled post-commit review startup failure
locks further mutations in that session; restart only with the approved
report digest and complete collection. The underlying setup multi-file
operation remains non-atomic across process crashes. This tool cannot replace
consistent SQLite snapshots, backups or independent checkpoint custody.

Private directories and file permissions are inherited from setup. All
inputs are fixed-name local files; a completed configuration is restricted
to the original absolute paths, so moving a workspace requires a separately
governed migration. Browser access uses the existing loopback security
boundary and a fresh random credential and CSRF token on each launch. No
multi-user authorization, source-record verification, clinical validation,
publication approval, TLS or remote service is supplied. Reports remain
restricted. The existing setup process and CLI/API remain available.

## Tests

    python -m pytest -q tests_unit/test_assessment_resume_web.py

Synthetic tests cover resumed procurement, collection, real HTTP replay and
download, corruption, stale checkpoints, partial collections, config path
redirection, sidecars and changed data after restart.
