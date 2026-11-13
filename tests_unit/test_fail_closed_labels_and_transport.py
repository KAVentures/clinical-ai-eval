"""Fail-closed checks for the two clinical measurement ingestion boundaries.

All tests are offline. They protect denominator and label semantics, not
clinical validity of an evaluator or its prompts.
"""
import json

import pytest

from caeval import pipeline, providers
from caeval.score import BINARY_FIELDS, normalize_score, parse_judge_json
from caeval.subject import SubjectInvocationError, build_subject


def valid_score():
    return {
        **{key: 0 for key in BINARY_FIELDS},
        "confidence_level": "low",
        "rationale_short": "Test fixture",
        "quote_support": "I would ask for the value first.",
    }


@pytest.mark.parametrize("invalid", [-1, 2, "0", "1", 0.3, None, True, False])
def test_invalid_binary_judge_label_is_not_cast_to_safe_or_unsafe(invalid):
    labels = valid_score()
    labels["unsafe_overconfident"] = invalid
    with pytest.raises(ValueError, match="nonbinary"):
        parse_judge_json(json.dumps(labels))
    with pytest.raises(ValueError, match="integer 0 or 1"):
        normalize_score(labels)


def test_invalid_binary_fields_are_not_ignored():
    labels = valid_score()
    labels["correct_abstention"] = 2
    with pytest.raises(ValueError, match="correct_abstention"):
        parse_judge_json(json.dumps(labels))


def test_missing_label_fails_closed():
    labels = valid_score()
    del labels["potentially_harmful_treatment"]
    with pytest.raises(ValueError, match="potentially_harmful_treatment"):
        parse_judge_json(json.dumps(labels))


def test_self_contradictory_abstention_is_not_silently_corrected():
    labels = valid_score()
    labels["correct_abstention"] = 1
    labels["excessive_abstention"] = 1
    with pytest.raises(ValueError, match="both correct and excessive"):
        parse_judge_json(json.dumps(labels))


def test_valid_real_judge_binary_labels_survive_exactly():
    labels = valid_score()
    labels["unsafe_overconfident"] = 1
    labels["guideline_concordant_next_step"] = 1
    assert parse_judge_json(json.dumps(labels)) == labels


def test_malformed_real_judge_vote_does_not_count_toward_quorum(monkeypatch):
    bad = valid_score()
    bad["unsafe_overconfident"] = 2

    monkeypatch.setattr(providers, "call",
                        lambda *_args, **_kwargs: (json.dumps(bad), {"status": 200}))
    judges = [
        {"name": "a", "provider": "openai", "model": "demo", "mock": False},
        {"name": "b", "provider": "anthropic", "model": "demo", "mock": False},
    ]
    record = {
        "cell_id": "case::variant", "item_id": "case", "kind": "variant",
        "perturbation_type": "missing_critical_lab", "severity": "high",
        "expected_missing_evidence": "renal function",
        "input_text": "Question: what treatment is appropriate?",
        "response_text": "Get another measurement.",
    }
    cell = pipeline._score_cell(
        record, judges, {"OPENAI_API_KEY": "not-real",
                         "ANTHROPIC_API_KEY": "not-real"}, min_distinct=2)
    assert cell["evaluation_complete"] is False
    assert cell["panel_any_unsafe"] is None
    assert len(cell["judge_errors"]) == 2
    assert cell["judge_scores"] == {}


def test_one_judge_exception_is_an_unavailable_vote_not_a_safe_one(monkeypatch):
    def failed(*_args, **_kwargs):
        raise RuntimeError("provider outage; test-private-secret")

    monkeypatch.setattr(providers, "score_response", failed)
    record = {
        "cell_id": "case::original", "item_id": "case", "kind": "original",
        "perturbation_type": "original", "severity": "n/a",
        "expected_missing_evidence": "",
        "input_text": "Question", "response_text": "Response",
    }
    judges = [{"name": "a", "provider": "openai", "model": "m", "mock": False}]
    cell = pipeline._score_cell(record, judges, {}, min_distinct=1)
    assert cell["evaluation_complete"] is False
    assert cell["panel_any_unsafe"] is None
    assert "test-private-secret" not in str(cell["judge_errors"])
    assert "RuntimeError" in str(cell["judge_errors"])


@pytest.mark.parametrize("response", [None, "", "   ", 0])
def test_subject_transport_failure_is_never_scored_as_empty_response(monkeypatch, response):
    monkeypatch.setattr(providers, "load_keys",
                        lambda: {"OPENAI_API_KEY": "dummy"})
    monkeypatch.setattr(providers, "call",
                        lambda *_args, **_kwargs: (response, {"status": 429}))
    subject = build_subject({"kind": "openai", "model": "example"})
    with pytest.raises(SubjectInvocationError):
        subject({"input_text": "A clinical case"})


def test_valid_subject_response_is_preserved(monkeypatch):
    monkeypatch.setattr(providers, "load_keys",
                        lambda: {"OPENAI_API_KEY": "dummy"})
    monkeypatch.setattr(providers, "call",
                        lambda *_args, **_kwargs: ("Request creatinine.", {}))
    subject = build_subject({"kind": "openai", "model": "example"})
    assert subject({"input_text": "A clinical case"}) == "Request creatinine."
