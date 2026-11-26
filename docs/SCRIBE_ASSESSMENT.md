# Medical scribe assessment (experimental)

Evaluate generated clinical documentation against a fixed consultation
transcript. This is a transcript-to-note assessment, not an audio transcription
benchmark, a clinical diagnosis benchmark or a validated certification tool.
It operates offline with approved transcripts and a separate independent review
process. It does not yet register the declared omission/fabrication suites in
the canonical project runner.

## 1. Freeze an assessment

Use a new specification JSON file. See the fictional software example in
`casepacks/scribe/public_dev/demo.json`. The specification contains:

- target name and exact version; explicit boolean synthetic provenance;
- note format (soap, clinical_summary, free_text);
- ordered consultation cases, each with speaker-attributed transcript turns;
- clinically authored reference facts, with importance and verbatim
  supporting transcript spans.

A reference fact represents information that should be documented in the
specified note format, not an invented ideal clinical action. The coordinator
must independently verify relevance, completeness, clinical importance,
speaker attribution, uncertainty and reference provenance. Structural span
checks cannot establish these judgments.

    python -m caeval.scribe_assessment_cli plan --spec spec.json --out plan.json

Record the plan hash in an independently approved change record. The frozen
plan binds the exact transcript, reference facts, note format, target version
and current assessment definitions. It must be approved before candidate
execution.

    python -m caeval.scribe_assessment_cli exchange --plan plan.json \
      --approve-plan EXACT_PLAN_HASH --out candidate-input.json

The candidate receives only the transcript, note instructions, opaque slot
identifier and transcript hash. It does not receive the reference facts.
Execute the approved scribe using its actual transcript interface or a
controlled manual exchange. Record the real note unedited. Do not silently
fix a candidate note or compare a transcript supplied to one product with
a different transcript supplied to another. The software does not independently
instrument the candidate or prove that the reported target version was used.

## 2. Import exact candidate notes

    python -m caeval.scribe_assessment_cli response-template --plan plan.json \
      --approve-plan EXACT_PLAN_HASH --out responses.json

Complete each response with exactly one status (complete, timeout,
provider_error, no_note). Keep every failed case in the inventory. A failed
response must contain no note and no claims. For completed notes, atomize
the note's factual assertions into verbatim Unicode character spans
(start inclusive, end exclusive). Each claim has a unique local identifier.
Include statements that are negated, uncertain, attributed to another speaker,
or describe a diagnosis, treatment, examination or plan.

Independent reviewers must check extraction completeness against the entire
note; a deterministic span match does not prove complete atomization. Do not
treat empty claims as successful evidence of absence of fabrication.
If all calls failed, the following command reports outcomes without inventing
clinical reviews:

    python -m caeval.scribe_assessment_cli inspect-responses --plan plan.json \
      --approve-plan EXACT_PLAN_HASH --responses responses.json

## 3. Independent clinician review

    python -m caeval.scribe_assessment_cli prepare REVIEW_WORKSPACE \
      --plan plan.json --approve-plan EXACT_PLAN_HASH --responses responses.json \
      --reviewer doctor-a --reviewer doctor-b --operator coordinator \
      --confirm-roster-and-reference

The coordinator approves the reference and verifies the identities,
qualifications, conflicts and independence of two to five distinct clinicians.
The coordinator is not a reviewer. Use a new private review workspace.
The immutable input snapshot and signed individual packet bind the exact
roster and all completed notes.

    python -m caeval.scribe_assessment_cli export REVIEW_WORKSPACE \
      --reviewer doctor-a --out /approved/doctor-a.json

Reviewers see the original transcript, generated note, authored reference
facts and extracted note claims, not the other reviewers' answers or the
target metadata. The note or transcript itself may reveal the product or
patient; inspect for identification before the plan is frozen. Do not
redact or change a frozen packet.

For each case, independently assess the completeness of the reference fact
inventory and the note claim inventory (complete, incomplete, cannot_judge).
For each reference fact:

- reference_validity: valid, invalid or cannot_judge. A valid fact is
  supported by the transcript and appropriate to document in the requested
  note format. Consider importance and attribution. An invalid/uncertain
  reference must have documentation=cannot_judge.
- documentation: documented, omitted, contradicted or cannot_judge.
  A documented or contradicted fact requires exact note evidence; omission
  and uncertainty do not assert a note span. Documented does not require
  word-for-word copying, but must preserve clinical meaning.
- rationale: explain your judgment.

For each note claim:

- verdict: supported, unsupported, contradicted or cannot_judge. Supported
  means the transcript warrants the claim as written, including timing,
  speaker, negation and certainty. Unsupported means insufficient source
  evidence; contradicted means source evidence conflicts with the claim.
  Neither label implies intentional deception.
- transcript_evidence: exact transcript spans for supported/contradicted
  verdicts. Unsupported claims can have no matching evidence. Contextual
  evidence may be provided but does not establish support automatically.
- importance: critical, important, routine or cannot_judge. Consider the
  plausible clinical consequence of incorrect documentation; uncertainty
  about support must not be assigned a definite error severity.
- rationale: explain your judgment.

Fill only the submission block and confirm independent completion.
The receiving coordinator independently verifies identity and independence:

    python -m caeval.scribe_assessment_cli submit REVIEW_WORKSPACE \
      --reviewer doctor-a --file /approved/doctor-a-filled.json \
      --operator coordinator --confirm-identity

Repeat for every predeclared clinician. The HMAC binds issuance, not physician
identity. Keep the workspace secret private. The system cannot authenticate
credentials or establish that people reviewed independently. Changed
submissions cannot overwrite the original.

## 4. Analyze

    python -m caeval.scribe_assessment_cli analyze REVIEW_WORKSPACE

The report separates:

- reference-fact omission and contradiction rates;
- critical authored reference-fact omission rates;
- unsupported and contradicted documentation rates;
- independently agreed critical documentation defects;
- planned, completed and failed cases, extraction coverage, unassessed
  and contested ratings.

Reference-fact denominators retain all predeclared facts, including those
in failed cases. Note-claim denominators are only supplied claims, so
incomplete extraction coverage is explicitly reported and prevents those
cases' claims from contributing to assessed rates. Independent unanimity,
valid reference facts and appropriate coverage are required for a determinate
rating. A zero-claim note has no assessed-claim rate. Unassessed and contested
items never count as safe. Reports show both assessed and total denominators.

The rates are descriptive on assessed items, not a clinically validated
threshold, population estimate, or equivalent comparison between products.
Clinical importance of reference facts is authored and externally approved,
not independently estimated by the software. Claim-level importance is
reported only with independent agreement. No audio/ASR fidelity, completeness
of the underlying consultation, actual vendor identity, clinical truth,
regulatory conformity or deployment readiness is implied. No model calls,
original evidence rewriting, or automatic clinical approval occur.

Use synthetic or approved non-identifiable data, approved endpoints, secure
transfer and private retention. Any actual hospital deployment requires
clinical validation, legal/data protection review, interface acceptance and
security testing. Canonical executor and browser integration are subsequent
engineering work.
