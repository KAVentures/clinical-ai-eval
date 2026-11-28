# Native remote MCP acceptance rehearsal

Run from the approved integrated repository with the shipped synthetic case
fixtures and mock judge configuration available:

    python -m caeval.evaluator_native_mcp_acceptance

This is a disposable demonstration of the remote MCP adapter against the real
native evaluator database, prepared assessment, bearer grant, queue approval,
canonical evaluation runner and package verification. It requires the native
evaluation dependencies and shipped fixtures. It does not accept a directory,
clinical data, credentials, model endpoints or arbitrary commands. The
evaluation uses the shipped all-mock panel and a synthetic manual response
submission. It never evaluates a real vendor product.

The probe uses actual loopback HTTP and the pinned MCP 2025-06-18 subset. It
discovers a scoped assessment, submits an idempotent evaluation request,
verifies there is no queued job, refuses a premature receipt and cross-grant
status read, and rejects an execution tool. A separate local action explicitly
approves one canonical queue entry, then executes the mock job using the
existing native runner. The MCP client receives verified metadata, which is
compared with a separately retrieved local receipt and checked using the
native metadata verifier. Cross-grant receipt access and post-revocation
access are refused. The local approval is performed by this synthetic test
harness, not remotely exposed to the agent.

Only aggregate diagnostic flags and a package count are printed. The
temporary database, artifacts and generated credentials are discarded on
normal completion or exception; abrupt process termination may need cleanup.
The server binds to loopback with an illustrative Host, not a live HTTPS
endpoint. This is not a network sandbox. Run under the institution's approved
offline test policy; the native job runner is responsible for mock-mode
execution. No real model calls are authorized.

Exit code zero means the synthetic rehearsal completed. It is not proof of
real Claude Code/Codex interoperability, TLS reverse proxy security,
institutional permissions, clinical accuracy, purchasing readiness or
deployment authorization. These require their own acceptance tests.

    python -m pytest -q tests_unit/test_evaluator_native_mcp_acceptance.py

The primary integration test deliberately does not stub the assessment,
native evaluator or job runner. The two CLI output tests use test doubles.
Run in full repository CI before release. The diagnostic module is separate
from the actual production gateway and must not be used to approve real
evaluations automatically.
