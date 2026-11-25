# Formal offline clinical review handoff (experimental)

This is a trusted-operator workflow for a completed verified **generic
paired-text** assessment. It reuses the existing canonical adjudicator,
review manifest and platform-issued HMAC packets. It is independent of
the institutional browser's exploratory account-bound reviews. Those
submissions are never silently converted into formal clinical evidence.

## Before review

An authorised institution must independently confirm professional
credentials, identities, relevant clinical expertise, conflict-of-interest
and independence from the product vendor, hazard authors and defect
implementers. Record the institution's own verification documentation in
approved systems. The software records the operator's explicit attestation;
it does not perform credential checks or authenticate a returned document's
author. Use approved non-identifiable cases and secure offline exchange.

Predeclare two to five independent safety reviewer IDs and a **different**
validity reviewer before anyone receives packets. The issuing and receiving
operator cannot be one of those reviewers. Any predeclared restrictions in
the original assessment remain in force. Use a new handoff for a different
roster or source version, not an edited handoff.

    python -m caeval.formal_review_cli prepare /srv/caeval/workspace \
      --job JOB --family missing_information \
      --reviewer clinician-a --reviewer clinician-b \
      --validity-reviewer clinician-c --operator coordinator --confirm-roster

The result gives a handoff ID. The source package and review queue are
reverified; captured source artifacts and issued packets are frozen in a
private derivative workspace. The per-handoff HMAC secret stays on the
trusted server. A synthetic/non-L1 source is automatically marked synthetic
and cannot be relabelled as genuine clinical evidence.

## Independent review and validity

Export one packet per reviewer to an approved destination:

    python -m caeval.formal_review_cli export /srv/caeval/workspace \
      --handoff HANDOFF --reviewer clinician-a --out /approved/a.json

Repeat separately for the other safety reviewer(s) and validity reviewer.
Do not send a combined dossier or automated scores to independent
reviewers. Safety packets expose only case-as-shown, response and opaque
slot IDs. Validity packets expose the original and perturbed case, with
the canonical validity questions. The coordinator should check the packet
for accidental identifying text before distribution.

Reviewers fill only the submission block. Each safety verdict is safe,
unsafe or cannot_judge. Each validity question is yes, no or cannot_judge;
notes are optional. Set the independence confirmation only after independently
performing the review. Do not change the request, packet or slots.

The coordinator separately verifies the source of each returned file and
the reviewer's identity and independence before importing it:

    python -m caeval.formal_review_cli submit /srv/caeval/workspace \
      --handoff HANDOFF --reviewer clinician-a --file /approved/a-filled.json \
      --operator coordinator --confirm-identity

A different submission for an already received reviewer is refused; an
identical submission is idempotent. There is no automatic clinical
identity verification and the HMAC signs the issued packet binding, not
the reviewer's filled verdicts. Account names alone do not prove clinical
qualifications.

## Disagreement resolution

After all independent submissions and the validity submission are locked,
the coordinator can export a separate consensus packet containing only tied
cells:

    python -m caeval.formal_review_cli consensus-export /srv/caeval/workspace \
      --handoff HANDOFF --out /approved/consensus.json

Original reviewers discuss only these tied cells, document the agreed
verdict and rationale, and explicitly confirm the consensus. If no
agreement is possible, record cannot_judge, leaving the cell unresolved.
The coordinator verifies their agreement externally before importing:

    python -m caeval.formal_review_cli consensus-submit /srv/caeval/workspace \
      --handoff HANDOFF --file /approved/consensus-filled.json \
      --operator coordinator --confirm-consensus

Consensus is bound to the exact independent submission digests; it never
changes independent labels or inter-rater reliability.

## Analysis

    python -m caeval.formal_review_cli status /srv/caeval/workspace --handoff HANDOFF
    python -m caeval.formal_review_cli analyze /srv/caeval/workspace --handoff HANDOFF

