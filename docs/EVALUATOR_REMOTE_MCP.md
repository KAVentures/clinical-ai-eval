# Agent-first remote MCP evaluator (pilot)

A compatible coding agent can connect by HTTPS URL without installing Python,
Clinical AI Eval, a desktop application or a local MCP relay on the agent host.
A hospital-controlled evaluation server, an approved assessment, an active
machine grant and an approved network path are still required. This is a
restricted request and metadata interface, not unattended remote execution.

## Administrator setup

Install and validate the approved release on the evaluator host, prepare the
assessment and provision a short-lived scoped bearer grant as described in
EVALUATOR_REQUEST_API.md. Never embed actual credentials in prompts, source
control, URL parameters or terminal logs. Use approved client secret handling.

    python -m caeval.evaluator_remote_mcp --workspace /private/institution --origin https://eval.hospital.example --port 8773

This hostname is illustrative. The reference WSGI server binds only to IPv4
loopback. An approved HTTPS reverse proxy must preserve the configured exact
Host and /mcp path, forward Authorization securely, and enforce client access,
TLS, request limits, timeouts, rate limits and service supervision. Do not
expose the development listener directly. Do not log grant headers or bodies.
Do not forward browser cookies or Origin headers; browser-origin requests are
not supported. Only /mcp is served by this adapter. Follow the separation and
security guidance in EVALUATOR_AGENT_GATEWAY.md.

## Agent connection

Claude Code supports remote HTTP MCP configuration. For example, an approved
private client configuration can use an environment variable reference:

    {"mcpServers":{"clinical-ai-eval":{"type":"http",
      "url":"https://eval.hospital.example/mcp",
      "headers":{"Authorization":"Bearer ${CAEVAL_AGENT_TOKEN}"}}}}

Provision that variable using institutional secret management outside the
model's prompt and avoid granting the agent unrestricted access to secrets.
Use client-specific managed configuration and tool approval settings. An
ordinary shell-enabled agent is not itself a security boundary. Do not assume
local invocation means local inference; even aggregate returned metadata may
be sent to the model provider. The example is not a running public endpoint.

The pilot implements the 2025-06-18 Streamable HTTP JSON response subset:
initialize, initialized notification, ping, tools/list and tools/call. No
session IDs, SSE streams, batching, server-initiated requests, OAuth discovery
or automatic token refresh. Subsequent POSTs must include the negotiated
MCP-Protocol-Version header, and Accept must list application/json and
text/event-stream. Unsupported protocol headers are refused. GET and DELETE
are unsupported (405); the transport does not retain session state. The
native bearer grant is rechecked on every accepted tool call and on handshake,
notification, ping and tool discovery. The caller must handle errors and
reprovision an expired credential through trusted IT. Compatibility with a
real approved Claude Code or Codex version must be demonstrated, not assumed.

The five tools discover the granted assessment, list owned requests, request
local human approval with a stable idempotency key, inspect owned status and
retrieve a completed metadata receipt. No caller-supplied workspace path,
assessment override, model endpoint, prompts, records, credentials, raw
package release, remote approval or execution operations are exposed. Native
approval, queue and model-call authorization remain mandatory. No assessment
results or clinical validation are implied by an accepted request.

## Verification

    python -m pytest -q tests_unit/test_evaluator_remote_mcp.py

The isolated tests use an evaluator test double and exercise real loopback
HTTP, negotiation, tool mapping, grant refusal and hostile inputs. Real native
grants, TLS proxy, actual client interoperability, full integration CI,
institutional security review and clinical validation remain separate gates.

Protocol reference: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
Claude Code configuration: https://code.claude.com/docs/en/mcp
