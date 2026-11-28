# Scoped local monitoring ingestion and alert polling

The aggregate lifecycle-monitoring module can receive structured reports and
incident metadata from authorized hospital collectors using a separate
loopback-only HTTP service. This service is optional. It cannot authorize
deployment, execute clinical models, initiate reassessment, pause or roll back
a deployment, or send external notifications.

## Start the service and obtain the contract

Run the service on the approved institutional host as the trusted application
OS account, pointing to the same institution directory as the browser
application:

```sh
clinical-ai-eval serve-monitor-ingestion /private/institution --port 8772
clinical-ai-eval monitor-ingestion-openapi > monitoring-api.json
```

The listener binds only 127.0.0.1. The exact Host header is required. Browser
cookies, Origin headers and query strings are refused; this is not a route
around institutional browser identity or CSRF checks. No CORS, remote bind,
TLS, network egress or remote hospital connector is included. Do not publish
this reference listener to a network or untrusted host. A production
integration needs institution-managed network security and authentication,
capacity testing and operational validation. The OpenAPI document describes
the structural API; the underlying monitor enforces additional semantic and
byte limits.

## Grants

In the institutional browser, an administrator opens Lifecycle monitoring →
Manage local ingestion capabilities. Choose the exact monitor, an active
operator account, and any subset of report, incident and check scopes.
Explicitly confirm issuance. Copy the one-time random bearer token into an
approved local credential store. Only the token hash is persisted; the
management screen does not redisplay the token. Do not put secrets in URLs,
shell history, logs or exported monitoring evidence.

Grants expire after 60–86,400 seconds, are revocable, and are invalid once
the assigned operator is disabled or no longer an operator. One grant
cannot write to another monitor, perform escalation actions or read raw
records. The admin browser remains the only issuance/revocation workflow.
The service allows up to 200 historical grants and 5,000 distinct request
receipts per institutional store, with up to 300 distinct ingestion requests
per grant. There is no automatic grant renewal or history pruning.
Plan credential rotation and storage capacity before production use.
The polling scope is read-only and does not consume the ingestion quota.
The reference listener has no production request rate limiter.

## Ingest aggregate reports and incidents

POST /v1/monitor/ingest with Content-Type: application/json and
Authorization: Bearer <approved secret>.

A request is an explicitly confirmed batch of 1–12 events, not a patient
record. For example (fictional metadata and digest placeholders):

```json
{
  "schema":"lifecycle-ingestion/1",
  "request_id":"collector-window-001",
  "confirmed":true,
  "events":[
    {
      "kind":"report",
      "document":{
        "plan_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "target":{"name":"Fictional product","version":"v1"},
        "window_index":0,
        "metrics":[{"id":"fictional-rate","numerator":1,"denominator":100}],
        "source_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "complete_aggregate_attested":true
      }
    }
  ]
}
```

Use the actual frozen monitor's complete metric inventory, exact target,
plan digest and source digest. Incident documents use the existing
structured lifecycle incident schema. No actions or unstructured patient
information are accepted. A request is limited to 128 KiB. Duplicate JSON
keys, nonfinite numbers, invalid counts, unfinished windows and incompatible
version reports are refused. Batch items are processed in input order; a
version-change event may block a subsequent overlapping report.

The transaction locks every event and an audit-linked receipt, or rolls
everything back. The same grant and request ID with exactly the same
canonical request returns the original receipt while the grant remains
valid, without consuming another use. A different request under the same
ID is refused. Repeated native events with identical content retain their
original record. New grant IDs are not global request-id deduplication;
native window/incident immutability still applies. Receipts contain event
digests in input order and identify the grant and monitor, not proof of
the external source or actual deployed version.

The local audit inventory binds grant issuance, revocation, owner and
request receipt to its monitor events. It detects local inconsistency,
not privileged database/audit history rewriting. The API does not verify
the semantics or authenticity of externally attested aggregate counts.

## Scheduled checks and notification integration

GET /v1/monitor/check with a valid check-scoped bearer grant. This returns
only a monitor ID, plan digest, UTC as-of date, opaque stable alert IDs,
alert kind and severity, and attention flag. Raw counts, incident IDs,
source digests, external artifacts and reviewer rationale are not returned.
The feed includes active native monitoring alerts, stale scope and ended
schedule conditions. Threshold alerts remain active under the native
monitor rules; an external resolution record is not independent proof of
safety.

