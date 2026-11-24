# Formal clinical review in the institutional browser

This workflow is distinct from exploratory physician reviews and from the
trusted offline CLI handoff. It uses the same canonical formal-review engine,
immutable issued packets, independent verdicts and perturbation-validity
requirements. It does not authenticate professional credentials or establish
clinical independence by itself.

An administrator opens **Formal independent clinical review**, selects a
completed generic paired-text evaluation job and family, and predeclares
two to five distinct active reviewer accounts plus a different validity
reviewer. The administrator must externally verify credentials, expertise,
conflicts of interest and independence before freezing the roster. One
browser handoff per job/family is permitted. A new evaluation job is required
for a changed roster. Browser queues are limited to 400 items (larger queues
require the approved offline workflow).

Each assigned reviewer signs in and opens their own packet. They see blinded
case/response pairs (or original/perturbed case pairs for validity review),
not automated scores, other reviewers' verdicts, or the identity of the
other reviewers. Reviewers confirm independent completion and lock their
answers. No replacement or post-submission editing is supported.

The administrator sees submission status and a digest, not the independent
verdicts or notes. They must independently verify the identity, credentials
and independence of the reviewer for that exact submission before importing
it into the canonical formal-review store. The system records the explicit
attestation; it does not make it on the administrator's behalf.

After all independent and validity submissions are attested, the administrator
can compute the canonical adjudication. If there are tied cells, the original
reviewers must discuss them through an approved institutional process. The
administrator records only their confirmed consensus and rationale in the
separate consensus form; cannot_judge leaves a case unresolved. Independent
verdicts and agreement measures remain unchanged. Both independent submissions
and consensus require an exact matching authenticated browser attestation
ledger entry; offline imports do not satisfy browser provenance. Analysis
rechecks these bindings before returning a result.

The report is conditional scientific evidence. Synthetic source packages
cannot become real clinical evidence; negative or indeterminate validity
findings block the formal gate. The original assessment and claim authority
are not upgraded or modified. This workflow does not approve procurement,
deployment or patient care.

The registry and pending submissions are stored in the institutional access
database. Canonical packet files are stored in the private formal-review
workspace. Browser writes are serialized through SQLite, but database and
filesystem operations are not a single transaction. After a crash or failed
attestation, trusted IT must reconcile the stored registry and canonical
files before resuming. Do not delete, reissue or edit issued packets to
recover. Do not mix offline CLI mutations with browser-owned handoffs.
Include the private review directory and access database in institutional
retention and stopped-service recovery planning. No clinical or reviewer
data should be exposed to agent metadata tools.

Actual clinical independence, identity-provider integration, clinical
validation, operational security and full browser acceptance remain required.
