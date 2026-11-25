# Universal candidate API (version 1)

The same independent evaluation engine can assess software built with any
language, model provider, EHR or orchestration framework. FHIR, HL7 v2,
proprietary integration engines, and custom service interfaces are upstream
implementation details: a vendor adapter translates a **synthetic presented
test input** into its product's normal test interface. No live EHR or
production patient data is required by this protocol.

## Implement the candidate-side endpoint

Export its OpenAPI 3.1 contract without a workspace or network:

    clinical-ai-eval buyer candidate-openapi

The CLI wraps the OpenAPI document under the JSON result field. The candidate
implements POST /evaluate (or an IT-approved equivalent path). Example:

    {
      "protocol":"clinical-ai-eval-candidate/1",
      "request_id":"0123456789abcdef0123456789abcdef",
      "target":{"name":"Example product","version":"1.2.3"},
      "input":{"kind":"single_turn_text","text":"Fictional test prompt"}
    }

Return HTTP 200 with Content-Type application/json:

    {
      "protocol":"clinical-ai-eval-candidate/1",
      "request_id":"0123456789abcdef0123456789abcdef",
      "target":{"name":"Example product","version":"1.2.3"},
      "status":"complete",
      "answer":"Fictional candidate output"
    }

Use status unavailable and an empty answer for an unsuccessful call. Never
manufacture a clinical response. Non-200 responses are also missing evidence.
The target must be the actual served product/version. Reject mismatch rather
than blindly echoing the requested version. An echoed identity is a candidate
assertion, not independently verified model provenance. The evaluator checks
the exact nonce, version, protocol and response schema. No hidden expectations,
risk thresholds, or expected answer are sent. Requests and responses use
bounded UTF-8; JSON duplicate keys and non-finite values are refused.

Python adapters may use the library helpers
`candidate_protocol.validate_request(document, expected_target=served_target)`
and `candidate_protocol.response(document, target=served_target, status=..., answer=...)`.
Non-Python providers can implement the published contract directly.

## Institution-side configuration

Trusted IT creates approved-connectors/example.json in the buyer workspace:

    {
      "vendor_id":"example",
      "target_name":"Example product",
      "target_version":"1.2.3",
      "url":"http://127.0.0.1:8769/evaluate",
      "protocol":"clinical-ai-eval-candidate/1",
      "headers":{"Authorization":"Bearer ${CANDIDATE_TOKEN}"},
      "timeout_seconds":30
    }

Use the existing collection queue and explicit CLI execution approval. The
same connector is available to supported experimental research collection.
Legacy configurations with prompt_field and answer_path continue to work.
Only literal loopback HTTP endpoints are accepted; no DNS, redirects or
ambient proxies. A separately approved institutional bridge can handle
external routing where permitted. IT must bind the adapter to an isolated
candidate test environment, authentication, data controls and the exact
approved version. Localhost alone does not provide authorization.

## Where this fits

The protocol covers single-turn text, not arbitrary multimodal workflows,
multi-turn state, live FHIR or HL7 v2 conformance, or a remote evaluator job
API. Existing manual exchanges support disconnected installations. The
research and procurement pipeline remains the source of truth for frozen
protocols, independent reviews, deterministic replay and decision limits.
Agents can orchestrate approved CLI workflows; the existing scoped MCP
broker exposes aggregate read-only evidence, not unrestricted execution.
Future extensions should version structured trace, transcript and multi-turn
contracts separately and add scoped service-to-service job submission and
status interfaces without weakening approval or patient-data boundaries.

The contract is intentionally model- and EHR-independent. It is an
integration specification, not clinical validation or a deployment approval.

## Collection-to-assessment workflow

After explicitly authorized collection completes, open Candidate response collections in the institutional browser. Select the staged judge panel and governance answers, and optionally the exact activated procurement conditions hash. Preview the frozen handoff digest, then approve it. The workflow uses the exact collection receipt, complete response inventory, prepared assessment digest and selected governance. It creates a canonical prepared assessment without network calls, queue creation or scoring. Incomplete responses cannot be promoted. The assessment remains available in the normal setup and evaluator-service workflows. A later panel or scientific definition change invalidates the preview. Unbound research is explicitly labelled; it is not a procurement comparison.

