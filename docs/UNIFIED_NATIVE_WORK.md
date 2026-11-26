# Unified native evaluation work inventory

From an institutional unified assessment programme, administrators and operators
can open Programme evaluation work. This is a coordination view, not a job
executor or a clinical decision. The native workflows retain their existing
authorization, preparation, submission, review and evidence-lock boundaries.

The work inventory covers text and conversation jobs, synthetic RAG rehearsals,
FHIR, HL7 and image-text readiness reports, experimental research programmes,
diagnostic imaging studies and RAG grounding studies. Synthetic interoperability
reports are replayed using the existing exact-plan evidence adapter. Research
programme summaries use the restricted committee projection. Imaging and
grounding studies are matched only by exact candidate vendor ID and version:
these are not exact buyer-plan matches and may not be eligible evidence.
No other candidate's outputs, case labels, corpus text or reviewer ratings are
included by the new adapters.

New source scans are bounded at 200 records per kind and plan scope. An
unavailable inventory or a partial scan is explicit; it is not an empty
successful result. Unverified-record counts may include records that could
not be scoped due to a failed replay. The older text/conversation scans remain
plan-level and bounded, with no reliable completeness count. Multiple family
requirements can show the same plan-level job; family eligibility is separately
verified during native association. Stale programmes do not initiate new work
scans. A sealed or available record is not automatically associated, and no
clinical score or deployment authorization is implied.

The new browser tests cover native FHIR and imaging discovery, role checks,
and corrupt or missing synthetic records. Run the full unit/integration suite
on the installed repository before using this workflow in a hospital.


## Evidence gaps and next actions

The programme detail page links to the evidence-gap report for administrators
and operators. It derives the native work projection and locked-evidence
status from the same institutional database transaction; external workspace
files are not an atomic snapshot. Every required canonical suite appears,
including unsupported and unplanned suites. A linked internal-regression
source is labelled as internal regression only. A synthetic RAG rehearsal
does not fill a canonical evidence gap, and incomplete imaging rosters
remain explicitly limited.

The report differentiates stale plans, missing associations, changed or
unavailable locked sources, incomplete work inventories, potentially ready
records and work still being prepared or executed. Administrative priorities
are not clinical urgency. Administrators can follow a potential-ready task
to native evidence discovery, where the exact candidate is independently
verified and explicitly approved. Operators can open the native work queue
but cannot lock evidence. Potential-ready counts are not evidence eligibility
or a recommendation to select a particular result.

The digest identifies the deterministic projection, not a signature, a
frozen hospital approval, or an independent clinical acceptance result.
Nothing is executed or associated by the report. The programme's underlying
canonical evidence conditions and deployment authority are not changed.


## Portable administrative evidence-gap handoff

Administrators can preview and explicitly download a JSON handoff from the
evidence-gap report. The exported snapshot includes existing programme
coverage and locked source metadata, aggregate counts of potentially ready,
active and other native work, and the derived gap report. Unassociated native
job identifiers, job notes, raw clinical outputs and reviewer records are
not included by the work summarization. Programme and associated source
identifiers remain sensitive institutional metadata; use approved recipients
and storage.

The export recaptures the programme in an institutional transaction and
requires the exact preview digest; a changed snapshot is refused. Successful
exports are audited. The digest identifies content, not identity or
authenticity. The recipient can replay the administrative projection offline:

```sh
clinical-ai-eval unified-gap-handoff verify handoff.json --expected-digest APPROVED_DIGEST
```

Use an independently communicated approved digest. Verification reconstructs
the coverage structure and gap projection from captured metadata; it cannot
reverify private source records, prove that captured counts were correct,
or establish clinical acceptance. External workspace files are not covered by
an atomic database snapshot. Count and size bounds apply, and projection drift
after redaction is refused. This is an administrative handoff, not a clinical
evidence package or a deployment approval.


Coverage verification additionally reconstructs the complete frozen plan and
requirement inventory, the runnable/blocked suite partition, the omitted
suite list and the native source scope and authority fields. It uses the
verifier build's registered executors; older exports whose executor
definitions have changed may require the matching historical build.
Handoff replay compares canonical JSON content digests, not Python numeric
equality, so substituting integers for Boolean claims is refused. These
structural checks still do not independently establish authenticity of an
export or the truth of native evidence.