The canonical adjudicator runs offline against a private derivative copy
with the originally issued packets, independently submitted verdicts and
any locked consensus. A negative or indeterminate answer to a canonical
positive perturbation-validity gate blocks formal validity; no cell is
silently removed or counted as passing. The report retains canonical
coverage, disagreement, judge operating points and gate outcomes. It also
records the source manifest, roster and submission digests. The result
does **not** change the original assessment or its claim authority, and is
not a procurement or deployment decision. Canonical L2 gate output is
only an audited-scope statistical/conformance result, conditional on
external identity and independence attestations; it is not clinical
validation. Existing clinical study protocols remain separate.

Do not put the handoff directory or signing secret in a reviewer export,
MCP snapshot, public repository or external service. Protect all derivative
data under institutional retention, access and recovery policies. A trusted
filesystem administrator can alter local files and reissue HMAC packets;
this is not PKI or tamper-proof audit. Browser-based formal issuance and
submission, institutional identity federation and production acceptance
are future work.

## Selected-queue judge calibration

Formal analysis additionally emits selected_queue_calibration, a descriptive
comparison with independently determined human majority labels. The locked
review queue is enriched and is not a random clinical population sample.
Every reviewed opportunity is counted once in an exclusive coverage category:
invalid/indeterminate human validity, insufficient determinate reviewers,
no majority of the frozen independent roster, incomplete automated evaluation,
or complete automated evaluation. Per-judge and panel confusion counts are
computed only for paired valid independent human references and available
binary automated labels. Incomplete automated evaluations remain in the
human-reference denominator as missing, alongside missing judge labels.
Sensitivity/specificity missing-label bounds are
shown (not confidence intervals). Case-cluster counts are explicit. Consensus
never changes this independent calibration reference. No claim promotion, new
clinical validity threshold or population extrapolation is made. The canonical
adjudication report and original source package remain separate and unchanged.

## Prospective full-variant validation cohorts

The institutional browser's Prospective validation cohorts page allows an admin
 to freeze a separate full-variant cohort for one canonical generic paired-text
family and exact approved pack. Freeze before preparing the candidate exchange;
choose two to five independent safety reviewers and a distinct validity reviewer.
The complete variant inventory is determined without candidate outcomes. The
admin explicitly attests no known prior exposure; local ordering does not prove
external non-exposure. Attach one exact verified completed job, then assigned
reviewers independently lock blinded answers and the administrator attests each
submission's identity, expertise and independence outside the software. The
report retains invalid, uncertain and missing results and reuses independent
reference calibration. It does not alter or promote the original run. Full pack
coverage is not a population-representative sample, and only one family is
included per cohort. Do not enter real patient identifiers.

The prospective guarantee applies only to the full case inventory and reviewer roster. The completed job and evaluator configuration are selected afterwards and are not prospectively fixed by this protocol. Report this limitation in any research use; do not treat the cohort as a confirmatory preregistered validation study.

### Panel-bound cohort revision

New cohorts require an approved panel ID and freeze the exact panel bytes, parsed evaluator configuration digest, and scientific definition hashes before exchange preparation. After response collection and assessment preparation, reserve a single dedicated deterministic job; its committed institutional approval is required by the worker before execution. Only that completed job can be attached. Failed or cancelled reserved jobs cannot be replaced or retried. Legacy cohorts retain their weaker explicitly labelled protocol. Candidate submission selection is not prospectively bound; local ordering and the reserved job do not rule out prior tests elsewhere, selective submission, runtime provider changes or unrecorded exposure. The earlier limitation about evaluator and job selection applies only to legacy cohorts. No clinical claim is promoted.

### Connector-bound cohort revision

A staged single-turn candidate connector can optionally be frozen at cohort creation. A dedicated collection ID is reserved; a committed audit authorizes exactly one worker attempt, and its receipt is sealed before handoff. Only that exact complete submission, with every planned call attempted and provided, may enter the dedicated evaluation job. Incomplete or failed attempts cannot be substituted, and do not yield calibration. The worker still requires explicit execution approval. This local control does not prove absence of external tests, vendor state changes, or administrator tampering. Cohorts without a frozen connector retain the earlier weaker claim.
