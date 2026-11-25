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
