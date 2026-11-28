# Hospital application launcher

The combined institutional and local research stacks have distinct data stores,
credential models and deployment assumptions. This launcher provides a single
module entry point, not unified authentication or a shared database.

Install the approved integrated repository version following its existing
Python installation instructions. From an existing private parent directory,
use:

    python -m caeval.hospital_launcher init-local --root /private/caeval

On POSIX the parent must be owned by the operator and mode 0700 or stricter.
On other platforms enforce approved ACLs; POSIX permission checks do not
establish Windows ACLs. The command creates only workspaces, staging and
comparisons, with no overwrite, clinical evidence migration or institutional
account creation. Existing directories are checked before creation; an
interrupted initialization may be rerun after inspecting the layout.

    python -m caeval.hospital_launcher doctor --root /private/caeval
    python -m caeval.hospital_launcher local --root /private/caeval

Doctor reports directory readiness only, without reading evidence, databases
or credentials. Local starts the existing multi-vendor browser with all three
explicit directories. Its session password, local loopback binding, evaluation
workflow and background worker behavior are unchanged. See
ASSESSMENT_MULTIVENDOR_WEB.md. No network service is exposed by this launcher
beyond the existing local browser.

## Institutional gateway

An administrator must independently provision the approved institution buyer
workspace at the institution child directory, including accounts and
permissions using the existing institutional CLI. Do not copy local workbench
records into it. The launcher does not create that directory or provision
accounts. Review the existing institutional lifecycle preflight, backup and
reverse proxy documentation before operating the gateway.

    python -m caeval.hospital_launcher institution --root /private/caeval --origin https://hospital.example --port 8767

The launcher requires an exact HTTPS origin without path, credentials, query
or fragment, and invokes the original institution gateway on its existing
loopback listener. TLS, network access, approved proxy configuration and
institutional authentication remain the responsibility of that gateway and
the deployment administrator. The local workbench is not routed through the
institutional gateway and its operator password is not an institutional login.

The profiles do not start each other, synchronize files, assume clinical
readiness or grant permission to deploy clinical models. Root and child
directories must be real private directories, not symlinks. This does not
protect against a trusted administrator manipulating directories concurrently,
mount aliasing or external processes. Use separate managed service accounts and
storage boundaries as required. No system installer, SSO integration,
clinical validation or release approval is provided.

    python -m pytest -q tests_unit/test_hospital_launcher.py
