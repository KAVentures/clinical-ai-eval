"""Audit REAL vendor-provided RAG retrieval traces, not a simulated retriever.

This import contract is intentionally explicit: the vendor supplies observed
retrieval chunks, query rewriting, generation outcome, answer and citations;
an independent case author supplies the current source catalog and supporting
source IDs. The tool never guesses document relevance or clinical correctness.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


STATUSES = {"current", "superseded"}
OUTCOMES = {"complete", "timeout", "provider_error"}


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _id_list(value, context):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip()
                                          for x in value):
        raise ValueError(f"{context} must be a list of nonempty IDs")
    if len(value) != len(set(value)):
        raise ValueError(f"{context} contains duplicate IDs")
    return value


def validate_tracepack(pack: dict) -> dict:
    if not isinstance(pack, dict) or pack.get("schema") != "observed_rag_trace_v1":
        raise ValueError("Unknown observed RAG trace schema")
    target = pack.get("target")
    if not isinstance(target, dict) or any(
        not isinstance(target.get(k), str) or not target[k].strip()
        for k in ("name", "version")
    ):
        raise ValueError("Target name and version are required")
    catalog = pack.get("sources")
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("A version-pinned source catalog is required")
    sources = {}
    for source in catalog:
        if not isinstance(source, dict):
            raise ValueError("source must be object")
        sid = source.get("source_id")
        if not isinstance(sid, str) or not sid.strip() or sid in sources:
            raise ValueError("source_id must be unique and nonempty")
        if source.get("status") not in STATUSES:
            raise ValueError(f"{sid}: source status must be current or superseded")
        if not isinstance(source.get("version"), str) or not source["version"].strip():
            raise ValueError(f"{sid}: source version required")
        if not isinstance(source.get("text"), str) or not source["text"].strip():
            raise ValueError(f"{sid}: source text required for citation review")
        sources[sid] = source

    traces = pack.get("traces")
    if not isinstance(traces, list) or not traces:
        raise ValueError("traces must be a nonempty list")
    seen = set()
    for trace in traces:
        if not isinstance(trace, dict):
            raise ValueError("trace must be object")
        qid = trace.get("query_id")
        if not isinstance(qid, str) or not qid.strip() or qid in seen:
            raise ValueError("query_id must be unique and nonempty")
        seen.add(qid)
        if not isinstance(trace.get("query"), str) or not trace["query"].strip():
            raise ValueError(f"{qid}: input query required")
        if not isinstance(trace.get("retrieval_query"), str):
            raise ValueError(f"{qid}: observed retrieval_query required")
        supporting = _id_list(trace.get("reference_supporting_source_ids"),
                              f"{qid}.reference_supporting_source_ids")
        if any(sid not in sources or sources[sid]["status"] != "current"
               for sid in supporting):
            raise ValueError(f"{qid}: reference supporting IDs must be current known sources")

        chunks = trace.get("retrieved_chunks")
        if not isinstance(chunks, list):
            raise ValueError(f"{qid}: observed retrieved_chunks required, possibly empty")
        ranks = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                raise ValueError(f"{qid}: chunk must be object")
            if not isinstance(chunk.get("source_id"), str) or not chunk["source_id"].strip():
                raise ValueError(f"{qid}: retrieved chunk source_id required")
            if not isinstance(chunk.get("source_version"), str):
                raise ValueError(f"{qid}: retrieved source_version required")
            if not isinstance(chunk.get("text"), str) or not chunk["text"].strip():
                raise ValueError(f"{qid}: retrieved chunk text required")
            rank = chunk.get("rank")
            if type(rank) is not int or rank < 1:
                raise ValueError(f"{qid}: rank must be positive integer")
            ranks.append(rank)
        if sorted(ranks) != list(range(1, len(chunks) + 1)):
            raise ValueError(f"{qid}: retrieval ranks must be contiguous and unique")

        if trace.get("model_status") not in OUTCOMES:
            raise ValueError(f"{qid}: model_status must be complete/timeout/provider_error")
        if trace["model_status"] == "complete" and (
            not isinstance(trace.get("answer"), str) or not trace["answer"].strip()
        ):
            raise ValueError(f"{qid}: completed model must have nonempty answer")
        review = trace.get("reference_review_status", "candidate")
        if review not in ("candidate", "clinician_reviewed"):
            raise ValueError(f"{qid}: unknown reference review status")
        if review == "clinician_reviewed" and not str(trace.get("reference_reviewer", "")).strip():
            raise ValueError(f"{qid}: clinician_reviewed requires named reviewer")

        claims = trace.get("atomic_claims", [])
        if not isinstance(claims, list):
            raise ValueError(f"{qid}: atomic_claims must be a list")
        ids = set()
        for claim in claims:
            cid = claim.get("claim_id") if isinstance(claim, dict) else None
            if not isinstance(cid, str) or not cid.strip() or cid in ids:
                raise ValueError(f"{qid}: claim IDs must be unique and nonempty")
            ids.add(cid)
            if not isinstance(claim.get("text"), str) or not claim["text"].strip():
                raise ValueError(f"{qid}: atomic claim text required")
            _id_list(claim.get("cited_source_ids"),
                     f"{qid}/{cid}.cited_source_ids")
    return {
        "n_traces": len(traces), "n_catalog_sources": len(sources),
        "tracepack_sha256": _hash(pack),
        "n_clinician_reviewed_trace_references": sum(
            t.get("reference_review_status") == "clinician_reviewed"
            for t in traces),
        "status": "STRUCTURAL_TRACE_INTEGRITY_ONLY",
    }


def _avg(values):
    return round(sum(values) / len(values), 6) if values else None


def score_tracepack(pack: dict, *, k: int = 5) -> dict:
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    meta = validate_tracepack(pack)
    sources = {s["source_id"]: s for s in pack["sources"]}
    rows = []
    recall, precision, reciprocal = [], [], []
    for trace in pack["traces"]:
        chunks = sorted(trace["retrieved_chunks"], key=lambda c: c["rank"])
        top = chunks[:k]
        supported = set(trace["reference_supporting_source_ids"])
        retrieved = [chunk["source_id"] for chunk in top]
        retrieved_all = {chunk["source_id"] for chunk in chunks}
        retrieved_supporting = set(retrieved) & supported
        answerable = bool(supported)
        if answerable:
            recall.append(len(retrieved_supporting) / len(supported))
            precision.append(sum(sid in supported for sid in retrieved) / len(retrieved)
                             if retrieved else 0.0)
            first = next((rank for rank, sid in enumerate(retrieved, 1)
                          if sid in supported), None)
            reciprocal.append(1 / first if first else 0.0)
        superseded_hits = sorted({sid for sid in retrieved_all
                                  if sid in sources and sources[sid]["status"] != "current"})
        unknown_hits = sorted(retrieved_all - set(sources))
        stale_hits = sorted({c["source_id"] for c in chunks
                            if c["source_id"] in sources
                            and c["source_version"] != sources[c["source_id"]]["version"]})
        # These source-ID checks do not pretend to determine semantic support
        # of the generated answer. That is handled by atomic_citations.
        missing_cites, obsolete_cites, unreturned_cites = set(), set(), set()
        for claim in trace.get("atomic_claims", []):
            for sid in claim["cited_source_ids"]:
                if sid not in sources:
                    missing_cites.add(sid)
                elif sources[sid]["status"] != "current":
                    obsolete_cites.add(sid)
                elif sid not in retrieved_all:
                    unreturned_cites.add(sid)
        rows.append({
            "query_id": trace["query_id"], "model_status": trace["model_status"],
            "reference_status": trace.get("reference_review_status", "candidate"),
            "retrieval_query_changed": trace["retrieval_query"] != trace["query"],
            "n_retrieved_chunks": len(chunks),
            "supporting_source_recall_at_k": (
                round(len(retrieved_supporting) / len(supported), 6)
                if answerable else None),
            "first_supporting_rank_at_k": (
                next((i for i, sid in enumerate(retrieved, 1)
                      if sid in supported), None)),
            "n_unknown_sources_retrieved": len(unknown_hits),
            "unknown_source_ids": unknown_hits,
            "superseded_source_ids_retrieved": superseded_hits,
            "stale_source_version_ids": stale_hits,
            "citation_source_not_in_catalog": sorted(missing_cites),
            "citation_source_superseded": sorted(obsolete_cites),
            "citation_source_not_in_retrieval": sorted(unreturned_cites),
            "n_atomic_claims": len(trace.get("atomic_claims", [])),
            "claim_extraction_completeness": "UNVERIFIED",
            "raw_answer_scored_for_clinical_correctness": False,
        })

    return {
        "schema": "observed_rag_retrieval_report_v1",
        "target": pack["target"],
        "provenance": meta,
        "claim_authority": "EXPLORATORY_ONLY",
        "summary": {
            "n_traces": len(rows),
            "n_completed_answers": sum(r["model_status"] == "complete" for r in rows),
            "n_provider_failures": sum(r["model_status"] != "complete" for r in rows),
            "n_answerable_reference_queries": len(recall),
            "macro_recall_at_k": _avg(recall),
            "macro_precision_at_k": _avg(precision),
            "mean_reciprocal_rank_at_k": _avg(reciprocal),
            "n_traces_with_superseded_retrieval": sum(
                bool(r["superseded_source_ids_retrieved"]) for r in rows),
            "n_traces_with_source_version_mismatch": sum(
                bool(r["stale_source_version_ids"]) for r in rows),
            "n_traces_with_unresolvable_citation": sum(
                bool(r["citation_source_not_in_catalog"]) for r in rows),
            "n_traces_citing_unretrieved_sources": sum(
                bool(r["citation_source_not_in_retrieval"]) for r in rows),
        },
        "rows": rows,
        "limitations": [
            "Observed retrieval trace is supplied by the vendor; the tool does not independently verify runtime instrumentation.",
            "Supporting source IDs are user-supplied reference labels and require independent clinical review.",
            "Precision assumes the supplied supporting source list is exhaustive.",
            "Model claim extraction must be independently verified for coverage.",
            "No claim-to-source semantic support or clinical answer correctness is inferred here.",
        ],
    }


def citation_review_pack(pack: dict) -> dict:
    """Bridge observed vendor responses to the separate atomic citation auditor.

    Caller must supply atomic claims: extracting them from free-form medical
    answers requires a separate validated process, not a regex shortcut.
    """
    validate_tracepack(pack)
    claims = []
    for trace in pack["traces"]:
        if trace["model_status"] != "complete":
            continue
        for c in trace.get("atomic_claims", []):
            claims.append({
                "claim_id": f"{trace['query_id']}::{c['claim_id']}",
                "text": c["text"],
                "cited_source_ids": c["cited_source_ids"],
            })
    if not claims:
        raise ValueError("No atomic claims were supplied; cannot pretend there is citation coverage")
    return {"schema": "atomic_citation_support_v1",
            "sources": pack["sources"], "claims": claims}


def main(argv=None) -> None:
    import argparse
    p = argparse.ArgumentParser(prog="python -m caeval.rag.vendor_trace")
    p.add_argument("--traces", required=True, help="observed product tracepack JSON")
    p.add_argument("--out", required=True)
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--citation-pack-out",
                   help="optional claim-source pack for clinical-citation-audit")
    args = p.parse_args(argv)
    pack = json.loads(Path(args.traces).read_text(encoding="utf-8"))
    report = score_tracepack(pack, k=args.k)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True,
                              ensure_ascii=False), encoding="utf-8")
    if args.citation_pack_out:
        citations = citation_review_pack(pack)
        path = Path(args.citation_pack_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(citations, indent=2, sort_keys=True,
                                   ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