An institution-managed cron job, systemd timer or other approved scheduler
can poll this endpoint, compare the opaque IDs to a retained local set,
and deliver alerts using the hospital's own approved notification system.
Keep the credential in a restricted credential store and supply it through
the HTTP Authorization header. Treat failures, expiration, revoked grants
and unavailable service as monitoring failures, never as an empty or
healthy feed. This release does not install a scheduler, persist external
delivery state, send notifications or guarantee delivery. The existing
institutional escalation inbox also shows aggregate-monitor alerts for
administrators; acknowledgment in that inbox does not resolve source
conditions. Operators continue to use the native monitoring view.

Scheduled native clinical regression evaluations retain their separate
existing approved runner and controls. The ingestion listener cannot
execute them. All outputs are administrative monitoring evidence, not
clinical validation, safety certification or deployment authorization.


## Replayable alert transitions

For institution-managed notification delivery, the offline
`monitor-alert-outbox` commands compare a successful feed with the last
acknowledged checkpoint. They produce a deterministic pending batch and
detect newly reported alerts, severity changes and alerts no longer reported.
A disappeared alert is not a clinical resolution or proof of safety.

First retain the output of a successful authenticated feed request in a
restricted file. On the first run, omit `--previous`; on later runs:

```sh
clinical-ai-eval monitor-alert-outbox prepare current-feed.json --previous checkpoint.json > pending.json
clinical-ai-eval monitor-alert-outbox verify pending.json
```

Use an institution-managed single-writer scheduler and private directory.
The scheduler must atomically persist the pending file before delivery,
retain it unchanged through failures, and deliver its event inventory
using an approved notification system. Use the batch digest as the
delivery idempotency key wherever the destination supports it. Do not
overwrite an unacknowledged pending batch with a later poll; process it
first. The commands deliberately do not install the scheduler, contact a
notification provider or manage filesystem locking and durable writes.

When delivery of the exact event batch has been acknowledged externally,
advance the checkpoint using its verified batch digest:

```sh
clinical-ai-eval monitor-alert-outbox acknowledge pending.json --expected-digest BATCH_DIGEST --confirm-delivered > next-checkpoint.json
```

Atomically replace the previous checkpoint with the resulting checkpoint
before retiring the pending file. An event-free batch may be acknowledged
without `--confirm-delivered`. A failed HTTP poll or invalid feed must
not be substituted by an empty list of alerts. Treat it as a separate
monitoring failure. This contract rejects changed monitor/plan identity,
backward dates, duplicate IDs, changed kinds under the same ID and
inconsistent attention flags. Same-day feed order, actual source authenticity,
delivery receipts and filesystem durability are not independently verified;
a digest is not a signature. No distributed exactly-once delivery is claimed.


## Persistent transactional outbox (preferred for schedulers)

The optional local SQLite store removes the need to implement atomic
checkpoint and pending-file replacement in scheduler code. Initialize
exactly one store per monitor and frozen plan in an operator-controlled
private directory on a local filesystem:

```sh
clinical-ai-eval monitor-alert-store initialize /private/alerts.sqlite3 MONITOR_ID PLAN_DIGEST
clinical-ai-eval monitor-alert-store status /private/alerts.sqlite3
```

Initialization is exclusive; an existing or interrupted store is never
automatically overwritten or reset. Use a regular non-symlink file with
private permissions and trusted directory ancestors. SQLite DELETE
journaling, FULL synchronous mode and immediate transactions serialize
state updates. The filesystem and OS must honor SQLite locking and
durability guarantees; network filesystems and untrusted shared directories
are not supported.

Before polling, inspect status. If a pending attempt exists, resume it
unchanged instead of preparing a later feed. After a successful scoped
feed poll:

```sh
clinical-ai-eval monitor-alert-store enqueue /private/alerts.sqlite3 current-feed.json
```

This persists the exact attempt before returning it. Repeating the same
feed while pending returns the same attempt; a different feed cannot
overwrite it. Use the outer attempt digest (not the nested batch digest)
as the external delivery idempotency key. A random store identity and
monotonic sequence distinguish recurring identical transition batches.

After the approved notification system acknowledges the exact attempt:

```sh
clinical-ai-eval monitor-alert-store acknowledge /private/alerts.sqlite3 --expected-digest ATTEMPT_DIGEST --confirm-delivered
```

Omit the delivery flag for event-free attempts. The transaction advances
the checkpoint and clears the pending attempt together. The most recent
acknowledgment can be replayed with the same digest and attestation without
clearing a newer pending attempt. Only the current checkpoint, one pending
attempt and last acknowledgment are retained; this is not a complete
notification audit archive.

