# Controlled citation assessment (experimental)

This offline assessment implements distinct controlled-context probes and independent
claim-to-source review using the existing atomic citation scorer. It is a
new research CLI, not an automatic judgement of clinical correctness, and not
yet a registered canonical project executor or browser workflow. The observed
RAG trace contract is reused; controlled supplied source material must not
be mistaken for independently instrumented live vendor retrieval.

## Freeze the test protocol

Create a JSON specification with `target` (name, version), explicit boolean
`synthetic`, `sources` (source_id, text, version, status: current/superseded),
and `cases` (case_id, question, current_source, superseded_source,
distractor_source, ghost_source). The four source IDs must differ. The ghost
ID must not exist in the catalog. The current supporting, superseded and
distractor documents must have distinct texts. Independent clinical experts
must approve the clinical question, source versions, supporting references
and relevance of the distractor; structural checks cannot establish that.

    python -m caeval.citation_assessment_cli plan --spec spec.json --out plan.json
    python -m caeval.citation_assessment_cli prompts --plan plan.json \
      --approve-plan EXACT_PLAN_HASH --out prompts.json

The plan creates four distinct contexts per question: current supporting
document, explicitly requested nonexistent ID with no available document,
superseded document, and current distractor. Exported query IDs are opaque;
the prompt naturally discloses the supplied document and currency. Do not
interpret the four conditions as estimates of real-world prevalence.

## Run and import

Use approved candidate execution or manual exchange. Record every query,
including timeout/provider_error. Submit the existing observed_rag_trace_v1
schema with the exact target, source catalog, query IDs and prompt text,
author-declared reference supporting source IDs and exact supplied context
as retrieval chunks (one chunk containing the full pinned document per
nonempty context). The requested ghost context has zero chunks. Do not
claim this is independently observed retrieval if the candidate's actual
retriever was not instrumented. The independent real-retrieval audit remains
available through caeval.rag.vendor_trace.

For every completed answer, supply atomic_claims (possibly empty), with
claim_id, verbatim text, cited_source_ids, answer_start and answer_end
(zero-based Python Unicode character offsets, end exclusive). A clinician
must independently verify that all factual/action claims and their citation
attributions were captured. Do not omit uncited claims or fabricate spans.
A failed trace must not contain a scored answer. Missing or extra trace IDs,
changed source versions/text and modified prompts are rejected.

    python -m caeval.citation_assessment_cli prepare REVIEW_WORKSPACE \
      --plan plan.json --approve-plan EXACT_PLAN_HASH --traces traces.json \
      --reviewer doctor-a --reviewer doctor-b --operator coordinator \
      --confirm-roster-and-reference

Use a new private directory. The coordinator independently approves
reference material, identities, credentials, expertise and independence.
The plan, trace and exact roster are frozen. No model calls occur.

## Review and analyze

    python -m caeval.citation_assessment_cli export REVIEW_WORKSPACE \
      --reviewer doctor-a --out /approved/a.json

Each clinician sees the entire answer, claimed spans and current
claim/source pairs. First check extraction coverage: complete, incomplete,
or cannot_judge (explain incomplete/uncertain). Check semantic support for
every current pair: supports, contradicts, does_not_support or cannot_judge.
Support and contradiction require a verbatim quote from the source.
Complete only the submission block. The same signed packet cannot be
submitted for a different reviewer. The signature protects packet binding,
not clinician identity.

    python -m caeval.citation_assessment_cli submit REVIEW_WORKSPACE \
      --reviewer doctor-a --file /approved/a-filled.json \
      --operator coordinator --confirm-identity

Repeat for all predeclared clinicians. A changed submitted answer cannot
overwrite the original.

    python -m caeval.citation_assessment_cli analyze REVIEW_WORKSPACE

Report separates planned/completed/failed queries and extraction-coverage
verification. Claim counts and unsupported rates use only assessed claims
with unanimous extraction coverage; absent, contested and indeterminate
semantic verdicts never count as supported. Any known invalid citation or
citation outside the supplied context is a defect in a determinate claim.
A zero-claim answer has no assessed-claim rate and is not a clinical pass.
The raw atomic component report is explicitly labelled before the extraction
coverage gate; it must not be interpreted as the final assessment.

Results are descriptive only, with the original source and independent
submission digests. No source currency verification against external
registries, clinical truth, automated claim extraction, safety certification,
purchase approval or claim elevation is implied. Actual independent clinical
validity, parser/trace interoperability, calibrated uncertainty and canonical
project/browser integration remain separate release gates.

Use protected institutional storage and non-identifiable approved inputs.
No patient data should leave the approved environment without authorization.
