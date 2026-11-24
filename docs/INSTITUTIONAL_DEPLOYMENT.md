# Institutional deployment: browser-only pilot

Clinical AI Eval retains its existing research CLI, evaluation protocols and
standalone buyer workspace. The new institutional gateway is separate from the
single-user buyer console. Staff use a normal browser over HTTPS; they install
nothing on their managed workstations.

## Scope and trust boundary

Deploy one instance, service account and workspace per organisation. Only trusted
IT administrators and scientific operators may access the underlying filesystem
or CLI. Different organisations must use separate workspace roots, OS accounts
and service instances. The gateway does not share cookies, accounts or review
records across workspaces. It is an early internal pilot, not a production IAM
system, a multi-tenant SaaS or a certification of clinical safety.

The gateway exposes a minimal allowlist: login/logout, redacted job overview,
assigned blinded review and administrator access audit. It does not mount the
legacy buyer console, arbitrary files, research artifacts, provider credentials
or model execution. No vendor-facing application is enabled.

| Role | Browser access |
| --- | --- |
| admin | Redacted job overview and access audit |
| operator | Redacted job overview |
| observer | Redacted job overview |
| reviewer | Only own assigned blinded review cases; own submission state |
| vendor | Denied; reserved for a future separate supplier portal |

Local account identity is not verification of medical credentials or reviewer
independence. The browser records immutable verdicts bound to account, assignment
and package digest. It neither modifies the canonical run nor upgrades conformance.
Scientific adjudication remains a separate research workflow.

## Installation and configuration

Use an approved, version-pinned release artifact and a locally provisioned Python
environment. For offline deployment, obtain the wheel and all pinned dependency
wheels through approved channels and verify their hashes. Install with
pip --no-index --find-links /approved/wheelhouse clinical-ai-eval==VERSION.
For the preferred independently approved wheelhouse workflow, exact target
checks, side-by-side installation and rollback, see
[Offline installation and upgrades](OFFLINE_RELEASE.md).
The institution feature itself uses the standard library and existing engine
dependencies. No third-party cloud identity or model service is required.

Create an OS-owned private workspace (0700 on POSIX) and provision individual
accounts from the trusted administrator terminal:

    clinical-ai-eval buyer init /srv/caeval/workspace --name "Hospital pilot"
    clinical-ai-eval institution account-create /srv/caeval/workspace --user-id admin-a --role admin
    clinical-ai-eval institution account-create /srv/caeval/workspace --user-id clinician-a --role reviewer
    clinical-ai-eval institution account-create /srv/caeval/workspace --user-id clinician-b --role reviewer
    clinical-ai-eval institution account-create /srv/caeval/workspace --user-id observer-a --role observer

Passwords are prompted interactively and stored as salted scrypt hashes.
Distribute initial credentials through approved channels, never a shared
account. Local administrators can disable accounts and revoke their sessions:

    clinical-ai-eval institution account-disable /srv/caeval/workspace --user-id clinician-a

Run the reference gateway on loopback only:

    clinical-ai-eval institution serve /srv/caeval/workspace --origin https://clinical.example.org --port 8767

An approved HTTPS reverse proxy must terminate TLS, enforce the exact configured
host and preserve Host and Origin, restrict access to the institutional network,
limit request size and request rates, and block direct access to the backend.
Do not publish the plaintext backend port or expose the standalone buyer server.
The reference wsgiref launcher is intended for a controlled pilot only.
An approved production WSGI server can instantiate
caeval.institution_web.InstitutionApp(workspace, origin) behind the same proxy.
Use a dedicated unprivileged account, read-only application dependencies,
encrypted volume, monitored resource limits, and restrictive egress. The
gateway itself does not call models; evaluation workers have separate operator
approval and network policy. Do not run the evaluation worker in the HTTP
request process.

Example proxy configuration (adapt to local institutional policy and certificates):

    server {
        listen 443 ssl;
        server_name clinical.example.org;
        ssl_certificate /approved/tls/fullchain.pem;
        ssl_certificate_key /approved/tls/privkey.pem;
        client_max_body_size 8m;
        location / {
            proxy_pass http://127.0.0.1:8767;
            proxy_set_header Host clinical.example.org;
            proxy_set_header Origin $http_origin;
            proxy_set_header X-Forwarded-Proto https;
        }
    }

