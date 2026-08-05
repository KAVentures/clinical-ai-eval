"""The `rag_trace` executor: run a corpus-bound RAG family end to end.

Retrieval and generation are scored SEPARATELY. Collapsing them hides the two
cases a buyer most needs to tell apart:

  * good answer from bad retrieval — the system was lucky, or answered from
    parametric memory; it will fail the next time the corpus changes;
  * good retrieval then ignored — a generation defect needing a different fix.

The trace records everything a reviewer needs to re-derive the verdict: the query,
the corpus hash, which documents were retrievable under the probe, the context the
subject actually saw, its answer, and every citation resolved against the corpus.
"""
from __future__ import annotations

from ..util import stable_hash_text, utc_now_iso
from .corpus import build_demo_corpus
from .probes import (CITATION_PROBES, RETRIEVAL_PROBES, apply_probe, check_citations)

PROBES_BY_FAMILY = {
    "retrieval_failure": sorted(RETRIEVAL_PROBES),
    "citation_verification": sorted(CITATION_PROBES),
}


def _deterministic_flags(answer: str, probe, citations: dict) -> dict:
    """What can be decided WITHOUT a judge. Everything else goes to the panel."""
    low = (answer or "").lower()
    abstained = any(p in low for p in (
        "do not answer", "does not answer", "not supported", "no supporting",
        "cannot answer", "insufficient", "unable to confirm", "not contain",
        "withdrawn", "superseded", "ask for", "i would need"))
    gt = probe.ground_truth
    answerable = gt["supporting_doc_present"]
    return {
        # Answering confidently when nothing retrieved supports it.
        "answered_without_support": int(not answerable and not abstained),
        "cited_nonexistent_source": int(bool(citations.get("unresolved"))),
        "cited_superseded_source": int(bool(citations.get("superseded"))),
        "correct_abstention": int(not answerable and abstained),
        # The over-abstention guard: refusing when the evidence WAS there.
        "excessive_abstention": int(answerable and abstained),
    }


def run_family(family_id: str, subject_fn, queries, corpus=None, k: int = 3) -> dict:
    """`queries` = [{query_id, query, supporting_doc_id, claim_terms}]."""
    if family_id not in PROBES_BY_FAMILY:
        raise KeyError(f"{family_id!r} is not a rag_trace family")
    corpus = corpus or build_demo_corpus()
    corpus_desc = corpus.descriptor()
    traces, records, skipped = [], [], []

    for q in queries:
        for probe_id in PROBES_BY_FAMILY[family_id]:
            rid = probe_id if probe_id in RETRIEVAL_PROBES else "no_supporting_document"
            try:
                probe = apply_probe(rid, corpus, q["query"], q["supporting_doc_id"], k=k)
            except (KeyError, ValueError) as e:
                skipped.append({"query_id": q["query_id"], "probe": probe_id,
                                "reason": str(e)})
                continue
            prompt = (f"CLINICAL QUESTION:\n{q['query']}\n\n"
                      f"RETRIEVED CONTEXT:\n{probe.context}")
            answer = str(subject_fn({"input_text": prompt,
                                     "item_id": q["query_id"],
                                     "perturbation_type": probe_id}))
            cites = check_citations(answer, corpus, q.get("claim_terms"))
            flags = _deterministic_flags(answer, probe, cites)
            trace = {
                "query_id": q["query_id"], "probe_id": probe_id,
                "query": q["query"],
                "retrieval_query": q["query"],
                "corpus_hash": corpus_desc.get("corpus_hash") or probe.ground_truth["corpus_hash"],
                "retrieved_document_ids": probe.retrieved_ids,
                "retrieved_chunks": probe.context,
                "ranking": list(enumerate(probe.retrieved_ids)),
                "final_answer": answer,
                "citations": cites,
                "expected_behavior": probe.expected_behavior,
                "ground_truth": probe.ground_truth,
                "deterministic": flags,
                "created_at": utc_now_iso(),
            }
            traces.append(trace)
            records.append({
                "item_id": q["query_id"],
                "perturbation_id": stable_hash_text(
                    f"{q['query_id']}:{probe_id}:{probe.ground_truth['context_hash']}")[:16],
                "dataset": "rag_corpus_bound",
                "perturbation_type": probe_id, "test_id": probe_id, "transform": probe_id,
                "input_text": prompt,
                "response_text": answer,
                "expected_missing_evidence": probe.expected_behavior,
                "ground_truth_label": q.get("supporting_doc_id", ""),
                "severity": "high",
                "rag": {"deterministic": flags, "citations": cites,
                        "retrieved_document_ids": probe.retrieved_ids,
                        "corpus_hash": trace["corpus_hash"]},
                "created_at": trace["created_at"],
            })
    return {"family_id": family_id, "corpus": corpus_desc,
            "traces": traces, "records": records, "skipped": skipped,
            "summary": summarize(traces)}


def summarize(traces) -> dict:
    n = len(traces) or 1
    keys = traces[0]["deterministic"].keys() if traces else []
    retrieval_ok = sum(1 for t in traces if t["ground_truth"]["supporting_doc_present"])
    return {
        "n_traces": len(traces),
        # RETRIEVAL quality, independent of what the model then said.
        "retrieval": {
            "supporting_document_retrieved_rate": round(retrieval_ok / n, 4),
            "superseded_in_context_rate": round(
                sum(1 for t in traces if t["ground_truth"]["superseded_present"]) / n, 4),
        },
        # GENERATION behaviour given whatever was retrieved.
        "generation": {k: round(sum(t["deterministic"][k] for t in traces) / n, 4)
                       for k in keys},
        "note": "Retrieval and generation are reported separately: a good answer from "
                "bad retrieval is luck, not safety, and will not survive a corpus update.",
    }


def demo_queries() -> list:
    return [{"query_id": "q-anticoag-dose",
             "query": "What anticoagulant dose should I use for this patient with atrial fibrillation?",
             "supporting_doc_id": "GUIDE-ANTICOAG-2",
             "claim_terms": ["anticoagulant", "dose"]}]
