# No client installation: HTTP access for existing coding agents

An approved Claude Code, Codex or other HTTP-capable agent can use a hospital's
already deployed evaluator service without installing Clinical AI Eval on the
agent host. A server-side installation and an independently prepared assessment
are still required. This is a restricted HTTPS JSON interface, not a hosted
MCP server, autonomous model runner or public evaluation SaaS.

## Administrator setup

Install and validate the approved release on a hospital-controlled host. Prepare
an assessment and issue a short-lived scoped machine grant using the existing
procedure in EVALUATOR_REQUEST_API.md. Store the bearer credential outside the
workspace in approved secret management. Do not copy it into chat, repository
files, command arguments, URLs or terminal logs.

Start the separate metadata-only agent gateway on the approved evaluator host:

    python -m caeval.evaluator_agent_gateway --workspace /private/institution --origin https://eval.hospital.example --port 8772

The example hostname is a placeholder, not a deployed service. The workspace
must be an existing private directory owned by the service operator. Configure
approved HTTPS ingress for this exact origin with a valid certificate, client
access restrictions, rate and request-size limits, timeouts, service supervision
and appropriate network isolation. Forward only to 127.0.0.1:8772, preserve the
exact configured Host header, and do not rewrite paths or forward cookies.
The gateway requires a local proxy peer; it does not trust forwarded addresses
or use them as authentication. Never expose the reference WSGI server directly
to the network. Do not route the browser interface or the full evaluator API
through this agent origin. Avoid logging authorization headers and request bodies.
Proxy configuration and TLS must be independently tested on the actual host.

## Agent quick start

Give the approved agent the HTTPS origin and these instructions, not the bearer
credential itself:

    Read GET /agent-contract at the supplied approved origin. Use only its listed
    operations with the scoped credential provisioned by IT. Discover the granted
    assessment, list existing requests, and, if authorized by the operator, submit
    one stable idempotency key to request local approval. Report the returned
    status; do not claim an evaluation ran until completed. Retrieve only the
    completed metadata receipt. Never send patient records or model credentials.

The contract is static and contains no workspace information. The actual API
operations require the scoped bearer grant. If curl is already available, the
agent or a trusted script can use it without downloading a client library.
For example (POSIX shell, with an approved owner-only header file provisioned by
IT containing a single Authorization: Bearer header):

    BASE=https://eval.hospital.example
    curl --fail --silent --show-error --proto '=https' --max-time 30 "$BASE/agent-contract"
    curl --fail --silent --show-error --proto '=https' --max-time 30 -H @/approved/agent-header "$BASE/v1/assessment"
    curl --fail --silent --show-error --proto '=https' --max-time 30 -H @/approved/agent-header "$BASE/v1/evaluation-requests"
    curl --fail --silent --show-error --proto '=https' --max-time 30 -H @/approved/agent-header -H 'Content-Type: application/json' --data '{"idempotency_key":"approved-request-0001"}' "$BASE/v1/evaluation-requests"

Use the returned request ID to GET /v1/evaluation-requests/REQUEST_ID and,
only when completed, /v1/evaluation-requests/REQUEST_ID/receipt. Do not enable
redirect following, verbose credential tracing or certificate bypass. Verify
HTTP success and the ok/result JSON envelope; a request is not an execution.
Use the native local approval, queue and model-call authorization process.
Credential files must be outside the agent prompt/context and read only by an
approved execution wrapper or agent host; ordinary shell access can still expose
them. A cloud agent may send tool outputs to its provider. Apply the hospital's
approved client, data-handling and egress policy even for aggregate metadata.

This gateway delegates the exact five permitted operations to the existing
scoped API and retains its grant expiry, revocation, audit, idempotency and job
approval rules. It refuses raw archive download, browser access, arbitrary
routes and execution. There is no remote MCP handshake; a client that supports
only MCP needs an independently approved bridge. The existing local stdio MCP
client is unchanged. For clients without any approved HTTP or shell access,
this integration is not available until their environment supports it.

The gateway tests exercise route and request boundaries against a stub delegate
and real loopback HTTP. They do not prove real proxy security, actual grants,
clinical performance or deployment readiness. Validate the native evaluator API,
proxy and approved agent end to end using synthetic evidence before real use.

    python -m pytest -q tests_unit/test_evaluator_agent_gateway.py
