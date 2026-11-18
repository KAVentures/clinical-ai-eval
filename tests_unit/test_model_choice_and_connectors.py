"""Any provider/model by config, and product connectors that are safe to use.

These pin down what a first-time user relies on: credentials by ${VAR} that are
expanded at call time and never saved, judges from providers defined in config,
diversity counted by model developer, and connector failures that name the
problem without echoing response bodies."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from caeval import adapters, llm, pipeline, providers
from caeval import project as P
from caeval.subject import SubjectInvocationError, build_subject

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from fake_endpoints import Server  # noqa: E402


# ---------------------------------------------------------------- providers
def test_custom_openai_compatible_provider_request_shape(monkeypatch):
    registry = llm.provider_registry({"providers": {"local": {
        "api": "openai_chat_completions", "base_url": "http://localhost:8000/v1/",
        "api_key_env": "", "effort_param": ""}}})
    seen = {}

    def fake(url, headers, payload, timeout):
        seen.update(url=url, headers=headers, payload=payload)
        return 200, {"model": "org/m", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}

    monkeypatch.setattr(llm, "_http_json", fake)
    text, meta = providers.call("local", "org/m", "sys", "user", {}, high=False, registry=registry)
    assert text == "hi" and meta["outcome"] == "ok"
    assert seen["url"] == "http://localhost:8000/v1/chat/completions"
    assert "Authorization" not in seen["headers"] and "reasoning_effort" not in seen["payload"]


def test_legacy_high_flag_maps_to_explicit_effort(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "_http_json", lambda u, h, p, t: (seen.update(p=p) or (200, {
        "model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": "x"}]})))
    providers.call("anthropic", "claude-opus-5-5", "s", "u", {"ANTHROPIC_API_KEY": "k"}, max_tokens=100)
    assert seen["p"]["output_config"] == {"effort": "high"} and seen["p"]["max_tokens"] == 8100
    providers.call("anthropic", "claude-opus-5-5", "s", "u", {"ANTHROPIC_API_KEY": "k"}, high=False, max_tokens=100)
    assert "output_config" not in seen["p"] and seen["p"]["max_tokens"] == 100


def test_failure_returns_none_and_never_the_error_body(monkeypatch):
    monkeypatch.setattr(llm, "_http_json", lambda *a: (400, {"error": "echo of PATIENT TEXT"}))
    text, meta = providers.call("openai", "gpt-x", "s", "u", {"OPENAI_API_KEY": "k"}, max_attempts=1)
    assert text is None and meta["outcome"] == "provider_failure"
    assert "PATIENT TEXT" not in json.dumps(meta)


def test_missing_key_is_named_not_a_crash():
    text, meta = providers.call("xai", "grok-x", "s", "u", {}, max_attempts=1)
    assert text is None and "XAI_API_KEY" in meta["error_detail"]


def test_keys_come_from_environment_without_a_keys_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert providers.load_keys(str(tmp_path / "absent.md"))["OPENAI_API_KEY"] == "from-env"
    (tmp_path / "k.md").write_text("OPENAI_API_KEY = from-file\n")
    assert providers.load_keys(str(tmp_path / "k.md"))["OPENAI_API_KEY"] == "from-file"


# ---------------------------------------------------------------- judge panel
def _panel(tmp_path, body: str):
    path = tmp_path / "panel.toml"
    path.write_text(body)
    return path


def test_panel_defines_its_own_provider_and_counts_developers(tmp_path):
    path = _panel(tmp_path, (
        "min_distinct_providers = 2\n"
        "[providers.agg]\napi = \"openai_chat_completions\"\nbase_url = \"https://agg.example/v1\"\n"
        "api_key_env = \"AGG_KEY\"\n"
        "[[judges]]\nname = \"a\"\nprovider = \"agg\"\nmodel = \"x/1\"\nvendor = \"dev_one\"\nmock = false\n"
        "[[judges]]\nname = \"b\"\nprovider = \"agg\"\nmodel = \"y/2\"\nvendor = \"dev_one\"\nmock = false\n"))
    panel = pipeline.load_panel(path)
    assert panel["providers"]["agg"]["api"] == "openai_chat_completions"
    with pytest.raises(ValueError, match="1 distinct"):        # same developer twice is one
        pipeline.assess_panel(panel)
    panel["judges"][1]["vendor"] = "dev_two"
    pipeline.assess_panel(panel)


def test_panel_rejects_unknown_provider_and_bad_effort(tmp_path):
    with pytest.raises(ValueError, match="unknown provider"):
        pipeline.load_panel(_panel(tmp_path, "min_distinct_providers = 1\n[[judges]]\nname = \"a\"\n"
                                              "provider = \"opnai\"\nmodel = \"m\"\nmock = false\n"))
    with pytest.raises(ValueError, match="not valid"):
        pipeline.load_panel(_panel(tmp_path, "min_distinct_providers = 1\n[[judges]]\nname = \"a\"\n"
                                              "provider = \"google\"\nmodel = \"m\"\n"
                                              "reasoning_effort = \"max\"\nmock = false\n"))


def test_shipped_mock_panel_still_loads():
    panel = pipeline.load_panel()
    assert all(j["mock"] for j in panel["judges"])
    pipeline.assess_panel(panel)


# ---------------------------------------------------------------- connectors
def test_env_references_expand_and_missing_ones_fail_closed():
    assert adapters.expand_env({"h": "Bearer ${T}"}, {"T": "abc"}) == {"h": "Bearer abc"}
    with pytest.raises(adapters.AdapterError, match="T_MISSING"):
        adapters.expand_env("Bearer ${T_MISSING}", {})


def test_persisted_spec_keeps_references_and_drops_literal_secrets():
    spec = {"kind": "http", "url": "https://x", "headers": {
        "Authorization": "Bearer ${PRODUCT_TOKEN}", "X-Api-Key": "sk-live-literal", "Accept": "json"},
        "providers": {"g": {"api_key_env": "G_KEY"}}, "token": "literal"}
    out = adapters.persistable(spec)
    assert out["headers"]["Authorization"] == "Bearer ${PRODUCT_TOKEN}"
    assert out["headers"]["X-Api-Key"] == adapters.REDACTED and out["token"] == adapters.REDACTED
    assert out["headers"]["Accept"] == "json"
    assert "sk-live-literal" not in json.dumps(out)


def test_answer_path_walks_lists():
    assert adapters.resolve_answer_path({"choices": [{"message": {"content": "t"}}]},
                                        "choices.0.message.content") == "t"
    with pytest.raises(adapters.AdapterError, match="stopped at '1'"):
        adapters.resolve_answer_path({"choices": [{}]}, "choices.1.message")


def test_http_subject_end_to_end_over_real_http(monkeypatch):
    with Server("tok", "key") as srv:
        spec = {"kind": "http", "url": f"{srv.base}/product/answer", "prompt_field": "question",
                "answer_path": "data.text", "headers": {"Authorization": "Bearer ${ACME_T}"}, "retries": 3}
        with pytest.raises(SubjectInvocationError, match="ACME_T"):
            build_subject(spec)
        monkeypatch.setenv("ACME_T", "wrong")
        with pytest.raises(SubjectInvocationError, match="HTTP 401") as exc:
            build_subject(spec)({"input_text": "case"})
        assert "unauthorized" not in str(exc.value)           # body never echoed
        monkeypatch.setenv("ACME_T", "tok")
        answers = [build_subject(spec)({"input_text": f"case {i}"}) for i in range(12)]
        assert all(a.strip() for a in answers)                 # includes retried 503s


def test_llm_subject_reaches_a_provider_defined_inline(monkeypatch):
    with Server("tok", "key") as srv:
        monkeypatch.setenv("GW_KEY", "key")
        subject = build_subject({"kind": "llm", "provider": "gw", "model": "fake-model-2",
                                 "reasoning_effort": "provider_default",
                                 "providers": {"gw": {"api": "openai_chat_completions",
                                                      "base_url": f"{srv.base}/v1",
                                                      "api_key_env": "GW_KEY", "effort_param": ""}}})
        assert subject({"input_text": "A case"}).strip()


def test_project_accepts_llm_subject_and_rejects_unknown_kind(tmp_path):
    d = P.template("t", "internal_regression")
    d["target"] = {"name": "X", "version": "1", "vendor": "V"}
    d["target_profile"]["types"] = ["clinician_decision_support"]
    d["intake"] = {q: "answered" for q in P.MANDATORY_INTAKE}
    d["governance"] = {q: "yes" for q in P.MANDATORY_GOVERNANCE}
    d["subject"] = {"kind": "llm", "provider": "anthropic", "model": "claude-opus-5-5"}
    import yaml
    (tmp_path / P.PROJECT_FILE).write_text(yaml.safe_dump(d))
    assert P.load(tmp_path).validate() == []
    d["subject"] = {"kind": "lmm", "model": "x"}
    (tmp_path / P.PROJECT_FILE).write_text(yaml.safe_dump(d))
    assert any("not a known connector kind" in p for p in P.load(tmp_path).validate())


def test_code_definitions_resolve_from_the_package_even_when_data_lives_elsewhere(tmp_path, monkeypatch):
    """Installed wheels keep data in caeval/_data; code definitions must still be
    found, or every installed run records them as missing and verification of
    every installed package is INCOMPLETE."""
    import shutil
    from caeval import manifest, util
    data = tmp_path / "data_only"
    data.mkdir()
    shutil.copy(util.repo_root() / "selection_rules.yaml", data / "selection_rules.yaml")
    for rel in ("prompts", "configs"):
        shutil.copytree(util.repo_root() / rel, data / rel)
    monkeypatch.setenv("CAEVAL_HOME", str(data))
    assert not (data / "caeval" / "score.py").exists()
    assert manifest.definition_file("caeval/score.py").exists()
    assert manifest.definition_file("prompts/judge_prompt.txt") == data / "prompts" / "judge_prompt.txt"


def test_conversational_subject_honours_every_mapping_field():
    from caeval.cli import _conversation_spec
    spec = _conversation_spec({"kind": "http", "modality": "conversation", "url": "https://x/chat",
                               "history_field": "turns", "answer_path": "data.reply",
                               "session_field": "sid", "timeout": 30, "retries": 5,
                               "headers": {"Authorization": "Bearer ${T}"}})
    assert (spec.history_field, spec.answer_path, spec.session_field, spec.timeout) == \
        ("turns", "data.reply", "sid", 30)
    assert spec.extra["_retries"] == 5


def test_conversation_with_a_custom_reply_field_over_real_http(monkeypatch):
    from caeval import adapters
    from caeval.cli import _conversation_spec
    with Server("tok", "key") as srv:
        monkeypatch.setenv("T", "tok")
        adapter, report = adapters.connect(_conversation_spec(
            {"kind": "http", "url": f"{srv.base}/product/chat", "answer_path": "reply",
             "headers": {"Authorization": "Bearer ${T}"}}))
        assert report["ok"] and "conversation" in report["capabilities"]
