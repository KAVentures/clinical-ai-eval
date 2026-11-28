# Matched clinician-versus-model predictive evaluation

Compare a prespecified candidate-model operating point with binary clinician
actions on the same frozen cohort. This is a finite-cohort descriptive API,
not a clinical trial, noninferiority test or replacement for clinical care.

## Freeze and collect

Call `freeze(plan, expected, reference_panel, panel_digest, spec)` with the
existing independently retained predictive plan and reference panel. The
comparison specification has exactly four fields:

- `clinicians`: one to thirty distinct declared clinician tokens, none equal
  to the reference coordinator, primary readers or adjudicator.
- `threshold_bp`: one of the model thresholds already frozen in the study.
- `decision_protocol_sha256`: digest of the independently approved clinician
  decision protocol, including action, available information and timing.
- `decision_input_sha256`: mapping of every planned unit ID to the digest of
  its approved decision-time information, excluding future outcome evidence.

Freeze and retain the comparison digest before collecting clinician decisions
or examining candidate results. Actual chronology and blinding must be
established independently. Provide only approved decision-time information
through the institution's existing secure collection process, not the reference
panel's outcome source files or candidate predictions. The separate digest
inventory does not prove that the clinician/model inputs are comparable or
free of leakage. This API does not distribute source files or check their bytes.

For each declared clinician call `seal(plan, expected, reference_panel,
panel_digest, comparison, digest, clinician, source_receipt, rows)`. Each
row has exactly `unit_id`, `action` (true, false or null) and `input_sha256`
matching the frozen input inventory. Missing rows remain unknown. Retain the
submission digest independently. Every clinician must have a submission,
which may be explicitly empty when no decisions were available. Do not select
clinicians or cases based on their performance. The source receipt is a digest
pointer, not an independently authenticated clinical record.

## Analyze and replay

Call `analyze` with the same six frozen-scope arguments and the following
keyword arguments: `primary`, `expected_primary`, `adjudication`,
`expected_adjudication`, `expected_reference`, `model`, `expected_model`,
`submissions`, `expected_submissions`. Primary and optional adjudication
submissions must replay the panel to the retained reference receipt. The model
must be the native candidate prediction bundle pinned by its retained digest.
Supply the full clinician submission inventory and its retained digests.
Candidate scores at or above the frozen threshold count as action=true.

Each clinician's report uses the entire planned cohort. Complete-case counts
show both correct, both wrong, only model correct and only clinician correct.
The paired complete-case difference uses the count of complete triples as its
denominator; zero denominator means undefined. Finite-cohort bounds are
integer counts divided by the fixed planned denominator. They are sharp under
unrestricted completion of unknown actions and outcomes: enumerate each
unit's binary possibilities, using the same reference for both decisions,
then sum the per-unit extrema. The delta is candidate minus clinician; positive
values favor candidate agreement with the binary reference, not patient benefit.
The report does not pool multiple clinicians' judgments as independent patients,
optimize thresholds, estimate population confidence intervals or assert
superiority, equivalence or noninferiority. Correctness is simple agreement
with the declared outcome, not clinical utility or appropriateness of action.

`verify` recomputes the report using the original pinned inputs and checks the
retained report digest and exact report content. Use the existing restricted
JSON tooling for storing artifacts. This API does not provide a new browser,
CLI, authentication service, independent identity check or clinical validation.
No deployment or publication authorization is supplied. All test cases are
synthetic. Full repository integration CI remains required.

    python -m pytest -q tests_unit/test_predictive_human_comparison.py
