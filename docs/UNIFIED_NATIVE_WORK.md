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
