# Durable local predictive execution jobs

This standalone CLI/API extends the approved numeric prediction adapter with
SQLite progress, an append-only digest chain and explicit interruption recovery.
It does not replace the original one-shot adapter or create a background service.
Use an approved private local model endpoint and frozen study as described in
PREDICTIVE_LOCAL_EXECUTION.md. Do not send raw patient records or credentials.

## Start a job

Use the existing predictive_local_execution preview command and independently
retain the approved execution digest. Create one new job database per approved
execution. All job commands take the same common arguments:

    --db /private/job.db --plan plan.json --expected-plan PLAN_DIGEST
    --execution execution.json --expected-execution EXECUTION_DIGEST --rows features.json

Run `python -m caeval.predictive_execution_job init` with these arguments.
The parent directory must be private and the database must not already exist.
Retain the returned head independently. Every later command also takes
`--expected-head HEAD` using the most recently approved head. Do not create
multiple jobs for the same approval as a way to bypass the call budget.

## Execute and monitor

- `step --confirm-loopback`: make at most one model call, with an explicit
  approval and exact current head. Retain the new head before another step.
- `status`: verify the entire job event history against the supplied current
  head and show counts, pending unit, elapsed attempt time and finish state.
- `finish`: explicitly terminate with complete, budget, or operator_stop
  reason; unattempted units remain unknown. Once finished, no new calls.
  A step after the cohort or budget is exhausted also finalizes the job.
- `export --output package.json`: only after finish, reconstruct the job
  receipt and native prediction bundle without network calls. Both digests
  are printed. The package uses the job receipt schema, not the one-shot
  adapter receipt format. Use its bundle directly with predictive_research.

The call budget is cumulative across steps. The time budget counts observed
monotonic attempt duration, excluding operator pauses and local bookkeeping.
Interrupted attempts charge the configured socket timeout as a documented
accounting convention, not a measured duration. This is a soft pre-call
budget, not a hard runtime deadline; an active request may overrun it.
The operator should stop an unresponsive service using approved procedures.
No automatic retries, external host configuration or redirects are introduced.

## Interrupted execution

A claim is committed before the network call. If execution stops before the
result commits, the unit might or might not have reached the service. It must
not be retried under the same job. A competing step is refused while the
claim is pending. No SQLite transaction spans the network call.

After stopping the worker, use `inspect` with the last retained head to verify
that it is an ancestor and see the current internally consistent head.
A newer head is NOT independently authenticated by this inspection. Reconcile
with the authorized operator, execution records and backups before retaining
it; never automatically adopt the displayed head. If the current state has a
pending claim and the prior worker is definitely stopped, use `recover
--confirm-worker-stopped` with the reconciled claim head. Recovery commits an
interrupted status with null probability; it does not call the service. A late
result cannot overwrite the recovered event. Continue with the next eligible
unit or finish. If the result was already committed, reconcile the head and
continue without recovery. Do not retry an uncertain call by creating a new job.

All rows, including failed, interrupted and unattempted units, remain in the
native prediction bundle. Only successful responses carry probabilities.
The receipt distinguishes interruption, budget and operator termination.

## Trust boundary

The database stores unit tokens, statuses, probabilities, elapsed time and
hashes, not feature values or raw responses. It is still sensitive. Retain
source feature files, approved execution scope, database and checkpoints in
restricted storage with appropriate ACLs and consistent backups. A hash chain
cannot protect against a party controlling both data and retained checkpoints.
SQLite transactions guard individual claims/results but not the external act of
retaining a checkpoint. Only one authorized job per approved execution is an
operator policy, not enforced across arbitrary databases. No independent model
identity verification, automatic source ingestion, background worker, browser
controls or clinical authorization is provided. Full integration CI is required.

    python -m pytest -q tests_unit/test_predictive_execution_job.py
