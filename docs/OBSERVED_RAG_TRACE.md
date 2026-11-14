# Real-product RAG trace audit — experimental

The built-in `retrieval_failure` executor injects a **synthetic corpus** into a
product prompt. It does *not* observe the product's own retrieval system. This
new audit accepts **actual retrieval traces exported by a vendor or instrumented
application**, and separately scores retrieval performance against
independently supplied, version-pinned source relevance references.

This is *not* clinical validation or a claim that the supplied trace is
authentic. The provider must expose/record its observed query rewrite, ranked
retrieved text chunks, source document versions, response status and final
answer. The audit cannot reconstruct inaccessible private retrieval from the
visible answer alone.

## Inputs

A JSON file with schema `observed_rag_trace_v1`:

- `target`: name and exact product/model version.
- `sources`: version-pinned source catalog, each with source ID,
  `current|superseded` status, source version, and exact source text.
- `traces`: query ID and text, **observed retrieval_query**, clinician-authored
  `reference_supporting_source_ids`, exact ranked retrieved chunks with
  version, source ID and text, `model_status` (complete/timeout/provider_error),
  and final answer for completed requests.
- Optional `atomic_claims`: a **separately extracted** and audited exhaustive
  list of atomic statements from the answer, with their cited source IDs.
  This file does not infer claims by naive splitting or keyword overlap.

All references remain `candidate` unless explicitly marked reviewed, and
even reviewed references are not independently verified by code.

## Run

```bash
python -m caeval.rag.vendor_trace \
  --traces /private/vendor_observed_traces.json \
  --out /private/vendor_retrieval_report.json \
  --k 5 \
  --citation-pack-out /private/vendor_atomic_claims.json
```

The retrieval report measures macro recall@k, precision@k, reciprocal rank,
unknown retrieved sources, superseded documents, stale source versions, cited
references not retrieved, and provider failures. **It does not mistake retrieval
quality for generation correctness.** Timed-out model calls do not appear as
correct empty answers; all failures remain visible.

A citation-review JSON pack can be exported and passed to the separately
installed `clinical-citation-audit` tool (PR #15) for independent clinician
claim-to-source review. This prevents a frequent false-positive: a response
cites an existing guideline but its claim isn't supported by that guideline.

The product's own telemetry/adapter must be configured to emit this contract.
The example below is **only a software demonstration**; no claim from it is
medical guidance. Never upload patient identifiers or production clinical
content to a public repository.

## Current boundary

This closes the **trace ingestion and retrieval-metric** gap technically.
It does **not** integrate every commercial API, automatically validate
the reference relevance labels, or prove that the vendor supplied a faithful
trace. If those requirements are unmet, report only exploratory evidence.

A truly comprehensive clinical-RAG qualification additionally needs
independently validated atomic-claim extraction, clinician review of
citation support, case sampling representative of intended use, and
calibrated evaluation across product versions.