Only the approved HTTPS origin is accepted for writes. Cookies use Secure,
HttpOnly, SameSite=Strict, and the __Host- prefix; sessions expire after eight
hours. Mutating forms also require the session's CSRF token. The local login
throttle limits guesses, but shared global throttling can temporarily deny
legitimate users during an attack; configure proxy rate limits and monitor
availability. Institutional SSO/MFA, reset and recovery flows, and external
security testing remain release requirements.

## Review workflow

The trusted scientific operator creates and executes a candidate assessment
using the existing buyer workflow and research engine. After a completed job has
been verified, the administrator assigns each reviewer to a specific family:

    clinical-ai-eval institution review-assign /srv/caeval/workspace --user-id clinician-a --job-id JOB_ID --family-id missing_information
    clinical-ai-eval institution review-assign /srv/caeval/workspace --user-id clinician-b --job-id JOB_ID --family-id missing_information

Each reviewer signs in separately, sees only their assignments and the canonical
blinded case/response pairs, and submits safe, unsafe or cannot_judge. They
cannot choose another reviewer's identity, access another assignment, view
automated scores or overwrite an earlier verdict. If a package changes,
the assignment fails closed. Review records reside in the institutional SQLite
database, separate from the original scientific artifacts.

## Data governance and operational acceptance

Keep protected information out of initial pilots. Before use with real patient
information, obtain applicable organisational approvals, a documented data
inventory and lawful basis, security and privacy review, approved retention
and deletion rules, and clinical validation. Use the institution's own policies.
No deployment mode automatically authorises transmission to model providers.

Audit records capture account actions, assigned review access, submissions,
redacted job overview access and denied authenticated requests, without storing
case text or reviewer notes in the audit trail. The local administrator and
database operator remain trusted; this is not a cryptographically sealed audit
log. Forward approved audit metadata to institutional monitoring as appropriate.

For pilot backup, stop the gateway and all workers, then back up the entire
workspace, including SQLite databases, case packs, bindings and run manifests,
to encrypted approved storage. Restore to an isolated staging workspace;
verify scientific packages, account boundaries, assignment state and sessions
before service. Revoke old sessions on restore before reopening access.
Keep a previous pinned application release and matching consistent backup for
rollback; do not assume schema downgrade compatibility.

Before production approval, complete institutional SSO/MFA and reviewer
credential provisioning, signed clinical review packet integration, granular
procurement write permissions, retention/deletion tooling, independently
reviewed audit integrity, backup/restore automation, deployment hardening,
concurrency/load testing and independent security assessment. The current
pilot is deliberately not presented as hospital-ready or clinically validated.


## Implementation and lifecycle register

Administrators and operators can create a version-specific implementation pilot
for a registered candidate, designate an accountable owner, specify intended
scope, a review date and a rollback plan. They can record risks, incidents,
version changes and monitoring actions. Observers have read-only register
access. Reviewers and suppliers cannot access the register.

Each write checks the current revision within a SQLite transaction; stale
browser forms are refused. Serious open risks and any open version change
pause the administrative record and block return to review. Administrators
must record their resolution and reasons; closure requires all items resolved.
The state is a human-recorded administrative status, NOT a live deployment
control, an automated incident detector or evidence of clinical safety.

Completed baseline/retest jobs can be linked only if the verified candidate
and exact product version match the pilot. Linked package digests are checked
again on viewing. Changed or unavailable evidence is displayed explicitly.
A version change requires a separate version-specific pilot for the new
version; do not treat earlier results as evidence of the updated version.
No result automatically authorizes implementation or releases a product.

The register retains transactionally recorded snapshots and resolution reasons.
Administrators can inspect the event history; access metadata appears in the
existing audit log. Keep patient identifiers and confidential clinical records
out of free-text fields. Retention, incident escalation, actual rollback,
prospective outcome monitoring and production approval remain organisational
responsibilities. The register is not a substitute for clinical governance.


## Scientific review analysis and evidence overview

The institutional evidence overview displays per-job package verification state,
the recorded claim ceiling, selected review queue size and recorded assignment /
submission counts. Observers may see the overview but cannot initiate analysis.
Counts describe recorded accounts, not credential-verified independent clinicians.

