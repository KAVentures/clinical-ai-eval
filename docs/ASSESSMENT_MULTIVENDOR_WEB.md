# Local browser multi-product comparisons

Launch the integrated local assessment application:

    python -m caeval.assessment_multivendor_web --root /private/workspaces --staging-root /private/staging --comparison-root /private/comparisons

All three existing directories must be private, distinct and non-nested.
The application retains the existing workspace, protocol, prediction worker
and research dossier routes. Use the printed loopback address and session
credential. The new comparison link is available from the workspace selector.

## Freeze comparable study conditions

Create two to five product-specific workspaces and freeze their assessment
plans and research dossier policies. Follow ASSESSMENT_MULTIVENDOR.md for
exact common study conditions and requirements. Before collection sealing,
open Matched product comparisons, give the comparison a unique name, and
select each workspace in a separate product slot. The workspace names are
also the product aliases in the comparison. Supply the independently
retained assessment plan, procurement head and dossier policy digests for
each workspace. Leave report, dossier and trial digests blank at this stage.
Leave all fields blank in unused product slots.

Preview design checks the common conditions and displays the comparison
digest without writing a file. Retain the approved digest independently;
enter it in the approval field and select Freeze approved design. A new
NAME-design.json is written exclusively in the private comparison directory.
The browser refuses a new design if a selected workspace already contains
any sealed or partially sealed collection files. This local ordering check
does not independently prove actual prospective chronology or blinding.

## Compare completed dossiers

Complete each assessment and seal its research dossier in the usual browser
workflow. Return to the comparison page, re-enter the exact comparison name,
workspace roster and retained checkpoints, including the updated procurement
heads if they changed. Enter the frozen comparison design digest. For each
completed dossier, supply the independently retained assessment report and
sealed dossier digests. If an assisted-trial package is staged for a completed
workspace, supply its retained digest; an existing package cannot be ignored.
A workspace with no sealed dossier remains in the roster with explicit
not-supplied status and no report, dossier or trial digest. The engine refuses
omitting an existing saved dossier. Never omit an unfavorable product.

Preview comparison replays each supplied assessment and saved dossier and
compares candidates only when the common reference and baseline evidence
matches exactly. It displays administrative gap counts and direct paired
Brier and net-benefit difference bounds. The direction is explicitly right
minus left, with product names in lexical order solely for enumeration. No
ranking, combined score, or clinical inference is implied. Binary adverse
outcome and assisted-trial findings remain individual dossier findings.

Independently retain the preview report digest, enter it in the approval
field and choose Seal approved report. A new NAME-report.json is written
exclusively. Verify and download replay the original source workspaces and
check the saved content against the retained digest on every operation. A
new browser session may replay the comparison by re-entering the retained
checkpoints. Use a new comparison name/design for a new report snapshot;
no overwrite, automatic revision or deletion is supported.

The browser accepts only existing immediate workspace names, not arbitrary
paths. Trial packages are read only from the fixed matching private staging
directory. Requests use the existing session credential, origin/peer/host
checks and action token; workspace switching invalidates stale forms. The
worker guard refuses comparison actions during an active prediction batch.
This is a trusted single-operator workstation, not institutional multi-user
authorization or a portable public release. Restricted aggregate reports
can still contain sensitive information. Retain all source files and
checkpoints under approved institutional custody. No clinical validation,
purchasing, deployment or publication authorization is supplied. Full
repository integration CI remains required.

    python -m pytest -q tests_unit/test_assessment_multivendor_web.py
