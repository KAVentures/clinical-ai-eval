# Unified clinical AI assessment programmes

This is the buyer-owned administrative coordination layer for heterogeneous
clinical AI assessments. It does not replace the native scientific engines,
their protocol-specific acceptance criteria, independent review or the approved
hospital execution processes. No combined score, ranking, clinical safety
verdict or deployment authorization is produced.

## Institutional workflow

1. Create and save the exact buyer scope plans for every product/version and
   profile to be considered. A programme can include several profiles for one
   vendor, but not two different product identities or versions for that vendor.
   A mixed-audience parent plan keeps its own canonical gaps; narrower child
   plans do not silently satisfy those gaps.
2. Open **Unified clinical AI assessment programmes** from the authenticated
   institutional dashboard. An administrator selects current saved plans and
   any additional research, imaging or interoperability requirements. The
   guided builder automatically includes supported runnable paired-text and
   patient-conversation families, plus a synthetic RAG context rehearsal. The advanced JSON interface can instead
   declare the precise requirement inventory. In both routes all omitted or
   unsupported required canonical suites remain visible as gaps.
3. Preview and explicitly freeze the exact programme digest. This freezes
   product versions, plan IDs, selected requirements and known gaps. No
   candidate or judge is called. Programme IDs are immutable.
4. Perform each native evaluation in the corresponding existing workflow,
   with its own approved data, reference protocol, scope and execution controls.
   When its evidence is available, enter the existing source record ID against
   the specific programme requirement. The preview verifies exact scope and
   source integrity. An administrator explicitly locks that association.
   Each requirement has one immutable source association, not a moving latest
   result. The administrator remains responsible for the source's relevance.
5. Operators and committee observers can view the administrative coverage
   matrix. The programme distinguishes unlinked, available, changed,
   unavailable and stale-plan requirements. A completed source is not a pass.
   Neither experimental research, imaging nor synthetic extraction
   rehearsals satisfy canonical requirements.
6. A committee snapshot includes the current programme coverage and becomes
   stale when that coverage changes. An administrator may also export a
   digest-approved restricted administrative coverage package. It contains
   source IDs, claim scopes, candidate versions and evidence digests, not
   images, clinical response content, reference labels or reviewer identities.

The programme does not enforce the timing of native source collection relative
to programme freeze. Locking already-existing evidence is an administrative
association, not proof of a prospective study. Source selection and any
scientific comparisons still require their respective native study protocols.

## Source types and exact association

| Requirement kind | Source ID | Association and currentness |
|---|---|---|
| `text` | Completed canonical single-turn job ID | Reverify the job and exact family package; exact saved plan, vendor, name and version. Demonstration remains demonstration. |
| `conversation` | Completed institutional conversation job ID | Rebuild current binding and reverify result, exact patient family, plan and product version. |
| `rag` | Sealed synthetic RAG rehearsal ID | Exact saved plan, candidate version and native retrieval_failure trace replay. Demonstration-only context response behavior; no claim of clinical validity. |
| `research` | Experimental research programme ID | Exact saved plan and target; all its planned report references integrity-checked. Not an independent replay in this coverage view. |
| `imaging` | Sealed diagnostic imaging study ID | Candidate ID equals registered vendor ID; exact declared version; at least one scored prediction. Retain reference and transport receipt digests when present and complete-roster flag. Task relevance and actual model identity remain unverified. |
| `fhir` | Synthetic FHIR rehearsal record digest | Replay exact saved plan and report. Synthetic extraction only. |
| `hl7` | Synthetic HL7 rehearsal record digest | Replay exact saved plan and report. Synthetic extraction only. |
| `image_text` | Synthetic monitor image-text rehearsal record digest | Replay exact saved plan and report. Synthetic transcription only. |

A shared imaging study may be linked separately for two registered vendors only
if each exact candidate version has a scored submission. This does not create
a new combined comparison or imply both vendors ran under equivalent conditions
in other modalities. Use the native matched comparison in the relevant
scientific workflow.

All available sources are rechecked on coverage retrieval; tampered or missing
sources become unavailable. A changed buyer scope or scientific plan marks the
whole programme stale. The stored link remains visible for audit; it cannot be
silently replaced. Start a new programme for a new version or scope. Programme
freeze and link records are checked against the local audit inventory, not
against a cryptographically signed external log.

