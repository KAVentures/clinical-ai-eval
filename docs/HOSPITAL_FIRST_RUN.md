# Local first-run browser setup

After installing the approved combined repository version, choose an existing
private parent directory owned by the operator. On POSIX it must have no group
or other permissions. On Windows configure approved ACLs separately.

    python -m caeval.hospital_first_run --root /private/caeval

Open the printed loopback address in a browser. Use the username `setup` and
random one-time session password printed in the terminal. The setup page shows
only directory layout statuses, not filenames, clinical records or credentials.
No GET request creates directories or launches anything. Check the approval box
and submit to initialize or reuse the three local workbench directories:
workspaces, staging and comparisons. A changed layout requires reloading the
page. Unsafe directory paths are refused, not repaired automatically.

On successful initialization the setup listener closes before the native local
workbench starts. Open the new address printed in the terminal using the new
operator credentials. The setup password is never transferred to the workbench.
If the native launch fails, the terminal reports refusal; the initialized
layout remains available for a later explicit restart. Ctrl+C cancels setup
before submission, or stops the running local application as appropriate.

The institutional directory is diagnostic only. This wizard never provisions
accounts, initializes institutional storage, migrates evidence or launches
an institutional service. Follow HOSPITAL_LAUNCHER.md for institutional setup.
The private parent directory is selected on the command line; the browser
cannot select arbitrary filesystem paths or overwrite existing files.

This is a graphical first-run workflow, not an OS installer or proof of
institutional readiness. It uses the same trusted local operator boundary as
the workbench, including exact loopback host, session credentials, CSRF and
no-cache responses. Layout checks do not protect against a trusted local user
manipulating files concurrently, or verify Windows ACLs, data authenticity,
clinical performance or deployment authorization. Full integration CI and
real hospital environment validation remain necessary.

    python -m pytest -q tests_unit/test_hospital_first_run.py
