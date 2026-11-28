# Durable local predictive batch scheduling

This standalone scheduler persists a time-windowed approval for an existing
local predictive execution job. It uses the original job engine; it does not
replace the browser worker or start a service automatically. Use an approved
private workstation, local endpoint, frozen study, feature inventory and job
as described in PREDICTIVE_LOCAL_EXECUTION.md and PREDICTIVE_EXECUTION_JOB.md.
No external host, credentials or raw patient records should be supplied.

## Initialize and schedule

All scheduler commands take these common arguments:

    --queue /private/schedule.db --db /private/job.db --plan plan.json
    --expected-plan PLAN_DIGEST --execution execution.json
    --expected-execution EXECUTION_DIGEST --rows features.json

Run `python -m caeval.predictive_schedule init` with these arguments and
`--expected-job-head JOB_HEAD`. The queue database is created exclusively in
an approved private directory. Retain the printed queue head independently.
Use exactly one queue per approved job; do not create competing queues or
jobs for the same execution. The queue binds the execution digest, not a
unique operating-system process identity or hardware identity.

Every subsequent command requires `--expected-head QUEUE_HEAD`. Approve a
batch with the `schedule` command and the additional arguments:

    --expected-job-head JOB_HEAD --not-before 2026-01-01T11:00:00Z
    --expires 2026-01-01T12:00:00Z --max-steps 100 --confirm-loopback

The timestamps above are syntax examples. Use actual approved future expiry
and UTC timestamps with second precision. The start must precede expiry.
The original job must have no pending claim and must not be finished. Each
queue permits one outstanding ticket at a time; successive tickets can be
approved after completion, cancellation or reconciliation. A ticket binds
its exact starting job head and authorizes at most 1–100 new steps, subject
to the original cumulative job budgets. Retain the new queue head.

## Dispatch and monitor

- `status` replays the queue at its exact retained head, with ticket, state,
  terminal job head and recorded dispatch steps. It makes no model calls.
- `dispatch --confirm-loopback` checks the UTC window and exact job head,
  commits a queue claim, and executes the approved bounded batch using the
  durable job engine. It refuses an early, expired or stale ticket. It stops
  starting new steps at expiry and finalizes an exhausted job as appropriate.
  A step already starting or in flight can exceed the window; this is not a
  hard cutoff. The local system clock must be trusted and synchronized.
- `cancel` closes a queued ticket without inference (including expired ones).
  A claimed ticket cannot be cancelled by this command.

Dispatch is synchronous and must be invoked explicitly by an authorized local
operator or separately governed scheduler. There is no cron installation,
background daemon, multi-job queue, automatic dispatch or automatic resume.
Do not run competing CLI or browser workers. Retain the returned queue and
job heads before approving further work. A completed ticket does not imply a
completed study; use the original job status, finish and export commands.
The queue records no feature values or raw model responses.

## Restart and reconcile

Queue and job events are persisted separately. If a dispatch stops before its
terminal queue event commits, the ticket remains running, even if some or all
job results have committed. It is never dispatched again. After stopping the
worker, use `inspect` with the last retained queue head to verify ancestry and
view the newer queue state; this is not independent authentication of a newer
head. Reconcile against authorized records and retain the accepted claim head.
Inspect the original job separately using its starting or last retained head.
If a job claim is pending, follow the job's explicit stopped-worker recovery
procedure to mark that attempt unknown. Never retry an uncertain call.

Use `reconcile --expected-job-head JOB_HEAD --confirm-worker-stopped` with the
reconciled queue claim head. It checks that the current job history descends
from the approved starting job head and has no pending claim. It records a
terminal reconciled ticket with unknown dispatch step count, not a fabricated
successful run. Reapproval of a new ticket requires the newly retained job
and queue heads. No new model calls occur during reconciliation. If a prior
worker was not actually stopped, late queue completion is refused by its
stale queue head; the operator must still supervise worker shutdown.

The queue uses an append-only digest chain with at most 600 events. It reuses
the private SQLite file checks and transaction boundaries of the workbench.
Cross-database updates and external checkpoint custody are not atomic.
Do not blindly trust discovered heads or bypass reconciliation. Preserve
approved input files, job and queue databases and independent checkpoints in
restricted storage with consistent backups. No authenticated hospital user,
independent model identity verification, clinical validity or deployment
approval is claimed. Full repository integration CI is required.

    python -m pytest -q tests_unit/test_predictive_schedule.py
