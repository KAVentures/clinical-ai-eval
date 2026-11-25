# Institutional evaluator request API

This is the evaluator-facing counterpart to the candidate-side endpoint.
A hospital integration, procurement application, or approved agent can request
an evaluation and read the restricted status of exactly one prepared assessment.
No third party receives filesystem access or browser credentials.

## IT setup and service contract

Export the OpenAPI description:

    clinical-ai-eval evaluator-api openapi

Create the assessment through the existing browser or CLI process, then issue
a short-lived scoped machine grant from the trusted local CLI:

    clinical-ai-eval evaluator-api issue /approved/workspace --owner operator --assessment-id ASSESSMENT_DIGEST --ttl 3600 --out /approved/private/service-token --confirm

The token is written to a new owner-only file outside the evidence workspace.
It is never printed. Provision and distribute it using institutional secret
management; do not pass it in URLs, shell history or model prompts.

    clinical-ai-eval evaluator-api serve /approved/workspace --port 8771

The reference service listens on 127.0.0.1 only. For a remote integration,
hospital IT must configure an approved reverse proxy with TLS, client access
restrictions, timeouts and rate limits. Do not expose the development WSGI
server directly to untrusted networks. The service is separate from the browser
gateway and the aggregate read-only MCP broker.

## Machine client

POST /v1/evaluation-requests with Authorization: Bearer TOKEN,
Content-Type: application/json and a body like:

    {"idempotency_key":"client-request-0001"}

No caller-supplied assessment identifier, prompt, patient data, model
credentials, path or execution flag is accepted. The grant determines the
assessment. Repeat the same key for an idempotent acknowledgement. Only one
outstanding request per grant is accepted. Status starts at
awaiting_local_approval. A request does not queue or execute a job.

GET /v1/evaluation-requests/REQUEST_ID with the same bearer grant retrieves
scoped status. No cross-grant request access. A completed linked job exposes
only independently rechecked canonical package manifest identifiers, family
names, claim ceilings and counts; no raw answers or records. These are
evidence metadata, not complete replay packages or clinical authorization.

## Local approval and linkage

    clinical-ai-eval evaluator-api inventory /approved/workspace

An institutional administrator can open External evaluation requests in the
browser dashboard, review the grant owner and assessment digest, select an
existing matching unlinked job and explicitly approve the association.
The browser cannot issue bearer credentials or execute a job from this page.
An administrator can also revoke the application's grant. The same operations
are available to trusted local IT through the CLI:

    clinical-ai-eval evaluator-api link /approved/workspace --request-id REQUEST_ID --job-id JOB_ID --confirm

Linkage checks the prepared assessment identity, active grant and account.
It cannot start a job or override existing model-call approval. The same job
cannot be linked to multiple requests. Existing job execution, evidence
review and procurement processes remain authoritative.

    clinical-ai-eval evaluator-api revoke /approved/workspace --grant-id GRANT_ID

Grants expire within 24 hours, are revocable, and require the associated
account to remain enabled. Restoring a database backup must not reactivate old
tokens; the workspace restore workflow automatically revokes all restored
service grants while preserving request history. IT must issue fresh credentials
after a restore. The initial API is a bounded local
request ledger, not a multi-tenant SaaS job service. Production network
hardening, recovery automation, structured data endpoints and delegated MCP
write orchestration remain future work.

## Agent orchestration and recoverable requests

GET /v1/assessment discovers the exact granted assessment and expiry.
GET /v1/evaluation-requests lists only requests belonging to the same grant.
GET /v1/evaluation-requests/REQUEST_ID/receipt returns a digest-bound metadata
receipt only for a completed verified job. Store its digest independently;
verify the receipt offline with:

    clinical-ai-eval evaluator-api verify-receipt --file receipt.json --expected-digest DIGEST

This checks metadata integrity, not clinical validity, digital signature,
or independent scientific replay. Full canonical packages remain available
only through authorized institutional evidence workflows.

A machine or local coding agent can use the separate stdio MCP client:

    clinical-ai-eval evaluator-mcp --port 8771 --token-file /approved/private/service-token

The agent host launches this executable with the approved arguments. Tools
discover the granted assessment, list requests, request human approval with
an idempotency key, inspect status and retrieve completed metadata receipts.
No workspace filesystem access or model execution is needed by the MCP relay.
The client connects only to the fixed literal loopback port, refuses redirects
and proxies, and does not accept a caller-selected URL. Remote installations
need a separately approved secure local bridge; this client is not a hosted
MCP service. Use the existing human approval and local worker execution flow.

## Direct institutional queue approval

Administrators can now use Approve and queue assessment in the browser or:

    clinical-ai-eval evaluator-api approve-queue /approved/workspace --request-id REQUEST_ID --confirm

This creates one canonical job for the exact prepared assessment. Retrying an
interrupted approval reuses the deterministic job ID rather than creating
another job. A worker refuses a service-created job if its committed local
approval audit or request binding is missing. Service-created jobs cannot be
manually linked to a different request. The canonical worker still requires
separate authorization for model calls. Revoking an API grant stops external
access; it does not cancel an already approved canonical job. Cancel queued
jobs separately using existing institutional controls. An interrupted queue
whose grant has expired must be investigated or cancelled, not automatically
reactivated.

## Explicit release of canonical family evidence

A normal machine grant exposes metadata only. A separate local administrator
may review and release a specific completed canonical family archive, which
can contain clinical test prompts, raw outputs and review artifacts. Never
approve an archive containing confidential or unauthorized data. Preview
writes an owner-only ZIP outside the workspace and returns its exact SHA-256:

    clinical-ai-eval evaluator-api package-preview /approved/workspace --request-id REQUEST --family FAMILY --out /private/review.zip

Review the entire ZIP, then explicitly approve the exact digest for that
request and family:

    clinical-ai-eval evaluator-api package-approve /approved/workspace --request-id REQUEST --family FAMILY --expected-digest SHA256 --confirm

The same active grant may then GET
/v1/evaluation-requests/REQUEST/packages/FAMILY with its bearer credential.
The response is application/zip with X-Content-SHA256; compare the bytes
against the independently communicated approved digest. Verify offline with
clinical-ai-eval verify-package review.zip using matching scientific definitions.
The archive contains only manifest-listed artifacts and review inputs, not the
entire execution workspace. The service checks the exact bytes and approval
audit again before delivery. Changing bytes invalidates the existing approval;
use a newly authorized request for another release. Revocation/expiry blocks
delivery but cannot retrieve already downloaded copies. MCP tools remain
metadata-only. This is canonical manifest verification, not rerunning all
model calls or independent clinical validation. Explicit export approval is
not clinical deployment authorization.
