"""Observed vendor RAG traces are distinct from the synthetic in-harness retriever."""
import json

import pytest

from caeval.rag import vendor_trace as vt


def sample():
    return {
        "schema": "observed_rag_trace_v1",
        "target": {"name": "synthetic-vendor", "version": "sha-001"},
        "sources": [
            {"source_id": "DOC-A", "version": "2", "status": "current",
             "text": "Synthetic source A: answer-specific evidence."},
            {"source_id": "DOC-B", "version": "1", "status": "current",
             "text": "Synthetic source B: irrelevant context."},
            {"source_id": "DOC-OLD", "version": "0", "status": "superseded",
             "text": "Synthetic older source: outdated recommendation."},
        ],
        "traces": [
            {
                "query_id": "q1", "query": "What does synthetic A say?",
                "retrieval_query": "synthetic A",
                "reference_supporting_source_ids": ["DOC-A"],
                "reference_review_status": "candidate",
                "retrieved_chunks": [
                    {"rank": 1, "source_id": "DOC-B", "source_version": "1",
                     "text": "Irrelevant text"},
                    {"rank": 2, "source_id": "DOC-A", "source_version": "2",
                     "text": "Evidence excerpt"},
                    {"rank": 3, "source_id": "DOC-OLD", "source_version": "0",
                     "text": "Old excerpt"},
                ],
                "model_status": "complete", "answer": "A claim [DOC-A]",
                "atomic_claims": [
                    {"claim_id": "a", "text": "Synthetic A asserts something",
                     "cited_source_ids": ["DOC-A"]}
                ],
            },
        ],
    }


def test_actual_retriever_ranking_is_used_instead_of_in_house_retriever():
    pack = sample()
    r = vt.score_tracepack(pack, k=2)
    assert r["summary"]["macro_recall_at_k"] == 1.0
    assert r["summary"]["macro_precision_at_k"] == 0.5
    assert r["summary"]["mean_reciprocal_rank_at_k"] == 0.5
    assert r["summary"]["n_completed_answers"] == 1
    assert r["claim_authority"] == "EXPLORATORY_ONLY"
    assert r["rows"][0]["retrieval_query_changed"] is True


def test_missing_relevant_hit_is_measured_as_failure_not_answer_correctness():
    pack = sample()
    r = vt.score_tracepack(pack, k=1)
    assert r["summary"]["macro_recall_at_k"] == 0.0
    assert r["summary"]["mean_reciprocal_rank_at_k"] == 0.0
    assert r["rows"][0]["raw_answer_scored_for_clinical_correctness"] is False


def test_superseded_version_and_unretrieved_citation_are_explicit():
    pack = sample()
    pack["traces"][0]["retrieved_chunks"][1]["source_version"] = "old-version"
    pack["traces"][0]["atomic_claims"][0]["cited_source_ids"] = [
        "DOC-A", "DOC-NONEXISTENT"]
    r = vt.score_tracepack(pack)
    row = r["rows"][0]
    assert "DOC-OLD" in row["superseded_source_ids_retrieved"]
    assert "DOC-A" in row["stale_source_version_ids"]
    assert "DOC-NONEXISTENT" in row["citation_source_not_in_catalog"]
    assert r["summary"]["n_traces_with_unresolvable_citation"] == 1


def test_provider_failure_not_scored_as_response_or_support():
    pack = sample()
    pack["traces"][0]["model_status"] = "provider_error"
    pack["traces"][0]["answer"] = ""
    r = vt.score_tracepack(pack)
    assert r["summary"]["n_provider_failures"] == 1
    assert r["summary"]["n_completed_answers"] == 0
    with pytest.raises(ValueError, match="No atomic claims"):
        vt.citation_review_pack(pack)


def test_response_without_claims_cannot_be_misrepresented_as_full_citation_review():
    pack = sample()
    pack["traces"][0]["atomic_claims"] = []
    assert vt.score_tracepack(pack)["rows"][0]["claim_extraction_completeness"] == "UNVERIFIED"
    with pytest.raises(ValueError, match="No atomic claims"):
        vt.citation_review_pack(pack)


def test_citation_review_bridge_preserves_pinned_claim_and_source_text():
    pack = sample()
    c = vt.citation_review_pack(pack)
    assert c["schema"] == "atomic_citation_support_v1"
    assert c["claims"] == [{"claim_id": "q1::a",
                             "text": "Synthetic A asserts something",
                             "cited_source_ids": ["DOC-A"]}]
    assert c["sources"] == pack["sources"]


def test_rag_trace_requires_real_observed_rank_and_explicit_current_reference():
    pack = sample()
    pack["traces"][0]["retrieved_chunks"][1]["rank"] = 1
    with pytest.raises(ValueError, match="contiguous and unique"):
        vt.validate_tracepack(pack)

    pack = sample()
    pack["traces"][0]["reference_supporting_source_ids"] = ["DOC-OLD"]
    with pytest.raises(ValueError, match="current known sources"):
        vt.validate_tracepack(pack)

    pack = sample()
    pack["traces"][0]["retrieval_query"] = None
    with pytest.raises(ValueError, match="observed retrieval_query"):
        vt.validate_tracepack(pack)


def test_failed_answer_never_gets_an_unearned_good_generation_score():
    pack = sample()
    pack["traces"][0]["model_status"] = "timeout"
    pack["traces"][0]["answer"] = ""
    r = vt.score_tracepack(pack)
    assert r["summary"]["n_provider_failures"] == 1
    assert r["summary"]["macro_recall_at_k"] == 1.0  # retrieval independent
    assert r["rows"][0]["raw_answer_scored_for_clinical_correctness"] is False


def test_clinician_review_flag_requires_named_person_but_is_not_a_certificate():
    pack = sample()
    pack["traces"][0]["reference_review_status"] = "clinician_reviewed"
    with pytest.raises(ValueError, match="named reviewer"):
        vt.validate_tracepack(pack)
    pack["traces"][0]["reference_reviewer"] = "Doctor"
    r = vt.score_tracepack(pack)
    assert r["provenance"]["n_clinician_reviewed_trace_references"] == 1
    assert r["claim_authority"] == "EXPLORATORY_ONLY"


def test_cli_writes_trace_report_and_atomic_citation_pack(tmp_path):
    pack = sample()
    fp = tmp_path / "observed.json"
    out = tmp_path / "audit.json"
    cit = tmp_path / "claims.json"
    fp.write_text(json.dumps(pack))
    vt.main(["--traces", str(fp), "--out", str(out),
             "--citation-pack-out", str(cit), "--k", "2"])
    assert json.loads(out.read_text())["summary"]["macro_recall_at_k"] == 1.0
    assert json.loads(cit.read_text())["claims"][0]["claim_id"] == "q1::a"


def test_unanswerable_reference_query_does_not_count_as_perfect_recall():
    pack = sample()
    pack["traces"][0]["reference_supporting_source_ids"] = []
    r = vt.score_tracepack(pack)
    assert r["summary"]["macro_recall_at_k"] is None
    assert r["summary"]["n_answerable_reference_queries"] == 0
