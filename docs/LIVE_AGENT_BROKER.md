# Live scoped local agent metadata (pilot)

The existing fixed snapshot MCP server remains the safer default. The optional
live broker is intended for institution-approved agents that need current
evaluation and procurement metadata, without opening the workspace filesystem
to the agent. The broker runs under trusted IT control on a POSIX server;
the MCP relay can run under a separate restricted account on that host.

## Trust and access boundary

Only the broker needs workspace access. A relay receives a private bearer-token
file and access to an approved Unix socket. No HTTP listener, arbitrary file
paths, clinical case text, candidate names, individual verdicts, credentials,
shell commands, assessment execution or procurement writes are exposed by
the protocol. The existing aggregate snapshot schema is reused and validated
before each response. A live view is a fresh computation, not a transactional
snapshot of all concurrent filesystem changes or clinical decision evidence.

The four possible scopes are:

- clinical_eval_capabilities
- clinical_eval_job_summary
- clinical_eval_research_summary
- clinical_eval_procurement_inventory

Grant only the tools needed by the approved agent. Metadata (including counts
and research status) may itself be confidential. The agent's model provider
may receive the returned data, even though the broker transport is local.
Use approved clients and enforce institutional egress/data-handling policy.

## Setup

The trusted administrator creates a protected broker-generation file outside
the workspace. Do not share this file with agent accounts.

    python -m caeval.agent_live_cli generation-create \
      --out /approved/caeval-agent-generation.key

Create a separate protected socket directory, owned by the broker account
with an approved group for authorized relay accounts. The directory must
not be group/world writable; all ancestors must be under trusted IT control.
Run the broker with that group and the least privileges needed for the
workspace. The socket is created with 0660 permissions. Do not expose it
through an HTTP proxy or mount the workspace into the agent's environment.

    python -m caeval.agent_live_cli serve /srv/caeval/workspace \
      --socket /run/caeval-agent/broker.sock \
      --generation-file /approved/caeval-agent-generation.key

An administrator issues a grant only after approving the intended data use:

    python -m caeval.agent_live_cli issue /srv/caeval/workspace \
      --generation-file /approved/caeval-agent-generation.key \
      --owner approved-operator \
      --scope clinical_eval_procurement_inventory \
      --scope clinical_eval_job_summary \
      --ttl 3600 --calls 40 \
      --out /approved-transfer/agent-token --confirm-sharing

The owner must be an active admin, operator or observer. Reviewers and vendors
cannot receive a workspace-wide metadata grant. TTL is at most 24 hours and
the call budget at most 200. Grant tokens are random, stored as hashes in
the access database and written once to an owner-only file, never printed
in the CLI output. Transfer the token file securely to the intended agent
account; it is a bearer credential, not an identity proof. Do not put it in
client configuration text, source control, workspace or backup.

Configure an approved local MCP client (for example, Claude Code) with:

    {
      "mcpServers": {
        "clinical-ai-eval-live": {
          "command": "/approved/bin/python",
          "args": ["-m", "caeval.agent_live_cli", "mcp",
                   "--socket", "/run/caeval-agent/broker.sock",
                   "--token-file", "/approved-agent/token"]
        }
      }
    }

Client interoperability with the existing supported stdio MCP protocol
versions must be tested in the actual environment. The relay does not need
the workspace path. Other tools in the agent client are not sandboxed by this
broker; enforce OS-level isolation separately.

## Revocation and operations

    python -m caeval.agent_live_cli inventory /srv/caeval/workspace
    python -m caeval.agent_live_cli revoke /srv/caeval/workspace --grant GRANT_ID

Each call transactionally reserves one unit of its budget before computation.
Failed reads consume a reserved call; ungranted tools are refused. The broker
rechecks grant validity, generation, account and scope before releasing the
view. Revocation blocks subsequent authorizations; it cannot retract an
already delivered or in-flight response. Disabling the owner blocks access.
A metadata-only audit records grant issuance, revocation and authorized read
requests/outcomes, not bearer tokens or clinical content.

The integrated restore command revokes copied grants and a restored workspace at a different path also rejects original grants. For EVERY
restore or rollback, including a restoration to the same path, stop the
broker, rotate its external generation file to a newly generated value, and
restart it with the new file before granting access. Existing tokens then
fail the generation check. Do not reuse the old generation file or assume
session invalidation automatically invalidates agent grants.

The reference broker is single-worker with bounded message sizes and socket
timeouts, not a production multi-tenant service or denial-of-service defense.
Limit socket access to approved local processes, apply service supervision
and OS resource limits, and monitor availability. Expiry and call budgets
do not erase information an agent has already received. Actual institutional
security review, recovery drills and client acceptance remain mandatory.