After two accounts have submitted reviews, an administrator or operator may
compute exploratory scientific statistics. The bridge verifies assignment,
submission digest, review coverage and source package integrity. It executes
the original adjudication code on a private temporary copy of the required
research artifacts; original run files and manifests are not altered. The
temporary files remain within the institution-owned workspace and are removed
on normal completion. An interrupted process may leave temporary files that
must be handled under the organisation's retention policy.

The view reports review coverage, unresolved disagreement, Cohen kappa,
Krippendorff alpha where estimable, and judge-versus-reviewer-majority sensitivity,
specificity, predictive values and denominators. Reviewer accounts are
pseudonymised within the report, and notes are excluded. A changed review set or
evidence package requires a new analysis.

The analysis is explicitly exploratory. It does not issue signed independent
clinical packets, adjudicate perturbation validity, grant L2 conformance,
or make a procurement or deployment recommendation. The original scientific
clinical review and validation requirements still apply. Metrics from a selected
review queue are not population performance estimates.


## Institutional procurement committee

The committee browser presents the existing buyer register, missing documents,
vendor disclosures (explicitly self-reported), and frozen clinical comparisons.
Operators prepare the shortlist and freeze the scientific study using the
existing research/procurement CLI. Only administrator-staged packages below
`assessment/packages/` in this organisation's workspace may be reconstructed
in the institutional view. Register the exact subject name and version in the
frozen study before ingesting a candidate's package. The institutional
comparison excludes missing, altered, externally located, misattributed or
unverified packages, and reuses the canonical procurement comparator rather
than independently calculating thresholds.

Operators and administrators can freeze content-addressed evidence snapshots.
Each snapshot includes the exact manifest hashes, product versions, criteria,
rates, uncertainty, gaps and limitations. Observers can view and download
committee evidence. Reviewer and vendor roles cannot access it. The JSON
export is an administrative committee artifact, not a scientific evidence
package or clinical approval. Treat it as confidential organisation data.

Administrators may record a request for more evidence, deferral, referral for
governance review or closure of committee review, with an explicit rationale.
These append-only administrative dispositions are linked to the exact snapshot;
no purchase, clinical use or deployment can be authorized from this feature.
Changed current evidence requires a new snapshot. Historical snapshots remain
visible, labeled as historical. Underlying files are controlled by trusted IT
and research operators; hashes are not digital signatures, and the current
implementation is not a cross-process filesystem transaction.

No composite score or ranking is produced. All scientific comparisons remain
experimental and subject to independent clinical validation. A procurement
committee must make its own risk-proportionate decision using the full
institutional governance process.


## Automated local candidate collection

For a single-turn candidate, first prepare the existing blinded manual exchange.
An administrator stages a JSON connector under
`approved-connectors/<connector-id>.json`, with exactly these fields:

```json
{
  "vendor_id": "candidate-a",
  "target_name": "Candidate A",
  "target_version": "1.0",
  "url": "http://127.0.0.1:8080/evaluate",
  "prompt_field": "prompt",
  "answer_path": "answer",
  "headers": {},
  "timeout_seconds": 30
}
```

The URL must be literal loopback HTTP with an explicit port. The product must
be running on the execution host. This transport disables proxies and redirects;
it does not establish that the target itself refrains from external calls.
Apply institutional egress restrictions and use approved non-identifiable
cases. Credentials may only be environment references in the supported
Authorization or X-API-Key headers, not literal secrets.

The institutional operator can queue and cancel collections from the browser.
The trusted CLI operator executes the exact approved binding:

```bash
clinical-ai-eval buyer connector-list ./hospital-assessment
clinical-ai-eval buyer collection-queue ./hospital-assessment --exchange-id EXCHANGE --connector-id local
clinical-ai-eval buyer collection-execute ./hospital-assessment --collection-id COLLECTION --approve-binding HASH --operator OPERATOR
clinical-ai-eval buyer collection-status ./hospital-assessment --collection-id COLLECTION
```

Queueing sends no data. Execution requires the binding hash and revalidates
the connector configuration, candidate/version and exact prompts. The
transport sends only the case-as-shown text, bounds requests and responses,
does not retry, stops after repeated failures and checks a 15-minute deadline
between calls. The socket timeout is not a hard process deadline; run under
an approved process supervisor with resource and wall-clock limits.

