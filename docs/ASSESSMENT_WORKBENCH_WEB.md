# Local assessment browser

The standalone browser provides a local operator interface to the coordinated
assessment workbench. It is an experimental review console, not an
institutional multi-user application or a clinical release gate.

## Start a session

An authorized operator first provisions the procurement database, predictive
study, prospective binary study, assessment plan and trusted operator config
as described in ASSESSMENT_WORKBENCH.md. This still requires the existing
CLI/API or a separately approved provisioning integration. Retain the plan
and all input checkpoints independently. No arbitrary paths, upload forms,
network integrations or source editing are exposed by the browser.

Run from a private local terminal (not a shared console, remote service,
container port forward, or log-capturing automation):

    python -m caeval.assessment_workbench_web --plan assessment-plan.json --expected-plan PLAN_DIGEST --config config.json

Open the printed loopback address, using exactly 127.0.0.1 and the printed
port. When prompted, use username `operator` and the newly generated session
password displayed in the terminal. The password is not embedded in the URL
or stored in a file. Stop the process with Ctrl-C when finished and close the
browser session. Do not share the password or expose the listening port.

## Operator actions

- Run assessment recomputes all three workbenches against the pinned plan and
  configured inputs. The page shows scope, missing documents, unresolved and
  overdue clarifications, source coverage, paired Brier and net-benefit
  bounds, pooled adverse outcome bounds, and population-shift flags.
- Replay and verify recomputes the report using the configured inputs. A
  successful replay means the same report is reproduced, not that original
  clinical evidence or model execution has been independently verified.
- Download verified JSON requires a successful replay and rechecks the inputs
  at download time. Save to an appropriately restricted local directory;
  the browser controls download permissions, history, and storage. Retain
  the displayed report digest separately. Do not send the report to vendors
  or publish it without approved review.

The report is kept in process memory, not persisted by the web server. A
failed run or replay invalidates the cached report. Changing the operator
config requires restarting the session. A stale procurement head is refused;
use an approved consistent database snapshot for historical replay. The
operator config is locally trusted and pinned at startup, not independently
signed; the plan and source checkpoints must be governed separately.

## Security and limits

The standard-library HTTP server binds only IPv4 loopback on a fresh port.
It checks the exact Host and loopback peer, requires a random per-session
HTTP Basic credential, rejects cross-origin requests, checks a separate
CSRF token on all POST actions, and serves no third-party content or scripts.
Responses prohibit caching and framing; errors omit source paths and values.
No GET route triggers analysis, replay or export. No changes are made to
underlying source files or the procurement database. The server is single
operator and serial, with a connection timeout; it is not hardened for
untrusted multi-user or network exposure. Local HTTP is not TLS. A malicious
local process, administrator, browser extension or compromised OS is outside
the trust boundary. Use on an approved private workstation only.

This is not a general provisioning wizard. The existing CLI/API remains the
source for creating requests, freezing plans, importing evidence and recording
clarifications. Multi-user authentication, hospital SSO, audit integration,
role-based authorizations and production deployment require the existing
institutional application integration. No clinical safety claim, combined
score, automatic recommendation or publication clearance is provided.

Run tests with:

    python -m pytest -q tests_unit/test_assessment_workbench_web.py

Tests use synthetic data and exercise the real three workbenches, including
an actual loopback HTTP request sequence, replay failure, download-time
revalidation, stale config, authentication, host/origin/CSRF checks and
escaped HTML output.
