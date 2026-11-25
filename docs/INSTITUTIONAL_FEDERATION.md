# Institutional federation and managed-server deployment

The institutional browser can use either the existing local account login or
a **gateway-authenticated mode**. In the latter, an institution-approved OIDC
or SAML gateway authenticates the user and enforces its MFA policy before
forwarding each request. The Clinical AI Eval application is not an OIDC
relying party or SAML service provider: it does not validate identity-provider
tokens, assertions, certificates, authorization codes or MFA challenges itself.
The gateway must do so. This is an integration boundary, not a claim of
interoperability with a particular IdP or of production security approval.

## Trust boundary (mandatory)

Deploy one service account and private workspace per organisation. Configure
one exact public HTTPS origin and one approved upstream issuer. Only the
approved gateway/reverse proxy may reach the application socket. Use a Unix
socket with restrictive ownership and permissions, or an independently
approved equivalent protected transport. Never expose the backend to the
public network, a general internal network or untrusted local processes.

For every request, the approved gateway must:

1. Authenticate the user with its approved OIDC/SAML implementation. Validate
   signatures, issuer, audience, lifetime, session state, nonce/replay
   protection and the actual institutional MFA assurance policy as applicable.
2. Strip ALL incoming client-provided identity and gateway-authentication
   headers, including equivalent spellings, duplicates and underscore variants.
   Do not rely on client-supplied groups, roles, email addresses or claims.
3. Supply the following backend-only headers from verified identity state:

   | Header | Required value |
   | --- | --- |
   | X-CAEVAL-Gateway-Secret | Dedicated 256-bit random secret in lowercase hex |
   | X-CAEVAL-Issuer | Exact configured HTTPS issuer |
   | X-CAEVAL-Subject | Stable, non-reassignable upstream subject identifier |
   | X-CAEVAL-Assurance | institutional-mfa, ONLY after the approved MFA policy succeeds |

The assurance value is a gateway assertion, not independent verification
of MFA by this application. Do not set it unconditionally after password-only
authentication. The backend checks the secret, issuer and assertion on every
request. A compromised gateway or leaked gateway secret could impersonate users;
keep the secret out of URLs, access logs, error logs and client-facing responses.
Use a dedicated secret per deployment, stored separately from the workspace.
Rotate it through the approved gateway and restart the app together if needed.
The gateway must maintain its own upstream authentication and revocation policy.

The proxy must preserve the exact configured public Host and legitimate Origin,
terminate TLS, limit request bodies and rates, enforce network access policy,
and avoid logging sensitive headers or request contents. Do not proxy the
standalone research console or CLI to browsers.

## Provisioning

Install a pinned clinical-ai-eval wheel and reviewed dependencies in a private
server virtual environment. If offline, install from an approved, hash-verified
wheelhouse. The application does not require an additional IdP SDK or cloud
identity service. The upstream gateway and WSGI server are installed and
managed by institutional IT. Configure the dedicated service account, private
workspace (0700 on POSIX) and protected gateway key location outside it.

Generate a gateway credential once on an approved administrator terminal:

    python -m caeval.institution_deployment gateway-secret-create \
      --out /etc/caeval/gateway.key

Grant only the intended gateway/service identities access by approved secure
distribution. The application's copy must be a private regular file (0600 or
more restrictive on POSIX); do not put the credential in the workspace or its
backup.

An administrator provisions each federated account explicitly:

    python -m caeval.institution_deployment federated-account-create \
      /srv/caeval/workspace --user-id clinician-a --role reviewer \
      --issuer https://id.example.org/tenant

The stable subject is prompted privately; a protected subject file is also
supported. Use the exact verified immutable identifier the gateway forwards,
not a mutable email address, display name or group. Provision a separate
administrator the same way. The role is assigned locally, and never inferred
from gateway claims. Existing local accounts cannot be silently linked;
identity migration requires separate approved account and review provenance
planning. Federated accounts cannot use password login. The stored subject
hash is a mapping identifier, not a guarantee of anonymisation.

Use the existing account-disable command for immediate local access revocation.
It invalidates sessions and leaves existing review records for audit. The
gateway must also revoke upstream access as appropriate. Reprovisioning a
different person under a previous subject or account is not supported.

## Start the managed browser service

Set the environment variables shown in deploy/institution/institution.env.example.
The application requires an explicit CAEVAL_AUTH_MODE of gateway or local.
Gateway mode has no fallback to local password login. Local mode rejects
federation configuration. Run the preflight with the intended service identity:

    python -m caeval.institution_deployment preflight

Preflight checks private configuration, HTTPS origin, gateway key format,
SQLite integrity and an active administrator for the selected mode. It does
not contact the IdP, prove proxy isolation, verify certificates, test MFA
or authorize production deployment.

An approved WSGI server can load caeval.institution_deployment:create_app().
The sample systemd service under deploy/institution uses a restricted Unix
socket, a dedicated non-root account, a private workspace and AF_UNIX-only
network sockets for the browser process. Adapt and test the service under the
actual OS, proxy, approved wheelhouse and security policy. Evaluation workers
must use separate execution identities and approved network policy. Do not run
the reference development listener as a public or production service.

The browser issues its own secure, HttpOnly, SameSite=Strict session and CSRF
token. A federated session lasts at most one hour and is checked against the
current verified upstream subject and local account on every request.
Switching identities cannot reuse a previous user's session. Signing out
revokes the application session, not the IdP session; use the gateway's
approved logout flow when upstream sign-out is required. Password and
federated sessions cannot be used interchangeably. No tokens, credentials,
case contents or upstream subject values are recorded in access audit events.

## Institutional acceptance (must be completed locally)

- Verify actual OIDC/SAML federation, issuer and stable subject semantics,
  MFA assurance enforcement, upstream revocation and subject lifecycle.
- Demonstrate backend socket isolation and rejection of forged headers,
  duplicate/underscore header variants and unauthenticated requests.
- Verify cross-account and cross-role access denials, local account revocation,
  CSRF, session expiry, cookie flags, logout, and protected audit records.
- Verify TLS, approved reverse-proxy limits, sandbox and no browser-process
  external network access; separately approve execution worker egress.
- Test backup/recovery, session invalidation, version-pinned upgrades,
  concurrency, monitoring, and rollback in an isolated environment.
- Independently review data governance, clinical validation and security
  before processing real clinical information.

The included tests exercise the WSGI trust boundary with synthetic gateway
assertions, not an actual IdP or production deployment. This is not a security
certification, proof of clinical credentials, clinical approval or authorization
to deploy an evaluated clinical AI product.
