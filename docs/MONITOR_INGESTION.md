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
