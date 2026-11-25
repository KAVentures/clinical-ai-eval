# Independent patient-conversation clinical review (experimental)

This offline workflow complements generic paired-text formal review and the
existing canonical patient evaluation engine. It does not alter the scorer,
clinical claim authority, original assessment manifest, or patient-specific
canonical unit-review adjudication. It is an independent descriptive physician
review of the recorded conversation using the existing patient judge rubric.

A trusted operator must approve the exact canonical patient assessment manifest
hash, independently verify reviewer identities, professional credentials,
expertise, conflicts and independence, and predeclare two to five distinct
clinicians before issuance. No model or external network calls are made.
Use approved synthetic or non-identifiable case data and protected storage.

    python -m caeval.patient_review_cli prepare SOURCE_PACKAGE REVIEW_WORKSPACE \
      --approve-manifest APPROVED_HASH --reviewer clinician-a \
      --reviewer clinician-b --operator coordinator --confirm-roster

SOURCE_PACKAGE is the completed run_patient_red_flag evidence directory.
REVIEW_WORKSPACE must be a new private directory outside the source.
The workflow verifies current manifest definitions, the frozen judge input,
scored episode identities, transcript hashes and the mandatory review queue.
Every recorded episode is included (up to 400), avoiding selection solely by
automated failures. It does not establish that every possible scenario or
population is represented; incomplete source runs must be investigated.

Export each physician's own packet separately:

    python -m caeval.patient_review_cli export SOURCE_PACKAGE REVIEW_WORKSPACE \
      --reviewer clinician-a --out /approved/a.json

The packet contains opaque episode references, the conversation transcript and
facts disclosed before system turns. It omits ground truth, hidden facts,
stress metadata, inferred dispositions, automated ratings, candidate metadata
and other reviewers' answers. Transcript text itself might identify a vendor
or patient; inspect it through approved institutional processes before sharing.
Do not silently redact a frozen packet; create a new approved evaluation if needed.

Review the entire conversation against what was knowable before each system
turn. Use the definitions in prompts/patient_judge_prompt.txt. Each of the ten
existing safety and usefulness fields accepts yes, no or cannot_judge.
History adequacy is a positive field; the other fields describe concerns.
Record the observed final disposition (including none or cannot_judge) and
a rationale per episode. Do not infer the hidden diagnosis or allow a later
correction to erase an earlier unsafe commitment. Complete only the submission
block and confirm independence. The coordinator verifies identity externally:

    python -m caeval.patient_review_cli submit SOURCE_PACKAGE REVIEW_WORKSPACE \
      --reviewer clinician-a --file /approved/a-filled.json \
      --operator coordinator --confirm-identity

Repeat for every predeclared reviewer. Imports verify the unchanged packet
and exact answers and cannot overwrite a prior different submission. HMAC
protects the issued packet binding; it is not a physician's digital signature
or proof of credentials. Keep the packet signing secret private.

    python -m caeval.patient_review_cli analyze SOURCE_PACKAGE REVIEW_WORKSPACE

Analysis is available only after all independent submissions are received.
It reports per-axis counts, unanimous determinate ratings, disagreements,
indeterminate episodes and observed pairwise agreement. Missing/indeterminate
ratings never become negative safety findings. Agreement is descriptive,
not a chance-adjusted reliability estimate, statistical equivalence,
population inference, or ground-truth clinical validation. No automatic
consensus, clinical claim promotion, procurement decision or deployment
approval is provided. Synthetic provenance remains explicit. The original
source stays unchanged; retain both source and derivative under approved
institutional retention policies.

The browser integration, independent hidden-world validity review and
clinical qualification of the patient rubric are separate future work.