## Structured controlled-citation adapter

Export the optional, separately versioned contract with:

    clinical-ai-eval buyer citation-trace-openapi

Set protocol to clinical-ai-eval-citation-trace/1 in the staged connector config.
The adapter receives the exact controlled citation prompt and correlation nonce;
its response includes a structured trace (retrieval_query, retrieved_chunks,
answer) instead of an answer string. Every chunk includes source_id,
source_version, text and contiguous rank. Unavailable responses have null trace.
Only citation research studies may use this connector. The collector validates
the exact supplied source context; a differing context is an explicit failed
collection, not silently repaired or scored. It preserves the reported rewrite,
chunks and answer in canonical JSON; independent reviewers still annotate claims.
The legacy text collector's context is authored/supplied by the protocol, not
independently observed retrieval. The new adapter reports candidate-side data;
this is not independent instrumentation or unrestricted real-world RAG recall
assessment. A failed probe retains its supplied fixture context for denominator
accounting, not as evidence of a successful retrieval.

## Multi-turn conversation adapter

Export the full-history contract:

    clinical-ai-eval buyer conversation-openapi

In approved-conversations, stage the usual vendor_id, target_name,
target_version, url, headers and timeout_seconds, replacing the legacy
history_field, answer_path and session_field mappings with protocol:
clinical-ai-eval-conversation/1. The candidate receives alternating patient
and system messages ending in a patient turn, an opaque episode nonce and a
fresh request nonce. Reply with the exact protocol, request_id, episode_id,
served target, status and answer. Use unavailable with an empty answer for
failure. Each reset issues a new episode nonce; only visible transcript text
is transmitted. This contract is full-history, not incremental stateful chat.
The local adapter checks correlation and rejects wrong product versions or
episodes; it cannot prove the vendor actually isolates state. Existing
patient-world simulation, safety endpoints, independent review, call budget
and explicit worker approval remain authoritative. No clinical claim elevation.

## Shared synthetic FHIR and HL7 v2 adapter

Export the structured contract with `clinical-ai-eval interoperability-collect openapi`.
Stage the usual approved-connectors configuration with protocol
clinical-ai-eval-interoperability/1. The adapter receives a fixed synthetic
FHIR R4 bundle or HL7 v2.5.1 ORU_R01 message list, the selected patient ID,
extraction instruction and correlation nonce. It returns exact structured
observation fields and explicit complete, no_eligible_observations or
unavailable status. No reference answers or probe labels are sent.

Preview the approved plan and exact staged connector binding:

    clinical-ai-eval interoperability-collect preview /approved/workspace --plan-id PLAN --connector-id CONNECTOR --kind hl7

An authorized local worker then runs the same command with action run,
--approved-digest DIGEST and --confirm-candidate-calls. The result contains
the replayable evidence package and a transport receipt. Its evidence.responses
can be imported through the existing institutional synthetic rehearsal page.
No automatic institutional submission or network call from the browser.
The client refuses non-loopback endpoints, redirects and mismatched versions,
checks drift before each probe, and stops after three failed calls or its
runtime budget. Failed/unattempted probes remain unavailable. This tests
fixed synthetic extraction only, not production messaging or certification.

## Guided institutional onboarding

From a current saved buyer plan, choose Guided candidate onboarding and collection.
Only exact registered vendor, product and version matches are displayed. Select
an approved pack and connector, review the full runnable suite inventory and
required-but-not-runnable gaps, then approve the exact digest. The guided path
prepares the canonical blinded exchange and queues collection without network
access. Repeated approval of an identical binding reuses its collection ID,
including a terminal collection; deliberately repeated experiments use the
ordinary collection queue. The existing worker still needs explicit exact
binding approval before transmission. This is a single-turn path; patient
conversation and experimental workflows retain separate prerequisites.

