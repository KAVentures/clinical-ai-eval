# Institutional deployment templates

These files are examples for a Linux server with systemd and an approved WSGI
server. They are not an installer, production approval or a complete identity
provider configuration. See [the federation guide](../../docs/INSTITUTIONAL_FEDERATION.md)
for the exact trust boundary and acceptance checklist.

Provision the dedicated service account, private workspace, separate protected
gateway key and authorized reverse-proxy group. Use a pinned, reviewed offline
wheelhouse containing the application, its dependencies and the institution's
chosen WSGI server. The example uses Gunicorn, installed separately. The
application itself adds no network identity SDK dependency.

Adapt paths, resource limits, identity policy and socket permissions. Never
start evaluation/collection workers under the browser service's identity.
The example restricts the browser service to Unix sockets; validate OS support
and the final effective sandbox. The reverse proxy is responsible for TLS,
upstream authentication, request limits and rate controls.

There is deliberately no generic OIDC/SAML proxy recipe that assumes a
particular product's header semantics. The upstream identity gateway must
supply a verified stable subject, exact issuer and actual MFA assurance on
every request, after stripping client-supplied identity headers. Configure and
test this with the selected institutional identity provider before exposure.
