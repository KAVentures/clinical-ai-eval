# Rule-bound decision certifiability assessment (experimental)

This standalone research workflow evaluates native structured responses about
a fixed, precisely specified action under a small, version-pinned, independently
authored rule bundle. It reuses the existing certificate verifier and exact
minimum-information (MMIP) solver. It does **not** certify patient care,
recommend a treatment, establish guideline correctness, or register the
canonical decision_certifiability executor. The example is entirely fictional.

## Prepare the reference before running the candidate

Use casepacks/decision_certifiability/public_dev/demo.json as a structural example.
The specification contains target name and version, synthetic provenance, one
rule bundle and a fixed case list. The bundle contains source IDs, versions,
statuses and exact source texts; finite string-valued fact domains and integer
relative information-acquisition costs; all action parameters; one support
rule; and a mandatory checklist of critical questions and contraindications.

Each check is a conjunction of allowed categorical values for one fact and
has a severity, a certificate effect, and a verbatim quote in a pinned active
source. Contraindication checks must have block effect. Multiple checks can
apply to one fact. Every fact must appear in the checklist. This deliberately
restricted representation does not model arbitrary guideline logic, temporal
clinical relationships, continuous variables or individualized dosing.

Every case has an explicit observation state for every fact: known, missing
or stale. Known and stale values require a timestamp no later than the
snapshot. Staleness is authored, not inferred from a universal clinical
freshness threshold. Sources available in that case are explicitly listed.
The reference builder checks that each declared probe is actually instantiated:
complete conditional support; missing information with mixed permitted and
prohibited worlds; a present blocking contraindication; stale information
with mixed worlds; or an obsolete-source condition lacking current rule
closure. The pack need not include every condition, so report scope honestly.

The reference certificate is derived from the frozen rule inventory, exact
observations and source availability. Source and quote hashes and observation
hashes are bound in the provenance chain. No candidate-provided checklist can
replace this reference. The existing verifier computes the conditional verdict.
Enumerate all combinations of the declared fact domains consistent with known
facts, bounded to 128 worlds and ten fact variables. The exact solver returns
all minimum-cost nonadaptive information sets that separate permitted from
prohibited worlds. The witness is explicitly unconfirmed by a clinician.

    python -m caeval.decision_assessment_cli plan --spec spec.json --out plan.json

A qualified clinical author and independent experts must validate the action,
rule encoding, source currency, scope, facts, temporal assumptions, critical
questions and completeness of the finite world model. The software verifies
structural and algebraic consistency, not clinical content. Record the approved
plan hash independently before candidate execution.

## Controlled structured-response exchange

    python -m caeval.decision_assessment_cli exchange --plan plan.json \
      --approve-plan EXACT_PLAN_HASH --out candidate-input.json

The candidate receives observed facts, specified action, available sources and
the public rule/check contract. For unavailable sources, the checklist IDs
and metadata are shown but their quotes and allowed predicates are withheld.
Reference verdicts, world sets, minimum-information solutions and probe labels
are withheld. This measures structured rule application under a known contract,
not autonomous discovery of the right clinical rules.

Candidate JSON output must be an object with verdict (CERTIFIED_CONDITIONAL,
DEFER or BLOCK), certificate (object or null), and requested_facts (unique
declared fact IDs). For certificate formatting, the existing
schemas/clinical_certificate.schema.json describes the base schema; this
protocol adds exact required source, snapshot and check closure. In particular:

- certificate_id is a nonempty candidate identifier; claimed_verdict is optional.
- patient_snapshot is exactly the observed snapshot; action is the exact
  requested action; evidence_bundle lists available source IDs, versions and
  statuses, not unavailable sources.
- support uses the public rule_id, applicable according to availability,
  conflict=false and pinned provenance when available.
- Include every required check with exact id, label, severity and effect.
  Set status to unknown when the fact is missing/stale or the rule source
  unavailable. Otherwise use pass/fail for questions, absent/present for
  contraindications.
- For available sources provenance is source:ID:VERSION:SHA256 and
  quote:SHA256, where the hashes are of the canonical JSON source object
  and exact quote string respectively. Checks also bind
  observation:FACT_ID:SHA256 of the exact observation object.
  Canonical JSON is UTF-8, sorted object keys, compact separators,
  non-ASCII preserved. For unavailable rule sources omit their source and
  quote provenance, but retain observation provenance for checks.

This strict contract is suitable for certificate-capable systems; it is
not a fair direct score for an ordinary prose-only assistant. Such a system
requires a separately validated action/fact/certificate extraction pipeline.

    python -m caeval.decision_assessment_cli response-template --plan plan.json \
      --approve-plan EXACT_PLAN_HASH --out responses.json

Record the exact native JSON string in raw_response and copy the three parsed
fields without modification. Complete responses require exact agreement with
the strict JSON parser, including rejection of duplicate keys. No independent
instrumentation of vendor execution is claimed. Timeout, provider_error and
extraction_failure are explicit outcomes; do not fabricate structured fields.
An extraction failure retains the original unstructured response.

## Analyze

    python -m caeval.decision_assessment_cli assess --plan plan.json \
      --approve-plan EXACT_PLAN_HASH --responses responses.json \
      --confirm-authored-reference --out report.json

The report compares the declared verdict to the reference and independently
runs the existing structural verifier on the submitted certificate. Required
certificate content is checked against the derived reference, including exact
action parameters, snapshot, source versions, provenance, mandatory checklist
and effects. Checklist list order is ignored, but missing or duplicate checks
and changed values invalidate closure.

Report includes false certifications among positive declarations (including
invalid closure on a reference-permitted case), certification on reference
noncertifiable cases, missed blocking decisions, unnecessary deferral, verdict
agreement, and sufficient information requests. These use explicit eligible,
assessed and unassessed denominators. Failed transport and extraction are
never safe observations. Information efficiency is evaluated only for
reference-underdetermined cases with current source closure and sufficient
queries; cost ratios are undefined for insufficient requests. It measures
the authored finite model, not clinical utility, timing or real money.

All outputs are bound to the plan and response digests and marked
decision_grade=false, clinical_validity=UNVERIFIED. No clinical claim or
family maturity upgrade is made. Independent blinded reference/encoding
validation, free-text action extraction validation, held-out hazard studies,
canonical executor and browser integration remain necessary. This is an
experimental assessment instrument, not a clinical decision service.
