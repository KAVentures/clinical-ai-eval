# Synthetic FHIR R4 interoperability rehearsal

An offline, deliberately narrow integration-readiness assessment for a candidate
that consumes FHIR data. The fixed inputs use FHIR R4 (4.0.1) collection bundles,
Patient references, Observation status, valueQuantity and dataAbsentReason.
See the pinned [Bundle](https://hl7.org/fhir/R4/bundle.html) and
[Observation](https://hl7.org/fhir/R4/observation.html) specifications.

The candidate must extract exact final numeric observations for a specified
synthetic patient, preserving source identity, coded quantity and units without
conversion. Nine authored probes check baseline, other-patient isolation,
entered-in-error and preliminary exclusion, missing value, zero, units, multiple
observations and entry-order invariance. These are product-specific test
conditions, NOT universal clinical interpretation rules or FHIR conformance tests.

## Browser workflow

Open a current saved candidate plan in Assessment setup, then Synthetic FHIR
interoperability rehearsal. Download the generated input bundles and response
template. Run these fixtures in an approved candidate test environment and paste
only the structured response JSON. Confirm synthetic-only provenance. Do not
enter real patient records, clinical notes, credentials or live endpoint URLs.
The response grammar is bounded and accepts no arbitrary source bundle or
free-text response. The synthetic declaration is operator-attested, not a PHI
detector. Operators and administrators can read the full report; committee
observers receive only exact-match, mismatch and unavailable counts and digest.
Results are immutable, bound to the exact saved plan, product version and audit
event. Missing responses must be explicitly unavailable rather than omitted.

## CLI and independent replay

    clinical-ai-eval fhir-readiness prepare --target-name "Synthetic candidate" --target-version 1

Extract the plan, exchange and response_template from the JSON result into
separate local files. After testing, save the completed responses as JSON:

    clinical-ai-eval fhir-readiness assess --plan plan.json --responses responses.json
    clinical-ai-eval fhir-readiness verify evidence.json --expected-digest EXPECTED_SHA256

The assess command returns the portable evidence object inside result; save that
object to evidence.json. Verification regenerates the pinned fixture plan and
every assessment result. No network calls, clinical data ingestion, clinical
judges, live EHR connectivity, FHIR server, write-back or new claim authority.
FHIR profiles, national implementation guides, terminology, authentication,
HL7 v2, integration security and clinical validation require separate work.

    python -m unittest tests_unit.test_fhir_readiness

The output is synthetic software evidence only. It cannot satisfy canonical
clinical suites, authorize a pilot or establish clinical safety.
