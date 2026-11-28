# Local background predictive worker

Launch the integrated assessment browser with bounded background execution:

    python -m caeval.predictive_worker_web --root /private/workspaces --staging-root /private/staging

Use distinct approved owner-only directories and the printed loopback address
and session credential. Follow PREDICTIVE_JOB_WEB.md to freeze an assessment,
prepare numeric features and execution options, approve the exact execution
digest, and create the role-specific durable job. No network request is made
by opening the worker page. The underlying service must be an approved local
predictive endpoint. Numeric features may still be sensitive.

## Start, monitor and pause

From the workspace selector open Background prediction worker. Select the
role, enter the independently approved execution digest and current retained
job head, and explicitly approve 1–100 new steps. The worker starts in a
separate thread within this local browser process. Each step makes at most one
model request and observes the original job's cumulative budgets. The exact
frozen plan, feature inventory, execution digest and procurement checkpoint
are revalidated before each step. The durable job commits a claim before
calling the service and commits a result afterward; no automatic retry.
The worker finalizes the job if the planned roster or original budget is
exhausted, otherwise stops at the newly approved batch limit.

Refresh progress to see the last committed result and current worker state.
The browser does not need to wait for an in-flight model request. The worker
allows only one active batch per browser process, and while active refuses
other browser mutations, including workspace switching, synchronous execution,
recovery, and collection sealing. Pause requests are bound to the run ID and
session action token. Pause is cooperative between attempts: a request that
is already starting or in flight is not cancelled. A soft socket timeout or
attempt-time budget is not a hard runtime deadline. Wait until paused before
performing other operations. The page shows the last committed head, not an
assertion that no claim is pending.

After a pause or batch limit, independently retain the last committed head
and explicitly approve another bounded run if needed. No new run starts
without exact scope and head validation. After finish, use the existing job
page to stage predictions and receipts, link both completed roles, and seal the
collection. See PREDICTIVE_EXECUTION_JOB.md for durable job semantics.

## Restart and recovery

Job history and completed results are persisted in the existing SQLite job
files. Worker scheduling intent, progress UI and pause state are in memory
only. The application does not automatically restart jobs. On shutdown it
requests a pause and waits briefly; a process exit may interrupt an attempt.
After restarting, inspect using the last independently retained head, reconcile
any newer history and recover a pending claim only after confirming the prior
worker stopped. An ambiguous attempt is recorded unknown, never retried. A
worker error stops the batch without adopting a newer head. If source inputs
change, restore the approved inputs under supervision before inspecting.

This is not a distributed worker, system service, persistent scheduler,
parallel executor or cross-process lock. Do not launch competing local browser
or CLI workers against these workspaces. The underlying database guards the
same job's claims but cannot prevent another process creating another database.
The existing private-workstation and trusted single-operator boundary applies;
no multi-user authentication, external source or model identity verification,
clinical validation or deployment approval is supplied.

    python -m pytest -q tests_unit/test_predictive_worker_web.py
