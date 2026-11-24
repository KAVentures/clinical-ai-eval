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