The returned receipt identifies the frozen response submission for the existing
assessment preparation workflow. Incomplete collections cannot be handed to
the scoring engine. The recorded product version is still operator-configured,
not cryptographically authenticated by the endpoint. The submission remains
unscored and operator-attested. Real patient data, external endpoints, multi-turn
collection, vendor attestation and deployment authorization remain outside
this feature. A crashed running collection requires operator investigation;
no automatic retry is performed.


## Local MCP agent integration

Clinical AI Eval provides a native stdio MCP interface over an explicitly
exported, aggregate-only metadata snapshot. The agent process does not need
access to the clinical workspace, and must not be given access to it through
other tools or filesystem permissions. An approved administrator exports
metadata to a separate, protected location:

```bash
clinical-ai-eval agent export /srv/caeval/workspace --out /approved-agent-data/summary.json
clinical-ai-eval agent mcp /approved-agent-data/summary.json
```

For Claude Code or another compatible local MCP client, configure a stdio
server using the absolute path of the installed executable:

```json
{
  "mcpServers": {
    "clinical-ai-eval": {
      "command": "/approved/bin/clinical-ai-eval",
      "args": ["agent", "mcp", "/approved-agent-data/summary.json"]
    }
  }
}
```

The server implements the initialization and read-only tools subset of MCP
2025-03-26 and 2024-11-05. Compatibility with individual current clients
must be verified before deployment. No HTTP listener or SDK dependency is
required. It supports four tools: scientific capabilities, evaluation job
summary, research package summary, and aggregate procurement inventory.
Every tool takes an empty argument object; the client cannot specify paths,
run shell commands, change records, or initiate evaluations.

The export excludes clinical text, candidate names, reviewer identities,
credentials, paths, free-text diagnostics and individual verdicts. It contains
counts, installed scientific family identifiers, enumerated states and claim
ceilings. A strict schema is validated independently of its digest. The
snapshot is loaded once on startup and is not live; stop the process and
explicitly approve a new export to refresh it. Its digest is an integrity
check, not a digital signature or a proof of authorization.

Even aggregate information may be confidential. A cloud-backed agent may
transmit tool outputs to its provider; only use a client approved for the
information in the export. Run the agent and MCP process under a separate
least-privilege account with access only to the approved export and installed
application dependencies. The gateway is not a sandbox for other client tools.
Full live institutional MCP with authenticated scoped grants remains separate.


## Integrated institutional assessment setup

Administrators and operators can now use the authenticated **Candidate intake
and assessment setup** page for the complete single-turn preparation workflow:

1. Register a candidate and store evidence documents or import completed vendor
   questionnaires. Exports are intended for approved offline supplier exchange.
2. Describe the exact product/version and intended workflow, answer every
   canonical intake question and save a draft scientific plan. Required but
   unavailable families remain visible.
3. Select a case pack staged by the trusted IT operator, prepare blinded prompts,
   then import the returned response template or queue an approved automated
   local candidate collection.
4. Choose an administrator-staged judge panel and answer the canonical governance
   questions to prepare a bound assessment from complete responses.
5. Queue or cancel an assessment. An authorized CLI operator executes the
   approved job; the browser never authorizes network/model execution.

The setup page is intentionally unavailable to observers, reviewers and suppliers.
Each write rechecks the operator account and uses the existing buyer workflow.
Multipart uploads are bounded by the 8 MiB request limit and reject duplicate
fields. Uploaded documents are stored as data, not served or executed. Browser
setup writes are serialized using the institutional database; trusted operators
must avoid concurrent CLI edits of the same buyer register. Database audit and
filesystem writes are not a single distributed transaction. This is still a
pilot deployment; staged scientific inputs, source authenticity, validation,
production identity and security acceptance remain institutional responsibilities.


## Offline backup and new-workspace recovery

The trusted server operator can create a bounded, self-contained checkpoint
after stopping the institutional gateway, the standalone console, every
evaluation/collection worker and all other workspace writers. Use approved
encrypted storage with enough free capacity. The checkpoint contains private
evidence and account databases; its file hashes do not encrypt it or establish
its authenticity. The current limit is 20 GiB and 20,000 filesystem entries.

```bash
clinical-ai-eval institution backup-create /srv/caeval/workspace \
  --destination /approved-backup/checkpoint-001 --confirm-services-stopped
clinical-ai-eval institution backup-check /approved-backup/checkpoint-001
clinical-ai-eval institution backup-restore /approved-backup/checkpoint-001 \
  --destination /srv/caeval/recovered --confirm-services-stopped
```