## Advanced specification

```json
{
  "programme_id": "hospital-evaluation-2026",
  "plans": ["EXACT_SAVED_PLAN_SHA256"],
  "requirements": [
    {"id": "text-safety", "kind": "text", "plan_id": "EXACT_SAVED_PLAN_SHA256", "family": "missing_information"},
    {"id": "imaging", "kind": "imaging", "plan_id": "EXACT_SAVED_PLAN_SHA256", "family": null},
    {"id": "interoperability", "kind": "fhir", "plan_id": "EXACT_SAVED_PLAN_SHA256", "family": null}
  ]
}
```

Replace placeholders with the actual lowercase 64-character IDs of current
saved plans. Every included plan must have at least one explicit requirement.
For canonical text/conversation requirements the family must be an exact
runnable family for the plan and supported institutional executor. For all
other requirements family must be null; no fake canonical association is
allowed. Bounds: 40 programmes, 24 plans and 80 requirements per programme.

## Portable evidence and retention

```sh
clinical-ai-eval unified-assessment verify-export coverage.json --expected-digest DIGEST_FROM_TRUSTED_RECORD
```

The verifier checks the administrative document's digest and structural
consistency, including requirement inventory, candidate identity association
and completeness flags. It cannot independently reverify source records
without the institution or the separate source packages. Its result explicitly
states that source records were not reverified. Store the trusted digest
separately. The private evidence behind each source still has its own export,
verification and authorization path.

The maintenance retention planner protects locked FHIR rehearsal references
even before a committee snapshot is frozen. Existing retention behavior for
unlinked eligible records is unchanged. All programme metadata and exports
are restricted organizational records; the administrative labels and product
versions can still be confidential.

This release does not add live PACS/DICOMweb, direct vendor dispatch, new clinical
executors, external validation, production identity federation, or an automatic
clinical purchasing decision. The RAG trace executor is integrated for the bundled synthetic context rehearsal only; external corpora, independent citation adjudication and vendor-retriever validation remain outstanding. Browser source discovery verifies eligible local records; it does not choose a best result or automatically lock evidence.


## Evidence discovery and coordinated work

Open an individual unlinked requirement and select **Find verified eligible
evidence**. Discovery checks the existing source adapters and returns only
eligible exact-scope records. It scans at most 200 candidate records, returns
at most 20 matches, and discloses truncation. No ranking, best-result
selection or automatic evidence lock takes place. Missing or invalid records
may be omitted; use an exact known ID if necessary. The administrator still
reviews scientific relevance and approves the source.

For multiple associations, the administrator may paste an array of explicit
requirement/source pairs into **Batch evidence association**:

```json
[
  {"requirement_id": "text-safety", "source_id": "EXISTING_JOB_ID"},
  {"requirement_id": "interoperability", "source_id": "EXISTING_REPORT_ID"}
]
```

A batch accepts up to 40 distinct requirements. Preview validates every
source. The digest binds the complete sorted selection and its verified
metadata. Approval re-verifies every selected source before any writes;
if any source, requirement or approval changes, the whole transaction is
refused. No duplicate or already-locked requirement is allowed.

**Native evaluation work and handoffs** is an operator/admin read-only view
of exact-plan prepared text assessments, native text/conversation jobs and synthetic RAG rehearsal states.
It distinguishes the native preparation, queue, execution and terminal
states, without inferring that any job passed. A completed multi-family job
can appear in more than one requirement. Navigation leads to the existing
native workflows; queueing, cancellation, local worker execution and all
network approvals remain there. Other modalities retain their native
workflow links and evidence coverage; their intermediate jobs are not
enumerated in this view. This is not an automatic cross-modality scheduler.

## Version-only programme successors

First create approved native version reassessments for the saved plans that
need new versions. On the baseline programme's **Version reassessment history**
screen, choose a current unchanged plan or an existing approved reassessment
successor for every baseline plan. At least one exact version change is
required. A separately created plan without native reassessment lineage is
not eligible. Incompatible profile or suite-selection changes require a new
explicitly scoped programme rather than the version-only route.

