# Local assessment workspace selector

Use one loopback browser session to create and reopen multiple private
assessment workspaces. This is a trusted single-operator tool, not a
multi-user institutional workspace service or a clinical release gate.

## Launch

Create two distinct private directories, one for persisted workspaces and
one for staged source files. Neither may be inside the other. On POSIX,
use resolved owner-only directories (0700); on Windows apply restricted
ACLs. Stop other setup/review processes using these workspaces.

    python -m caeval.assessment_workspace_web --root /private/workspaces --staging-root /private/staging

Open the printed loopback address, authenticate with username `operator`
and the random session password printed in the private terminal. Do not
forward the port, share credentials or capture the password in logs.

## Create and switch

Choose a short lower-case workspace name using letters, digits, hyphens or
underscores. The selector opens setup with the matching staging subdirectory
(which it creates if absent). Place the fixed-name files described in
ASSESSMENT_SETUP_WEB.md in that subdirectory. Preview and confirm the plan,
then import vendor evidence and collect outcomes. The actual workspace is
created only when the plan is confirmed. Existing workspaces are never
overwritten. Retain the confirmed plan, procurement head and eventual
report digest in a separately controlled record.

The Workspace selector link returns to the list from setup. From the review
dashboard, use the browser's back navigation or open /workspaces at the same
loopback address. Selecting another workspace discards uncommitted previews
and in-memory reports, but leaves committed files and events intact. Each
successful selection creates fresh child action tokens; forms in older tabs
cannot submit actions to the newly selected workspace. There is only one
active workspace per server process, not one per browser tab.

## Resume

The selector lists names of immediate, non-symlink directories under the
workspace root. It does not read their plans, keys or evidence to populate
the list, and a listed name does not imply a valid workspace. Enter the
independently retained plan digest and current procurement head. For a
completed collection also enter its retained report digest. The matching
staging directory must exist. The resume engine revalidates the original
workspace and refuses incomplete or changed states. A failed selection
leaves the previously active workspace intact. See ASSESSMENT_RESUME_WEB.md
for checkpoint recovery and completed-workspace restrictions.

## Boundaries

The inventory is limited to 200 immediate directory entries, and selection
forms to 4096 bytes. No recursive search, arbitrary path input, symlink
navigation, deletion, rename, workspace migration or automatic checkpoint
adoption is exposed. The roots and all local files must remain under the
trusted operator's control. The local browser is not TLS, an authentication
service, a hardened file loader for untrusted databases, or protection
against a compromised local process. Session credential, exact host and
peer, origin, CSRF, no-cache and content policy checks are inherited from
the local browser design. Reports are restricted and not deployment approval.

The existing setup/resume CLI and underlying scientific engines remain
unchanged. This feature does not remove the need to stage structured local
files or independently retain checkpoints. Native forms for scientific
protocols and institutional SSO remain separate work.

Run synthetic tests:

    python -m pytest -q tests_unit/test_assessment_workspace_web.py
