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