Preview and approve the exact new programme and lineage. Both are frozen
in a single institutional database transaction. No existing source association
is copied, even for unchanged plans. The new programme requires explicit
source selection and approval; old sources cannot meet a changed exact
product version. The underlying scientific protocols determine whether
source collection is prospective or repeatable.

The coverage comparison shows baseline and successor version, source
availability states and associated digests. It is not a model performance
comparison, equivalence or noninferiority analysis. Native reassessment
audit records are rechecked when programme lineage is read, and committee
snapshots include the lineage. Source evaluation and any scientific
comparison must still follow the appropriate native assessment protocol.


## Native retrieval-trace rehearsal

For a saved buyer plan with runnable `retrieval_failure`, the programme
supports a `rag` requirement with that exact family. The guided builder
includes it automatically. Follow the native work link or open **Synthetic
RAG context rehearsal** from the institutional dashboard.

An administrator previews and freezes the exact bundled synthetic corpus,
case pack, native definitions, product identity and four distinct probe
prompts. An operator downloads the prompt exchange and fills every slot of the
version-bound response template using the product under evaluation, outside
the application. The browser accepts the complete response document and
explicit confirmation, then locks and replays the native retrieval executor
offline. No candidate or judge is called by the application. Missing or
substituted responses and changed prompt digests are refused. This workflow
does not permit overwriting the locked answers; a different run has a new
study identifier.

Only the administrator may view descriptive native flags and explicitly export
the full private evidence package. The committee receives restricted coverage
metadata. Discovery and programme association require a sealed, reverified
rehearsal belonging to the exact buyer plan and product version.

```sh
clinical-ai-eval verify-rag-rehearsal private-rag-evidence.json --expected-digest DIGEST_FROM_TRUSTED_RECORD
```

Replay reconstructs the complete bundled corpus, native prompt inventory,
source definitions, responses, trace records and descriptive summary. Native
timestamps are omitted from the deterministic replay representation. Full
corpus and native trace contents are bound by the separate rehearsal evidence
digest; this is not a canonical assessment_manifest/1 package. Scientific
definition drift is a hard refusal; retain the exact repository version for
historical replay.

The existing native retrieval perturbations depend on identifiers in the
bundled synthetic fixture. Do not substitute arbitrary hospital corpora or
interpret the evaluator's deterministic lexical retriever as the vendor's
retrieval system. There is no positive-control baseline in the native
retrieval-failure probe inventory. Native abstention flags are keyword-based;
citation checks establish only limited identifier resolution and version
status, not claim support. Responses are operator-attested, and the application
does not verify model execution or the provenance of pasted text. The workflow
is an explicitly demonstration-grade rehearsal, does not satisfy canonical
clinical requirements and cannot support deployment or procurement conclusions.

## Independent RAG grounding research

The separate [controlled RAG grounding research workflow](RAG_GROUNDING_RESEARCH.md) adds authorized versioned corpora, paired support-available/support-removed controls, product-reported retrieval traces and two blinded reviewer accounts. Select `rag_grounding` as an additional experimental requirement (`family: null`). Evidence matching is vendor ID and exact declared version only; clinical relevance and actual model execution remain externally verified responsibilities. This does not satisfy the canonical RAG family.

## Matched historical version research

The separate [matched longitudinal research workflow](MATCHED_VERSION_REGRESSION.md) compares declared versions of the same product on exactly matched imaging or RAG grounding cohorts. Register before recorded successor exposure. It provides descriptive finite-cohort bounds, not statistical noninferiority or clinical acceptance, and does not replace unified successor coverage tracking.

## Hospital-specific review gates

The optional [hospital referral policy](HOSPITAL_REFERRAL_POLICY.md) freezes vendor-specific gate criteria before source association, binds assigned reviewers to a frozen evidence snapshot, and records an immutable administrative disposition. Missing or weak canonical evidence and unresolved gates block referral. It never authorizes clinical deployment.

## Operational lifecycle monitoring

[Institutional lifecycle monitoring](LIFECYCLE_MONITORING.md) records aggregate version-bound operational reports, missingness, local threshold alerts, incidents and documented escalation. It requires an external authorization attestation, not authorization by Clinical AI Eval. No automated clinical intervention or scheduled collection is performed.
