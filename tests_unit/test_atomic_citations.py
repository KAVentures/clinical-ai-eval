"""Claim-to-citation support cannot be inferred from existence or keyword overlap."""
import json

import pytest

from caeval import atomic_citations as ac


def pack():
    return {
        "schema": "atomic_citation_support_v1",
        "sources": [
            {"source_id": "REF-CURRENT-1", "version": "1", "status": "current",
             "text": "SYNTHETIC source: in scenario Z, action alpha requires further verification."},
            {"source_id": "REF-OLD-2", "version": "0", "status": "superseded",
             "text": "SYNTHETIC withdrawn source: action alpha is always appropriate."},
            {"source_id": "REF-CURRENT-3", "version": "1", "status": "current",
             "text": "SYNTHETIC source: action alpha is explicitly prohibited in scenario Z."},
        ],
        "claims": [
            {"claim_id": "c1", "text": "Action alpha requires verification in scenario Z.",
             "cited_source_ids": ["REF-CURRENT-1"]},
        ],
    }


def annotation(p, reviewer="doctor-one", verdict="supports", kind="clinician",
               quote="SYNTHETIC source: in scenario Z, action alpha requires further verification.",
               source="REF-CURRENT-1", claim="c1"):
    return {
        "claim_id": claim, "source_id": source, "reviewer_id": reviewer,
        "reviewer_kind": kind, "verdict": verdict, "evidence_quote": quote,
        "pack_sha256": ac.canonical_hash(p),
    }


def test_two_agreeing_independent_clinician_labels_are_required_for_support():
    p = pack()
    only_one = ac.score(p, [annotation(p)])
    assert only_one["support"]["supported"] == 0
    assert only_one["support"]["unassessed"] == 1
    assert only_one["support"]["unsupported_claim_rate_on_assessed"] is None
    complete = ac.score(p, [annotation(p), annotation(p, "doctor-two")])
    assert complete["support"]["supported"] == 1
    assert complete["coverage"]["adjudication_fraction"] == 1.0
    assert complete["claim_authority"] == "EXPLORATORY_ONLY"


def test_ai_annotation_does_not_substitute_for_second_clinician():
    p = pack()
    rows = [annotation(p), annotation(p, "model", kind="automated")]
    result = ac.score(p, rows)
    assert result["support"]["unassessed"] == 1
    assert result["coverage"]["n_adjudicated_or_deterministically_invalid"] == 0


def test_disagreeing_clinicians_are_contested_not_supportive():
    p = pack()
    a = annotation(p)
    b = annotation(p, "doctor-two", "does_not_support", quote="")
    report = ac.score(p, [a, b])
    assert report["results"][0]["status"] == "unassessed"
    assert report["support"]["unsupported_claim_rate_on_assessed"] is None


def test_verbatim_quote_is_necessary_but_not_assumed_sufficient_for_support():
    p = pack()
    invalid = annotation(p, quote="This is not in the source")
    with pytest.raises(ValueError, match="verbatim quote"):
        ac.score(p, [invalid])
    valid = annotation(p)
    assert ac.score(p, [valid])["results"][0]["status"] == "unassessed"


def test_unresolvable_and_outdated_references_are_deterministic_defects():
    p = pack()
    p["claims"] = [
        {"claim_id": "c1", "text": "Alpha", "cited_source_ids": ["REF-NOT-FOUND"]},
        {"claim_id": "c2", "text": "Alpha", "cited_source_ids": ["REF-OLD-2"]},
        {"claim_id": "c3", "text": "Alpha", "cited_source_ids": []},
    ]
    report = ac.score(p, [])
    assert report["support"]["unsupported"] == 3
    assert report["support"]["unsupported_claim_rate_on_assessed"] == 1.0
    assert report["citation_integrity"]["claims_with_missing_sources"] == 1
    assert report["citation_integrity"]["claims_with_superseded_sources"] == 1
    assert ac.prepare_review_queue(p) == []


