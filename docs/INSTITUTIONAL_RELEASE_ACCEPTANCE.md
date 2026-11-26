# Institutional release acceptance

This is an acceptance runbook, not a completed clinical certification or
production release declaration. Record the exact source commit, build,
dependency wheelhouse, institution policy version, approver and evidence
for each gate. Do not activate a clinical service merely because CI passes.

| Gate | Required verification |
|---|---|
| Build | Seven CI checks pass on the exact integrated head: Python 3.9/3.11/3.12, capabilities, smoke, fixtures, offline wheel installation. |
| Offline installation | Independently approve the release binding and exact target platform; install into a fresh environment; verify external installed CLI, dependencies and rollback. |
| Identity | Test actual institutional IdP/reverse proxy, MFA enforcement, spoofed gateway headers, expiry, logout, disable and restored-session revocation. |
| Permissions | Exercise admin/operator/observer/reviewer/vendor boundaries and CSRF using independent accounts. |
| Single-turn candidate | Stage exact product version and approved prompt pack; execute approved local collection; verify incomplete collection never scores. |
| Multi-turn candidate | Run the canonical patient runner with an approved product and full applicable stress inventory; inspect trace provenance and transport failures. |
| External gateway | Independently approve egress, hostname/IP, model, synthetic data scope, credentials, retention and firewall policy; verify actual provider interoperability. |
| Physician review | Predeclare independent clinical rosters, verify credentials outside the software, complete immutable blinded submissions and attestations. |
| Patient-world validity | Review applicable source worlds before candidate testing, investigate negative/indeterminate references, link exact pack and verify nonoverlapping reviewer rosters. |
| Monitoring | Freeze a verified baseline and exact retest conditions; test missed intervals, incomplete calls, drift, judge participation, descriptive comparison and disable. |
| Escalation | Test serious risks, evidence invalidation, acknowledgement scope, revised notice identity and institution-owned notification routing; establish actual response coverage. |
| Recovery | Stop all workers, verify backup, restore into a fresh root; confirm copied sessions and agent grants revoked and monitoring schedules disabled. |
| Agent integration | Test actual client connection to the approved local MCP broker, expiry, scope, budget, revocation and metadata-only data exposure. |
| Clinical and regulatory | Independent review of intended use, clinical validity, representative cases, reproducibility, privacy, security, incident response and applicable regulatory requirements. |

The release provides software workflows for these checks, not proof that
all gates have been executed in a real healthcare institution. Treat
synthetic/mock evidence, experimental family maturity and unresolved
review findings accordingly. Keep independently authorized deployment
decisions separate from software reports.

## Committee dossier

Freeze the committee snapshot after evaluation and implementation review. Verify
that its lifecycle inventory contains exact assessment IDs and verified package
references, aggregate independent-review coverage, pilot revisions, linked
assessment integrity, and regression occurrence states. Review coverage is not
a verdict. The inventory omits reviewer identities, answers, transcripts,
incident descriptions and worker approval hashes. It is a point-in-time
inventory across separate stores, not a transactional deployment authorization.
After any changed source evidence, freeze a new snapshot; old snapshots remain
historical. Earlier snapshots without the inventory remain readable.
