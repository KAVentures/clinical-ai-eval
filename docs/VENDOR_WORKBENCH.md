# Local vendor evidence workbench

The workbench combines the offline evidence submission and buyer/vendor
clarification modules into a persistent, single-operator CLI and Python API.
It is an administrative workflow, not a clinical validation or purchasing
approval engine. It does not replace the institutional browser, document
review, authentication, or deployment gates.

## Create a workspace

Use a private, trusted local filesystem directory (not a shared or remote
filesystem). For POSIX, for example:

    umask 077
    mkdir -m 700 procurement-local

Freeze a buyer evidence request using the workflow in VENDOR_SUBMISSION.md.
Record the request digest independently. Then initialize a new database:

    python -m caeval.vendor_workbench init --db procurement-local/workbench.db \
        --request request.json --expected-request REQUEST_DIGEST \
        --requester buyer-operator --reviewer buyer-reviewer \
        --representative vendor-representative

The printed root is also the initial head. Preserve both in a separately
controlled local record. Roles are distinct declared actor tokens, not
authenticated accounts. A trusted operator verifies identities and authority
outside this program; do not expose this CLI or API to untrusted callers.

## Import version-specific evidence

After authenticating the sender and applying institutional quarantine
procedures, record the opaque submission:

    python -m caeval.vendor_workbench import --db procurement-local/workbench.db \
        --root ROOT --head CURRENT_HEAD --archive submission.zip

This verifies the exact original request scope and stores the archive and
receipt together. The printed digest is the new head. Replace CURRENT_HEAD
with that value in the controlled record. Duplicate archive bytes are
rejected. Revised complete submissions can replace the current documentary
coverage projection; previous submissions remain recorded.

The workbench never extracts, executes or interprets evidence attachments.
It does not perform malware scanning or sender authentication. Do not treat
an internally consistent archive as evidence of clinical truth.

## Record questions and responses

Create a body JSON following VENDOR_CLARIFICATION.md and retain the actual
question, response or review document separately as an exact local file.
The evidence_sha256 must match those bytes.

    python -m caeval.vendor_workbench clarify --db procurement-local/workbench.db \
        --root ROOT --head CURRENT_HEAD --body event.json --artifact question.txt

The same command records a vendor response, acceptance, rejection or reopening.
Use the distinct declared actors and reference the exact current response
digest when reviewing. The new head is printed after the transaction commits.

## Export a restricted status report

    python -m caeval.vendor_workbench status --db procurement-local/workbench.db \
        --root ROOT --head CURRENT_HEAD --as-of 2026-12-06 \
        --output procurement-local/status.json

The program recomputes all archive receipts, the interleaved event chain,
artifact hashes and underlying clarification transitions. The report retains
submission history digests, the latest documentary coverage, unresolved
clarifications and overdue counts. It does not contain raw attachments.
An accepted clarification does not waive a missing mandatory document.
Even if every document is supplied and every clarification is resolved,
substantive evidence review and institutional approval remain outstanding.
All reports are non-decision-grade, not deployment-authorized and not
automatically publication-safe.

## Operational and trust boundaries

SQLite transactions and an expected-head comparison serialize cooperating
writers. A stale writer is refused rather than silently creating a fork.
The external checkpoint is not updated transactionally by the program:
if the process stops after a database commit but before the operator saves
the printed head, do not bypass the expected-head check or assume the
latest database content is authoritative. Reconcile under institutional
supervision against the original inputs and retained backup or journal.
Back up the database using a consistent SQLite snapshot together with its
independently retained checkpoint.

The database contains opaque evidence bytes; keep it private and follow
institutional retention, encryption-at-rest and backup rules. POSIX requires
an owner-only directory and database file; Windows users must enforce
appropriate ACLs. Use an actual resolved directory path. This program is
not a hardened database loader for arbitrary received SQLite files, an
authentication service, an immutable storage system, or a digital signature
scheme. A party that controls both the database and external checkpoints
can rewrite history.

The cap is 300 history events and 96 MiB of artifact data per workspace,
with individual clarification artifacts limited to 256 KiB and submissions
subject to the original archive limits. Every operation replays history
rather than trusting a cached projection. Larger workloads require a
separately designed institutional storage integration. Keep output files
restricted; the CLI creates report files exclusively and never overwrites
one.

No network, shared router, database migration or new dependency is required.
The workbench depends on the original vendor submission and clarification
modules from PRs #123 and #124. Run the synthetic tests together:

    python -m pytest -q tests_unit/test_vendor_submission.py \
        tests_unit/test_vendor_clarification.py tests_unit/test_vendor_workbench.py
