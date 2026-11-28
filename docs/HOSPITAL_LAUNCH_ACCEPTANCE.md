# Installed-release local launch acceptance

The existing offline release installer validates wheel inventory, dependency
consistency and installed CLI commands (OFFLINE_RELEASE.md). This additional
check exercises local browser onboarding and the integrated product comparison
route using the exact installed interpreter. It is not a second installer or
an operating-system package.

After independently approving and installing the offline release, run from an
unrelated working directory using that release's interpreter (examples):

    /opt/caeval/releases/RELEASE_ID/venv/bin/python -I -m caeval.hospital_launch_acceptance

    C:\caeval\releases\RELEASE_ID\venv\Scripts\python.exe -I -m caeval.hospital_launch_acceptance

The command starts the installed first-run module in an isolated child Python
process, with a fresh owner-only temporary root and a reduced environment.
It connects directly to the advertised IPv4 loopback listener without proxies
or redirects, checks that unauthenticated requests are refused and that GET
creates no data, submits the actual setup form with explicit approval, then
checks the new operator credentials, the workspace selector and the matched
product comparison page. It checks that only the three empty local directories
were created and never starts the institutional gateway. No real clinical
workspace, model endpoint or user credentials are accepted. Child credentials
and terminal output are not included in the resulting JSON or error messages.
The child is terminated and the disposable directory cleaned up on ordinary
success or failure. An externally killed process may require IT cleanup.

The default startup wait is 30 seconds per phase; `--startup-timeout` accepts
1–120 seconds. HTTP requests have bounded socket timeouts and response sizes.
An exit code of 2 indicates a failed check; do not activate a release solely on
the basis of successful installation or this acceptance check. The installed
release must contain the reviewed first-run and comparison browser modules.

The harness is not a network sandbox, proof of installed executable integrity,
actual clinical evaluation, Windows ACL validation, production approval or
institutional identity/recovery acceptance. Perform the existing institutional
release acceptance separately. Use the actual target OS, approved interpreter
and independently verified wheelhouse. No native Windows or macOS installers
are provided here.

Unit tests exercise real first-run HTTP, an isolated virtual environment and a
minimal workbench test double, including a failed handoff. They do not prove
that the real integrated workbench runs; execute the command above against the
real approved installation as a separate release acceptance step.

    python -m pytest -q tests_unit/test_hospital_launch_acceptance.py