The checkpoint records the complete relative inventory and SHA-256 file hashes,
and verifies SQLite databases read-only. Running jobs, transient staging data,
SQLite journal/WAL sidecars, symlinks, hardlinks and special files block the
operation; investigate rather than deleting unresolved evidence. The command
requires operator confirmation but cannot prove the service is actually stopped.
Checkpoints and recovery destinations are never overwritten. Verify the backup
identifier through an independently trusted channel when moving it between
approved systems.

Restore checks the checkpoint, stages a fresh workspace, revokes copied sessions,
and normalizes internally stored procurement package paths for the new location
without modifying source evidence. External procurement package locations must
be staged within the workspace before checkpointing. Other external operator
configuration and environment credentials are provisioned independently.
The original workspace and checkpoint remain unchanged. Use a compatible pinned
software build, recheck research packages and job state, then test role boundaries
and service configuration before switching the approved proxy to the recovered
workspace. File verification alone does not establish clinical validity.

## Integrated acceptance and storage guarantees

The combined product uses a shared SQLite write lock for supported buyer
register operations (candidate registration and evidence insertion), including
the institutional setup workflow and existing CLI. Immutable JSON records are
published from fully written private temporary files without overwriting an
existing destination. Use a local filesystem supporting SQLite locks and
hardlinks; direct out-of-band editing and unsupported shared filesystems are
not covered. Audit writes and filesystem operations are not a distributed
transaction.

Run the full existing test suite and the synthetic integrated acceptance test
before any deployment:

```bash
python -m pytest tests_unit -q
python -m pytest tests_unit/test_institution_acceptance.py tests_unit/test_institution_backup.py -q
```

The acceptance test exercises authenticated browser intake, disclosure exchange,
scientific planning, blinded prompt exchange, candidate response import,
prepared assessment, actual canonical mock-judge execution, assigned physician
review, exploratory statistics, aggregate agent export, and checkpoint recovery.
It is a software integration test using synthetic fixtures, not evidence that a
clinical AI product is safe or that the institutional service is production-ready.


## Restricted local multi-turn patient evaluation

The canonical research CLI now supports an opt-in literal-loopback transport
for conversational patient products. Create and validate a normal project
with the patient-facing profile, a compatible patient-world case pack, and an
approved judge panel. In the project subject configuration, use:

```yaml
subject:
  kind: http
  modality: conversation
  local_only: true
  url: http://127.0.0.1:8080/chat
  history_field: messages
  answer_path: answer
  session_field: session
  timeout: 30
  retries: 1
  headers: {}
```

Use a fresh output workspace and explicitly launch the standard
`clinical-ai-eval run --project <project>` command as an authorized operator.
The optional session field is cleared between every baseline and stress
episode. The transport sends only the disclosed conversation (role/content)
and the optional product-returned session identifier, not hidden case facts.
It refuses redirects, proxies, DNS names, literal credentials, extra request
payload overrides and automatic retries. It limits request/reply size,
candidate calls and elapsed time between calls, and rejects a completed
evidence package if an episode suffers a transport error. A socket timeout
is not a hard wall-clock deadline; retain an approved process supervisor.

This is the existing experimental patient evaluator, not a new scoring
method, and local transport does not prove that the product itself avoids
external requests. Use synthetic or approved non-identifiable case packs,
approved local judges or explicitly authorized external judge configurations,
and institutional egress controls. Browser preparation of conversational
jobs remains separate; the initial controlled route is the research CLI.
The corrected per-episode reset changes the scientific session definition
hash: older patient packages should be verified with their pinned version,
not rehashed to claim they used the corrected executor.


## Managed institutional identity and deployment

For an institution with an approved OIDC/SAML authentication gateway, see
[Institutional federation](INSTITUTIONAL_FEDERATION.md) for explicit upstream
subject mapping, per-request identity checks, local role provisioning,
session isolation and the protected proxy contract. The application does not
itself implement OIDC/SAML token validation or MFA; the upstream gateway must
enforce those policies. A managed WSGI app factory and sample Linux/systemd
deployment configuration are included under deploy/institution. Actual IdP
interoperability, security and production acceptance remain mandatory.
