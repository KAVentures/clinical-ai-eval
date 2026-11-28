# Local browser research evidence dossier

Start the existing local assessment workflow with a research dossier screen:

    python -m caeval.assessment_dossier_web --root /private/workspaces --staging-root /private/staging

Use the separate private roots, owner-only permissions and local session
credentials described in ASSESSMENT_WORKSPACE_WEB.md. The new entrypoint
inherits the workspace selector, protocol and CSV import, prediction job and
background worker routes. The dossier route is available from the workspace
selector after the assessment plan is frozen.

## Freeze evidence completeness criteria

Before sealing a collection, open Research evidence dossier and enter the
independently approved baseline, candidate and reference coverage minima and
assisted-trial minimum, in integer basis points (0–10000). Declare whether the
assisted trial is required. These are administrative completeness criteria,
not clinical performance thresholds; no defaults are supplied. Choose Preview
policy, review the scope and retain its digest independently. Re-enter that
approval digest and choose Freeze exact approved policy. The action replays
the assessment plan and procurement checkpoint and writes a new policy file
exclusively in the workspace. It does not modify the assessment plan or vendor
history. Existing policy files cannot be overwritten. The browser refuses new
policy freezes after collection files exist, including partial collections.
This does not independently establish that outcomes were never seen beforehand.

## Generate, verify and download

Complete and seal the assessment through the existing workflow, retaining the
assessment report digest and procurement head separately. Reopen the dossier
page and supply the retained policy, procurement and report digests. To include
an approved assisted trial, place assisted-trial-package.json in the matching
private staging directory and supply its independently retained package digest.
If the file exists, it cannot be ignored; if a digest is supplied without a
file, the action is refused. The underlying dossier engine requires the exact
same native reference bundle and predictive plan. No arbitrary path or upload
is exposed in the browser.

Preview dossier replays the source assessment and optional trial, displays a
restricted aggregate evidence summary and prints the dossier digest. It does
not save a dossier. Retain the approved digest and enter it in the approval
field before sealing. Seal exact approved dossier replays all inputs and writes
research-dossier.json exclusively to the completed workspace. Verify sealed
dossier and Verify and download JSON both recompute the full dossier and check
its exact saved content against the retained digest; changed files, missing
inputs, stale checkpoints and mismatched approvals are refused. No GET request
performs a write, and the download is a restricted JSON attachment. Retain the
policy, report, dossier and optional trial checkpoints outside the workspace.
Reopening a completed workspace requires the original assessment checkpoints;
the dossier actions require their own separately retained digests as well.

The browser cannot create a policy for an already completed workspace. For
legitimate retrospective administrative reporting, the separate CLI still
exists; do not describe retrospective criteria as prospective. The workbench
is a trusted single-operator loopback application. It refuses dossier actions
while a background worker is active, enforces the existing session credential,
exact host, origin and peer, CSRF, bounded form and no-cache restrictions, and
does not introduce new institutional accounts or shared-file authorization.
The summary excludes raw source records and unit-level review rows but may
still contain sensitive aggregates. Do not treat it as a public release or a
purchasing, deployment or clinical authorization. It is not integrated into
the institutional committee database. Full repository integration CI and
clinical validation remain separate.

    python -m pytest -q tests_unit/test_assessment_dossier_web.py
