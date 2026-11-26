# Supervised research candidate collection

The institutional research studio can queue a supervised collection using an
IT-staged local single-turn connector. This is separate from canonical
evaluation jobs, and does not elevate the scientific authority of the
experimental studies.

## Setup and approvals

Stage a connector under approved-connectors using the existing local
candidate connector contract: vendor_id, target_name, target_version,
literal loopback HTTP URL, prompt_field, answer_path, environment-based
credential headers and bounded timeout. The study target name and version
must match exactly. The institution is responsible for verifying the
endpoint identity and version, its isolation, its handling of session state,
and that any forwarding or external egress is independently authorized.
No remote host, DNS resolution, proxy or redirect is permitted by this
collector. The local endpoint itself may have additional capabilities;
configure it to implement only the approved isolated candidate interface.

Use the research studio to freeze a specification. Select a matching
connector, confirm the plan and queue the collection. The browser does not
call the endpoint and cannot set URLs or credentials. The binding digest
covers exact plan, connector configuration, target, owner and every actual
prompt hash. The candidate sees only approved candidate-facing material.
Each prompt is a separate request; the endpoint must provide fresh,
independent sessions. The collector cannot verify hidden provider state.

A trusted OS operator reviews the binding and executes explicitly:

    python -m caeval.research_collection_cli execute WORKSPACE STUDY_ID \
      --approve-binding EXACT_BINDING_HASH --allow-candidate-calls

Use a separately supervised worker process, not the browser service.
The process uses the approved environment credential references and
existing local single-turn transport. Do not place credentials in study
specifications or raw responses. A binding/config mismatch is refused
before the first call. The worker checks active study owner and connector
configuration before each new call; an in-flight request cannot be revoked.
No automatic retries are performed.

## Collection limits and failures

At most 200 prompts, 64 KiB per prompt, 1 MiB aggregate raw answer text and
the existing 6,000-byte per-answer transport limit are allowed. The worker
stops making new calls after three transport failures, budget exhaustion,
configuration drift, disabled study owner or a 900-second between-call
deadline. Individual call timeouts are configured separately; the deadline
is not a hard wall-clock interrupt. Each outcome is checkpointed in SQLite,
including calls that were not attempted. The receipt binds all outcomes.
No failed or skipped response is treated as a completed clinical observation.

Only queued work can be cancelled in the browser. A crashed or interrupted
running worker is never automatically resumed, since the last request
might have reached the endpoint before its outcome was checkpointed. Stop
the worker, investigate and explicitly abandon the collection:

    python -m caeval.research_collection_cli abandon WORKSPACE STUDY_ID \
      --worker-stopped

Use a fresh study if recollection is required. Interrupted partial outcomes
remain in the private database for investigation; no completed receipt is
fabricated. Running collections block stopped-service workspace backup.
Restoring a checkpoint cancels any copied queued research executions.

## Scientific handoff

When raw collection completes, the browser provides a raw receipt and the
existing response template populated with exact outcomes. The receipt
distinguishes provider failures and not-attempted calls; the existing
assessment response schemas represent both as explicit provider_error.
Inspect the receipt when interpreting these outcomes.

- Robustness: raw text answers map directly to the existing response schema.
- Scribe: raw notes map directly; manually annotate exact factual note spans.
- Citation grounding: controlled-context traces are populated with the
  supplied material; annotate exact atomic claim spans and cited source IDs.
  This is not independent instrumentation of a vendor's internal retriever.
- Decision certifiability: only exact native JSON meeting the existing
  verdict/field contract is parsed. Other raw outputs are retained as
  extraction failures, not scored as safe or converted to guessed certificates.

The user must import the populated response document explicitly. Only the
citation/scribe claim arrays can change; raw strings, source context, other
fields and outcomes are verified against the immutable receipt. The
assessment's own structural and independent clinical review gates still
apply. Collection does not prove claim completeness, semantic correctness
or clinical validity. If every citation call failed, inspect the raw receipt;
the existing clinical review module requires completed answers.

The browser does not automatically invoke an extraction model or judge.
Canonical executor registration, actual independently instrumented vendor
retrieval, validated free-text extraction, representative case packs and
clinical calibration remain separate work. Use approved non-identifiable
data and protect the local workspace and its backups.
