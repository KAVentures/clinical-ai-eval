# Offline installation, version-pinned upgrades and rollback

This procedure is designed for a hospital-managed server; clinicians use a
normal browser and install nothing. The release builder and installer require
only the Python standard library until the approved application wheels are
installed. This is a pilot deployment procedure, not a security accreditation
or clinical validation.

## 1. Build a release on an approved connected build system

Use a clean checkout of the exact reviewed commit and the same interpreter
implementation, Python minor version, OS platform, architecture, ABI and
libc version as the target server. Prepare a separate wheelhouse for each
target. Install build tooling through the institution's approved channel.

    python tools/bundle_data.py
    python -m build --wheel
    python -m pip download --only-binary=:all: --dest approved-wheels dist/*.whl

Resolve dependencies once on the approved build system. Review the resulting
exact dependency versions, wheel contents, provenance, licenses and
vulnerability findings according to local policy. Add any approved WSGI server
and its complete dependency closure to the wheelhouse before sealing it.
The default build includes application runtime dependencies, not a specific
identity gateway or WSGI server. No unreviewed extras are installed implicitly.

Create the inventory, using the actual application version and full commit:

    python tools/offline_release.py prepare approved-wheels \
      --version 0.22.1 --source-commit FULL_40_CHARACTER_COMMIT

The release ID hashes a canonical manifest containing the target fingerprint,
exact distribution names and versions, every wheel filename, byte size and
SHA-256 hash, plus the stated source commit. One wheel per distribution is
required. The source commit is a declared provenance field, not a proof that
the wheel was built from it. This is not a reproducible-build guarantee.

Record the release ID in a separately authenticated institutional change
record or approved artifact-signing system. Independently authenticate the
installer script itself and the Python interpreter. Transfer the complete
wheelhouse and reviewed installer via approved media. A manifest and wheels
that arrived together are not independent proof of authenticity.

## 2. Verify and install offline

On the isolated target server, using the exact intended interpreter:

    python offline_release.py verify /approved/wheels \
      --approve-release APPROVED_RELEASE_ID

    python offline_release.py install /approved/wheels \
      --approve-release APPROVED_RELEASE_ID \
      --destination /opt/caeval/releases/APPROVED_RELEASE_ID \
      --confirm-trusted-wheels

The installer verifies the approved ID, strict inventory, target fingerprint,
wheel metadata, safe archive paths and content hashes. It copies approved
bytes to private staging, creates a fresh venv at its final location, and
installs only those local wheel paths with no index, dependency resolution,
source builds or automatic download. Bootstrap pip comes from the locally
installed Python interpreter's ensurepip bundle. The installer rejects
existing destinations and does not modify the active service or workspace.

It checks installed distribution versions, pip dependency consistency,
application import location, and the installed inspect, families and
capability-consistency CLI commands. An installation receipt is published
only after those checks pass. On a handled installation failure the new
directory is removed; the previous release is untouched. An interrupted
process can leave an unmarked incomplete destination; investigate before
removing it, and never activate it. Keep the release and its parent under
trusted IT control.

Wheels are executable software. A checksum does not make malicious code
safe. The no-index/no-deps pip options and reduced child environment do
not constitute an OS network sandbox. Enforce offline network policy and
perform the actual installation under an approved unprivileged build or
service identity. The installer does not install the reverse proxy, create
clinical accounts, configure external identity, or activate a service.

## 3. Prepare an upgrade or rollback

Retain each approved wheelhouse, installation directory and authenticated
release ID. Never move a venv or upgrade the active one in place. Before
activating a retained release, recheck it:

    python offline_release.py check-install /approved/wheels \
      --approve-release APPROVED_RELEASE_ID \
      --destination /opt/caeval/releases/APPROVED_RELEASE_ID

This rechecks the receipt, package versions, dependency consistency and
installed CLI. It does not establish integrity of every installed executable
byte; keep installed directories immutable and use institutional artifact
verification or reinstall from approved wheels when uncertain.

Before switching versions, stop the browser, schedulers, brokers and all
evaluation/collection workers. Resolve running jobs. Create and verify a
stopped-service backup using the approved version's recovery procedure.
Run the new release against an isolated restored copy of the workspace and
complete its deployment preflight, permission checks, synthetic evaluation
rehearsal and recovery acceptance. Do not overwrite the old workspace.

Scientific artifact verification depends on pinned definitions and tool
versions. A new release may invalidate the current-version verification of
older evidence even if that evidence was valid under its original definitions.
Keep the original package, manifest and exact approved release; never rewrite
old hashes to appear compatible. Re-run evaluations under the new version when
appropriate, and do not combine incompatible evidence.

After authorized acceptance, IT changes the service executable and workspace
configuration to the newly approved installation and accepted workspace.
If acceptance fails, stop all services, recheck the previous installation
and restore the verified pre-upgrade checkpoint to a fresh destination.
Restore the matching previous service configuration. Do not point the old
binary at potentially migrated or changed state. Revoke restored sessions
and rotate any external agent broker generation credentials before enabling
access. Re-run acceptance before returning the service to staff.

The script deliberately never changes a running service, performs a schema
migration on live data or claims a production deployment has been approved.
Actual hardware/OS compatibility, identity-provider integration, network
isolation, recovery and clinical governance acceptance are institutional
responsibilities.

## Verification

The existing wheel CI job now builds a real wheelhouse, seals and installs it
using this offline tool, then runs the installed CLI and the canonical
self-service rehearsal outside the source checkout. Unit tests cover exact
approval, inventory drift, content tampering, target mismatch, unsafe archives,
failed-install cleanup, no-overwrite behavior and retained-install checks.
CI on the connected runner is not proof of physical air-gap isolation; repeat
the acceptance steps in the institution's actual isolated environment.
