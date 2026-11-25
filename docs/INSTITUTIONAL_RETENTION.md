# Governed retention and deletion

The institutional administrator can approve versioned retention policies in the
browser. There is no default synthetic-evidence deletion schedule. Enter the
expired-session grace period, obsolete login failure retention, optional
synthetic FHIR rehearsal retention, a global maintenance hold and individual
synthetic-report holds. Every revision is bound to the prior revision and an
audit event. The rationale must reflect actual organisational policy.

## Scope

The maintenance engine can delete only expired sessions, obsolete login failure
records (never within the authentication failure window), and old synthetic
FHIR rehearsal payloads that are not on hold, were not exported through the
institutional evidence export route, and appear in no frozen committee snapshot.
An immutable-metadata tombstone and audit event retain explicit evidence of
deletion. The same synthetic report cannot be silently re-imported under a
different operator. No automatic clinical-evidence deletion is supported.
Clinical records, physician assessments, raw model outputs, vendor documents,
committee snapshots, audit and backups must be handled under separately approved
institutional processes. The browser cannot execute deletion.

## Offline maintenance

IT must stop the gateway, all evaluation/collection workers, and trusted CLI
writers. Verify institutional backup and hold obligations before proceeding.
Use the existing offline workspace integrity checks:

    clinical-ai-eval institution retention-preview /approved/workspace --confirm-services-stopped

Review the returned policy, counts, protected and held IDs and exact eligible
synthetic IDs. No bearer tokens or login failure identifiers are returned.
If approved, pass the exact preview timestamp and digest:

    clinical-ai-eval institution retention-apply /approved/workspace --confirm-services-stopped --as-of TIMESTAMP --approved-digest SHA256 --confirm-deletion

The tool recomputes the inventory under a SQLite write lock and refuses changed
policy, eligibility or preview digest. Preview timestamps expire after 24 hours.
All supported deletions and audit events commit together. A policy can be
revised through the browser before shutdown, but not silently changed during
maintenance. Re-open the browser after successful maintenance and inspect the
restricted deletion history in the committee inventory.

This is logical database row deletion, not certified physical erasure. SQLite
secure_delete is enabled for this transaction, but old backups, exported files,
storage snapshots, filesystem remnants and external copies are outside its
scope. Service shutdown is operator-attested; the tool cannot prove all other
processes have stopped. Retention and deletion of actual clinical evidence,
legal holds across all data classes, backup expiration, data-subject requests,
and formal regulatory compliance require additional institutional governance
and software work.

Synthetic regression tests:

    python -m unittest tests_unit.test_institution_retention
