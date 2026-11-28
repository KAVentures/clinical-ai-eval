# Local browser predictive job control

Launch the integrated assessment workspace browser:

    python -m caeval.predictive_job_web --root /private/workspaces --staging-root /private/staging

Use the owner-only directories and loopback session credentials described in
ASSESSMENT_WORKSPACE_WEB.md. Create or resume an assessment and freeze its
prospective plan. Model execution is available only in the frozen, unfinished
workspace. Do not send raw patient data or credentials. Verify the approved
local model service and feature protocol independently.

## Run baseline and candidate jobs

For each role (baseline or candidate), put two files in the workspace's private
staging directory: ROLE-features.json and ROLE-execution-options.json. Use the
formats and exact eligible roster defined in PREDICTIVE_LOCAL_EXECUTION.md.
The options role must match the selected browser page. Keep those files
unchanged until all job evidence has been exported. The workspace selector
provides links to both role-specific job pages. Opening a page previews the
execution scope and digest without making any model calls. Review the scope
and independently retain the approved execution digest.

Enter the approved execution digest and choose Create job with an empty head.
One fixed database per role is created exclusively in the private workspace;
no overwrite or arbitrary database selection is available. Retain the returned
head independently. All subsequent actions require the approved execution digest
and the retained current job head.

- Verify progress replays the job history and shows counts and recorded units.
- Approve up to 3 model calls runs a synchronous bounded batch, respecting the
  job's existing cumulative call and soft elapsed-attempt budget. It may also
  finalize an exhausted job without a call. No automatic background task or
  retry is started. The browser remains busy while the batch runs.
- Finish job records a terminal state without additional inference.
- Inspect newer history verifies ancestry from a retained head; a newer head
  is not automatically approved. Reconcile it independently before adoption.
- Confirm worker stopped marks a pending ambiguous attempt unknown. Only use
  after independently confirming the worker stopped. Never retry an uncertain
  attempt. See PREDICTIVE_EXECUTION_JOB.md for recovery and budget limitations.

If a request is interrupted or a response is lost, inspect and reconcile the
job before continuing. Retain each displayed head independently. Reopening
an unfinished workspace uses the existing procurement/plan resume checkpoints;
job heads are separate and must still be provided. No background concurrency
or hard runtime cutoff is added.

## Stage predictions and link collection metadata

After finishing a job, Stage sealed predictions and receipt replays it at the
retained head and writes ROLE-rows.json and ROLE-execution-package.json to the
private staging directory. Both are exclusive new files. Existing files are
never overwritten. The package retains the native prediction bundle and job
receipt. A handled write failure removes only files created by that action;
process interruption is not crash-atomic. Retain receipt and bundle digests.

If both roles were executed this way, choose Link completed prediction jobs.
Supply both independently retained final job heads, the administrative as-of
ISO date, and an independently verified reference receipt SHA-256 pointer.
The action replays both jobs, compares the staged packages and prediction rows
exactly, and exclusively writes collection.json with the two job receipt
pointers and the supplied reference pointer. It does not collect or verify
reference outcomes. For a manually supplied prediction arm, prepare collection
metadata with the appropriate independently verified receipt pointers instead.

Stage the remaining reference rows, adverse outcomes and population counts as
specified in ASSESSMENT_SETUP_WEB.md. Perform the existing collection preview,
confirmation, assessment, replay and verified download. The job handoff checks
its sources at staging/link time; the existing collection seal subsequently
binds the staged values, not the live job database. Keep the approved job
packages, databases, feature inputs and independent checkpoints for later audit.
Do not modify staged files between approval and collection confirmation.

The local single-operator trust boundary is unchanged. Host, peer, session
credential, origin, CSRF, bounded form, escaping and no-cache restrictions
apply. No source identity attestation, authenticated hospital users, independent
reference adjudication, automated feature acquisition, clinical validation or
deployment authorization is provided. All existing CLI and browser entrypoints
remain available.

    python -m pytest -q tests_unit/test_predictive_job_web.py
