# Local assessment setup browser

This standalone trusted-operator workflow provisions the three existing local
workbenches and connects them to the assessment review browser. It replaces
individual provisioning commands, not clinical study preparation, institutional
identity verification or review. No clinical decision or deployment approval is
made. Only run it on a private approved workstation.

## Launch

Create a private staging directory and a private parent directory for a new
workspace. On POSIX both must be owned by the operator and mode 0700 (or more
restrictive); each staged file must be owner-only, for example 0600. On Windows
use appropriate restricted ACLs. The workspace destination must not exist.
Use resolved directory paths, not symlinks. Start once from a private terminal:

    python -m caeval.assessment_setup_web --staging /private/staging --destination /private/new-workspace

Open the printed loopback address and authenticate using username `operator`
and the random session password printed in the terminal. Do not expose the port,
forward it or capture credentials in logs. The browser has no arbitrary path
selection or file upload endpoint. Use your OS file manager or approved export
software to place the named local files in staging. No other files are read by
setup actions. Inputs must be pseudonymous, without raw patient records or
credentials. Use the existing module manuals for JSON schemas and scientific
assumptions. This workflow does not itself generate or collect model predictions.

## Step 1: freeze the prospective plan

Before inspecting any outcomes, stage these six JSON files:

| File | Contents |
|---|---|
| request-spec.json | Buyer-defined evidence request, VENDOR_SUBMISSION.md |
| actors.json | requester, reviewer, representative (distinct declared role tokens) |
| predictive-spec.json | Predictive research plan, PREDICTIVE_RESEARCH.md |
| binary-spec.json | Binary research plan, PROSPECTIVE_RESEARCH_WORKBENCH.md |
| manifest.json | Dataset partition manifest, DATASET_INTEGRITY.md |
| memberships.json | Exact test roster to subgroup mapping |

Select **Preview prospective plan**. The browser validates the holdout, versions,
cohort and both research plans and displays the planned scope, thresholds and
assessment digest. It generates random local audit and site keys. No outcomes
are read by this action. Select **Confirm and freeze** after review. The exact
preview digest and staged values must still match. The new private workspace
contains the request, plans, manifest, keys and initialized procurement DB.
Retain the assessment digest, request digest, procurement root and current head
in a separately controlled record. Keep the keys private. The audit cannot prove
source completeness, independence or that the freeze occurred before exposure.

## Step 2: evidence and remediation

Authenticate the vendor and apply institutional quarantine/scanning before
placing an archive named submission.zip in staging. Select **Import staged
vendor submission**. The existing workbench verifies the request binding and
records the opaque bytes without extracting or interpreting them. For a question,
response or review, stage clarification.json (event body from
VENDOR_CLARIFICATION.md) and clarification.bin (the exact evidence bytes).
Select **Record staged clarification**. Review binds the response digest;
only declared role transitions are accepted. Actual account authentication is
an external trusted-operator responsibility. Each successful write advances the
procurement head; retain the new head independently. Repeated archive bytes and
invalid or stale events are rejected. No documentary coverage or administrative
resolution establishes clinical evidence sufficiency.

## Step 3: seal the collection

After the prospective freeze and approved collection, stage:

| File | Contents |
|---|---|
| collection.json | as_of ISO date and source_receipts mapping baseline/candidate/reference to independently verified SHA-256 pointers |
| baseline-rows.json, candidate-rows.json | Prediction rows from PREDICTIVE_RESEARCH.md |
| reference-rows.json | Reference outcome rows from that manual |
| outcomes.json | Paired binary adverse outcomes, PROSPECTIVE_RESEARCH_WORKBENCH.md |
| reference-counts.json, target-counts.json | Development/holdout counts, POPULATION_SHIFT.md |

Select **Validate staged collection**. All three workbenches run against the
frozen cohort. The preview displays separate input bundle digests and the
report digest. Review and retain them independently. Select **Confirm and save
collection** to save the sealed inputs, counts, and operator config. The
confirmation is bound to the exact preview report and staged data. A change to
the collection or procurement head requires a new preview. Existing files are
never overwritten. The CLI-compatible configuration uses absolute paths in the
new workspace. Source receipt digests are provenance pointers, not independent
source verification. The local site receipts are not independent attestations.

Open the **assessment review dashboard** link. Use Run assessment, Replay and
verify, then Download verified JSON. The existing browser verifies again at
export time. Outputs are restricted and not automatically publication-safe.
Once the collection has been committed, this setup session no longer allows
procurement mutations. To change evidence or run a new collection, use the
existing governed CLI/API and a new approved configuration or workspace.

## Operational limitations

This setup console is intentionally single-session and single-operator. There
is no resume wizard. If the process ends after freezing, use the persisted
workspace and separately retained checkpoints with the existing CLI/API to
continue; once config.json is present, the existing assessment review browser
can be launched directly. A crash during multi-file provisioning may leave a
partial workspace. Do not delete it automatically or bypass checkpoint checks;
reconcile against the independently retained record under local supervision.
Handled errors roll back newly created files without overwriting existing ones.
The parent directory is assumed to be trusted and inaccessible to other users;
local malicious processes and administrators are outside the trust boundary.

The server uses the same loopback HTTP trust boundary and restrictions as the
assessment review browser: exact host, local peer, random session credential,
CSRF validation, cross-origin rejection, escaped HTML, restricted content policy,
no arbitrary upload routes and no caching. Preview confirmation is tied to the
exact plan/report digest. Staged JSON is bounded to 16 MiB per file and combined
canonical inventory; vendor archive and clarification limits are inherited.
There is no multi-user SSO, TLS, background service, automatic source ingestion,
malware detection, privacy guarantee or clinical validation. Browser downloads
must be saved in an approved restricted location. Full institutional browser
integration and governance remain separate work.

Run synthetic tests:

    python -m pytest -q tests_unit/test_assessment_setup_web.py

The tests exercise the real underlying modules and HTTP sequence, stale preview
and collection checks, tampering, private file boundaries, evidence import,
clarification and report replay.