def test_unassessed_citation_is_never_silently_unsupported_or_supported():
    p = pack()
    p["claims"].append({
        "claim_id": "c2", "text": "Other claim", "cited_source_ids": ["REF-CURRENT-3"]})
    report = ac.score(p, [
        annotation(p, "doctor-one"), annotation(p, "doctor-two")])
    assert report["support"]["supported"] == 1
    assert report["support"]["unassessed"] == 1
    assert report["coverage"]["adjudication_fraction"] == 0.5
    assert report["support"]["unsupported_claim_rate_on_assessed"] == 0.0


def test_adjudicated_semantic_non_support_enters_unsupported_denominator():
    p = pack()
    ann = [annotation(p, "doctor-one", "does_not_support", quote=""),
           annotation(p, "doctor-two", "does_not_support", quote="")]
    report = ac.score(p, ann)
    assert report["support"]["unsupported"] == 1
    assert report["support"]["unsupported_claim_rate_on_assessed"] == 1.0


def test_conflicting_current_sources_cannot_read_as_supported():
    p = pack()
    p["claims"][0]["cited_source_ids"] = ["REF-CURRENT-1", "REF-CURRENT-3"]
    rows = [
        annotation(p, "doctor-one"),
        annotation(p, "doctor-two"),
        annotation(p, "doctor-one", "contradicts", source="REF-CURRENT-3",
                   quote="SYNTHETIC source: action alpha is explicitly prohibited in scenario Z."),
        annotation(p, "doctor-two", "contradicts", source="REF-CURRENT-3",
                   quote="SYNTHETIC source: action alpha is explicitly prohibited in scenario Z."),
    ]
    report = ac.score(p, rows)
    assert report["support"]["conflicting_evidence"] == 1
    assert report["coverage"]["n_unassessed_or_conflicting"] == 1


def test_duplicate_reviewers_rejected_and_pack_hash_binding_is_required():
    p = pack()
    one = annotation(p)
    with pytest.raises(ValueError, match="duplicate reviewer"):
        ac.score(p, [one, one])
    bad = annotation(p)
    bad["pack_sha256"] = "stale"
    with pytest.raises(ValueError, match="pack hash"):
        ac.score(p, [bad])


def test_prepare_does_not_leak_any_prior_labels():
    p = pack()
    queue = ac.prepare_review_queue(p)
    assert len(queue) == 1
    assert "verdict" not in queue[0]
    assert "reviewer_id" not in queue[0]
    assert queue[0]["pack_sha256"] == ac.canonical_hash(p)


def test_cli_roundtrip_and_incomplete_review_exit_code(tmp_path):
    p = pack()
    claims = tmp_path / "claims.json"
    queue = tmp_path / "review_queue.jsonl"
    reviews = tmp_path / "annotations.jsonl"
    output = tmp_path / "report.json"
    claims.write_text(json.dumps(p))
    ac.main(["prepare", "--claims", str(claims), "--out", str(queue)])
    assert len(ac.read_jsonl(queue)) == 1

    ac.write_jsonl(reviews, [annotation(p)])
    with pytest.raises(SystemExit, match="Unassessed"):
        ac.main(["score", "--claims", str(claims), "--reviews", str(reviews),
                 "--out", str(output)])
    assert json.loads(output.read_text())["support"]["unassessed"] == 1

    ac.write_jsonl(reviews, [annotation(p), annotation(p, "doctor-two")])
    ac.main(["score", "--claims", str(claims), "--reviews", str(reviews),
             "--out", str(output)])
    assert json.loads(output.read_text())["support"]["supported"] == 1


@pytest.mark.parametrize("bad", [0, 1, -1, 1.5, "2", True])
def test_do_not_reduce_clinician_quorum_to_one(bad):
    with pytest.raises(ValueError, match="at least two"):
        ac.score(pack(), [], min_clinicians=bad)