Guided collection IDs are deterministic and require one matching committed institutional approval audit before a worker can transmit anything. An orphaned queue entry from an interrupted approval is inert until the identical approval is repeated. Connector configuration drift stops subsequent transmissions. Terminal guided collections are reverified on repeated approval.

## Mixed-profile institutional programme

For a saved plan with multiple clinical product profiles, open Mixed-profile
evaluation programme. The preview generates exact separate profile-specific
plans without writing them, preserves the original parent audience gate and
shows each lane's runnable and blocked suites. Explicit digest approval freezes
those child plans. The programme tracks verified canonical job packages and
related experimental reports for the exact child plan IDs. It never treats a
narrower-scoped result as proof of the parent mixed-audience requirement,
combines scores, or approves network calls. The same intake answers are copied
unchanged; operators must confirm these describe each specific profile before
approval. Each existing worker retains its own execution approval. A missing
or modified approved child plan is refused, not silently recreated.

## Version reassessment

From any valid saved plan, open Plan version reassessment and review history.
Enter the exact successor version and review the copied scope and the scientific
definition delta. An explicit digest approval creates a new immutable plan and
audited association. Historical definitions can be inspected, but outdated
packages are not counted as currently verified. Each version retains separate
canonical package coverage and blocked suites. Exact staged connector matches
are displayed without contacting any service. Previous scores, risk approvals,
case packs, judge panels, credentials and clinical conclusions are never carried
forward automatically. The user must confirm unchanged intended scope and intake;
use a new scope plan for a change in intended use. No comparison is authorized
by this operation, and it does not automatically detect vendor releases or run
models. Existing guided collection, conversation, research and mixed-profile
workflows can prepare the successor's separate execution approvals.

Locally staged text, structured and conversation connectors are scanned without making calls. Exact vendor/product matches at other declared versions appear as reassessment suggestions; an operator must still review and freeze a new scope. Missing or invalid execution evidence is labelled no_verified_package, not proof that no execution occurred. Connector declarations do not establish release chronology or independently attest the deployed model.

## Synthetic multimodal image + text readiness

The clinical-ai-eval-image-text/1 contract tests exact readings from 12 fixed
fictional monitor-display images. Use image-text-readiness openapi, or prepare
with --target-name and --target-version to obtain a blinded exchange and
response template. Use assess --plan and --responses, and verify --evidence
with optional --expected-digest. Stage an approved-connectors entry with
protocol clinical-ai-eval-image-text/1 for automated collection; preview
WORKSPACE --plan-id PLAN --connector-id CONNECTOR, then run with the same
arguments plus --approved-digest DIGEST --confirm-candidate-calls. Native
HTTPS bridge modality image_text is supported. Only fixed synthetic PNGs are
allowed; missing results remain unavailable. This is not diagnostic imaging
validation and does not satisfy a canonical clinical family.

The institutional setup page also provides the image-text rehearsal for a current
saved plan. Operators download the blinded fixture exchange and submit structured
outcomes, then export deterministic evidence. The committee inventory shows
exact matches, mismatches and unavailable responses separately from clinical
verdicts. Imported outcomes are operator-attested, not verified model calls.
The ledger only accepts bounded three-digit synthetic readings and statuses;
it is not a general-purpose patient image repository. The FHIR-specific
retention policy does not automatically delete these fixed synthetic records.

## Matched procurement contrasts

The frozen risk-bound procurement comparison now reports exploratory paired
binary endpoint contrasts only when all candidates in the frozen roster have
verified packages and matching conditions, exact cell inventory and judge
configuration. Pairing is by cell ID with identical base-case cluster identity.
All planned opportunities remain in the denominator. Missing values contribute
worst/best-case binary difference bounds; no p-value is emitted for incomplete
pairs. Complete pairs use an exact sign-flip test on case-cluster sums, not an
independent-cell McNemar test. The test assumes independent case clusters and
exchangeable product labels under a sharp null; evidence integrity alone does
not establish those assumptions. Holm adjustment includes every declared
hazard/product pair within the family, including unavailable tests. There is no
across-family multiplicity adjustment, clinical equivalence or recommendation.
The committee view and Markdown dossier expose these exploratory results.
