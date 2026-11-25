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
   patient-conversation families. The advanced JSON interface can instead
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
clinical purchasing decision. The RAG trace executor is not yet integrated
as a canonical institutional programme source. Browser source selection uses
existing record identifiers, not automatic evidence matching or source discovery.