The store does not claim a delivery lease: multiple workers might retrieve
the same pending attempt. Use a single authorized delivery worker or an
idempotent external destination. A crash after external delivery but before
local acknowledgment can result in duplicate delivery; no distributed
exactly-once guarantee is provided. The scheduler and external transport
remain institution-managed. The store does not send network requests,
verify actual delivery, detect replayed same-day source feeds, or establish
clinical safety. Treat poll errors and unavailable/corrupt stores as
monitoring failures, not as empty alert feeds. Keep this operational store
in the approved backup/recovery process; an older restored copy can replay
already-delivered attempts.


## Runnable polling and local notification handoff

The alert worker now provides one full scheduler-driven cycle using the
transactional store. Configure a check-only ingestion capability for the
monitor and an independent credential for an approved local notification
relay. The relay must implement the acknowledgment contract below. Use
separate private credential files, each containing a 64-character lowercase
hex bearer token and optional final newline. On POSIX these must be owned
and protected by the authorized operator (no group/other access); use a
private directory with trusted non-symlink ancestors. Windows operators
must set appropriate filesystem ACLs separately. Never pass tokens as CLI
arguments or log them.

The worker uses literal 127.0.0.1, fixed routes and distinct ports; there
is no DNS, proxy, redirect, arbitrary URL, remote HTTP or automatic
credential renewal. The local services are trusted and must be managed by
the institution; binding the port does not authenticate the local process
or establish TLS. Preview the store-specific endpoint binding:

```sh
clinical-ai-eval monitor-alert-worker preview /private/alerts.sqlite3 --source-port 8772 --sink-port 8773 --timeout 10
clinical-ai-eval monitor-alert-worker bind /private/alerts.sqlite3 --source-port 8772 --sink-port 8773 --timeout 10 --approved-digest APPROVED_BINDING_DIGEST --confirm-local-handoff
```

The binding is immutable in that store and includes its unique identity
and exact monitor/plan scope. Resolve any already-pending attempt before
initial binding. Credential rotation does not require changing the
binding, but the destination and scope must remain the approved ones.
Do not reset or copy a store just to bypass a pending attempt.

Run one cycle using an institution-managed single-worker scheduler:

```sh
clinical-ai-eval monitor-alert-worker cycle /private/alerts.sqlite3 --approved-digest APPROVED_BINDING_DIGEST --source-secret-file /private/check-token --sink-secret-file /private/relay-token
```

Use a non-overlapping systemd timer, cron job with an approved process
lock, or equivalent. No persistent timer is installed by this command.
Configure an external process deadline: the 1–60 second socket timeout
is not a total wall-clock deadline. A nonzero exit means polling,
delivery, or acknowledgment failed; investigate it as an operational
monitoring failure. Do not substitute an empty feed for a failure.

The worker first resumes a pending attempt, if present, before polling.
Otherwise it GETs /v1/monitor/check with the source credential, validates
the feed and commits a pending attempt. It sends only the transition
events and routing metadata to the approved relay. It does not transmit
the source credential, the entire checkpoint or raw clinical evidence.

### Local relay contract

The relay must accept POST /v1/alerts/deliver with the independent
Authorization bearer credential, application/json body, and
Idempotency-Key equal to the attempt digest. The envelope has exactly
these fields:

```json
{
  "schema": "monitor-alert-delivery/1",
  "attempt_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "monitor_id": "fictional-monitor",
  "plan_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "as_of": "2026-11-25",
  "sequence": 1,
  "events": [
    {
      "transition": "newly_reported",
      "alert": {
        "id": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "kind": "threshold",
        "severity": "serious"
      }
    }
  ]
}
```

The relay should durably accept the exact envelope and idempotency key
under institution-approved controls before returning HTTP 200 with
application/json and exactly:

```json
{
  "schema": "monitor-alert-delivery-receipt/1",
  "attempt_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "accepted": true
}
```

Repeated exact keys must be idempotent, while conflicting content for a
key must be rejected. Non-200, malformed, oversized, mismatched or
negative responses leave the pending attempt intact. After a matching
acceptance response the worker advances the checkpoint transactionally.
An event-free batch advances without any relay request. A crash after
relay acceptance but before local acknowledgment can replay the same
key. The relay is responsible for its actual notification delivery,
durability, deduplication and audit evidence. The worker reports
destination acceptance attestation, not verified delivery to a clinician.

A single non-overlapping worker is required: the store serializes its
transactions but does not lock the entire network cycle. Do not run
manual store updates concurrently with worker cycles. Credential
expiration, a bad local service or a restored older store can lead to
failed or repeated handoffs. The worker does not verify source identity,
detect same-day feed ordering, independently attest actual notification
delivery, or perform clinical escalation actions. Nothing in this
workflow constitutes clinical safety or deployment authorization.
