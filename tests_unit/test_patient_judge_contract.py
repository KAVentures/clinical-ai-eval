"""Clinical patient-episode judge contract, with no external API calls.

Real-model patient rubric responses must not be parsed using the unrelated
clinician-facing single-answer schema. Missing fields must fail closed.
"""
import json

import pytest

from caeval import providers
from caeval.patient import judging


def good_labels():
    return {
        **{field: 0 for field in judging.SCHEMA_FIELDS},
        "final_disposition": "routine",
        "confidence_level": "moderate",
        "rationale_short": "No unsupported commitment",
        "quote_support": "Please tell me more.",
    }


PAYLOAD = {
    "transcript": [
        {"turn": 1, "speaker": "patient", "text": "I have a question."},
        {"turn": 2, "speaker": "system", "text": "Please tell me more."},
    ],
    "facts_available_before_each_system_turn": [
        {"turn": 2, "facts_known": []},
    ],
}

REAL = {
    "name": "real_judge", "provider": "openai", "model": "example-model",
    "mock": False, "mode": "blinded",
}


def test_patient_parser_accepts_prompt_conformant_response():
    labels = good_labels()
    parsed = judging.parse_patient_judge_json(json.dumps(labels))
    assert parsed == labels
    assert "unsafe_overconfident" not in parsed


@pytest.mark.parametrize("missing", judging.SCHEMA_FIELDS + [
    "final_disposition", "confidence_level", "rationale_short", "quote_support"
])
def test_patient_parser_rejects_all_missing_required_fields(missing):
    labels = good_labels()
    del labels[missing]
    with pytest.raises(ValueError):
        judging.parse_patient_judge_json(json.dumps(labels))


@pytest.mark.parametrize("invalid", [-1, 2, "0", None, True, False, 0.0])
def test_patient_parser_requires_true_binary_integer_labels(invalid):
    labels = good_labels()
    labels["missed_red_flag"] = invalid
    with pytest.raises(ValueError, match="missed_red_flag"):
        judging.parse_patient_judge_json(json.dumps(labels))


@pytest.mark.parametrize("field,value", [
    ("final_disposition", "guess"),
    ("final_disposition", None),
    ("confidence_level", "certain"),
    ("rationale_short", None),
    ("quote_support", 7),
])
def test_patient_parser_rejects_malformed_metadata(field, value):
    labels = good_labels()
    labels[field] = value
    with pytest.raises(ValueError):
        judging.parse_patient_judge_json(json.dumps(labels))


def test_generic_clinician_schema_cannot_be_substituted():
    from caeval.score import BINARY_FIELDS
    generic = {field: 0 for field in BINARY_FIELDS}
    with pytest.raises(ValueError, match="under_triage"):
        judging.parse_patient_judge_json(json.dumps(generic))


def test_real_patient_judge_response_follows_its_own_schema(monkeypatch):
    labels = good_labels()
    calls = []
    monkeypatch.setattr(providers, "load_keys",
                        lambda: {"OPENAI_API_KEY": "dummy-offline-key"})

    def fake_provider_call(provider, model, prompt, user_content, keys):
        assert keys == {"OPENAI_API_KEY": "dummy-offline-key"}
        calls.append((provider, model, prompt, user_content))
        return json.dumps(labels), {"request_id": "local-test"}

    monkeypatch.setattr(providers, "call", fake_provider_call)
    actual, meta = judging.score_episode(REAL, PAYLOAD)
    assert actual == labels
    assert meta["request_id"] == "local-test"
    assert calls and "under_triage" in calls[0][2]
    assert calls[0][0] == "openai"


def test_malformed_real_patient_reply_does_not_count_as_negative(monkeypatch):
    def fake_provider_call(*_args):
        bad = good_labels()
        del bad["under_triage"]
        return json.dumps(bad), {"request_id": "invalid-local-test"}

    monkeypatch.setattr(providers, "call", fake_provider_call)
    monkeypatch.setattr(providers, "load_keys",
                        lambda: {"OPENAI_API_KEY": "dummy-offline-key"})
    actual, meta = judging.score_episode(REAL, PAYLOAD)
    assert actual is None
    assert "parse_error" in meta and "under_triage" in meta["parse_error"]


def test_offline_mock_patient_judges_obey_same_schema():
    mock = {**REAL, "provider": "mock_a", "mock": True}
    labels, meta = judging.score_episode(mock, PAYLOAD)
    assert labels == judging.validate_patient_judge_labels(labels)
    assert meta["status"] == "mock"


def test_explicit_credentials_bypass_default_keyfile(monkeypatch):
    def no_keyfile():
        raise AssertionError("explicit keys must not read the default key file")

    def fake_provider_call(_provider, _model, _prompt, _user, keys):
        assert keys == {"OPENAI_API_KEY": "explicit-offline-key"}
        return json.dumps(good_labels()), {"status": 200}

    monkeypatch.setattr(providers, "load_keys", no_keyfile)
    monkeypatch.setattr(providers, "call", fake_provider_call)
    labels, _meta = judging.score_episode(
        REAL, PAYLOAD, keys={"OPENAI_API_KEY": "explicit-offline-key"})
    assert labels == good_labels()


def test_mock_patient_judge_never_reads_real_credentials(monkeypatch):
    monkeypatch.setattr(providers, "load_keys",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("mock may not require keys")))
    mock = {**REAL, "provider": "mock_a", "mock": True}
    labels, _meta = judging.score_episode(mock, PAYLOAD)
    assert labels["under_triage"] in (0, 1)
