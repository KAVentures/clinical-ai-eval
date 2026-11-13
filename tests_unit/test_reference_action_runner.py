"""No-network regression tests for explicitly bounded clinical action execution."""
import json

import pytest

from caeval import providers
from caeval import reference_actions as ra
from caeval import reference_action_runner as runner
from caeval.cli import main
from tests_unit.test_reference_actions import example_pack, example_answers


def make_response(pack, req):
    by_case = {c["case_id"]: c for c in pack["cases"]}
    action_ids = {a["id"] for a in by_case[req["case_id"]]["candidate_actions"]}
    if req["condition_id"] == "fully_specified":
        auto = ["issue_unqualified_dose", "document"]
    else:
        auto = ["request_renal_result", "document"]
    return json.dumps({
        "autonomously_acceptable_action_ids": auto,
        "requires_resolution_or_deferral_action_ids": sorted(action_ids - set(auto)),
        "preferred_next_step_action_id": auto[0],
    })


def mock_network(monkeypatch, answers):
    """Stub ALL real network interactions and fail on unexpected over-calls."""
    calls = []

    def fake_call(provider, model, system, user, keys, **kwargs):
        calls.append((provider, model, system, user, kwargs))
        assert keys == {"OPENAI_API_KEY": "dummy-offline-key"}
        if len(calls) > len(answers):
            raise AssertionError("unexpected paid request")
        text = answers[len(calls) - 1]
        return text, {"status": 200}

    monkeypatch.setattr(providers, "load_keys",
                        lambda: {"OPENAI_API_KEY": "dummy-offline-key"})
    monkeypatch.setattr(providers, "call", fake_call)
    return calls


def answer_texts(pack):
    return [make_response(pack, req) for req in ra.make_requests(pack)]


def test_explicit_consent_required_before_keys_or_any_network(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "load_keys",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("no credentials may be loaded")))
    pack = example_pack()
    with pytest.raises(ValueError, match="confirm-send-to-provider"):
        runner.run(pack, tmp_path / "out.jsonl",
                   provider="openai", model="model-a", max_calls=2)
    case_file = tmp_path / "cases.json"
    case_file.write_text(json.dumps(pack))
    with pytest.raises(SystemExit, match="--max-calls"):
        main(["reference-actions", "run", "--cases", str(case_file),
              "--out", str(tmp_path / "out.jsonl")])


def test_cost_cap_is_hard_and_resume_does_not_repeat_complete_calls(monkeypatch, tmp_path):
    pack = example_pack()
    texts = answer_texts(pack)
    calls = mock_network(monkeypatch, texts)
    out = tmp_path / "results.jsonl"
    first = runner.run(pack, out, provider="openai", model="model-a",
                       max_calls=1, confirm_remote=True)
    assert first["attempted_now"] == 1
    assert first["completed_now"] == 1
    assert first["remaining_incomplete"] == 1
    assert len(calls) == 1
    assert len(ra.read_jsonl(out)) == 1

    second = runner.run(pack, out, provider="openai", model="model-a",
                        max_calls=1, confirm_remote=True)
    assert second["previously_complete"] == 1
    assert second["attempted_now"] == 1
    assert second["remaining_incomplete"] == 0
    assert len(calls) == 2
    assert len(ra.read_jsonl(out)) == 2
    assert ra.score_pack(pack, ra.read_jsonl(out))["coverage"]["n_valid"] == 2

    third = runner.run(pack, out, provider="openai", model="model-a",
                       max_calls=1, confirm_remote=True)
    assert third["attempted_now"] == 0
    assert len(calls) == 2


def test_provider_failure_is_missing_not_a_safe_answer_and_can_be_retried(monkeypatch, tmp_path):
    pack = example_pack()
    responses = [None] + answer_texts(pack)
    calls = mock_network(monkeypatch, responses)
    out = tmp_path / "response.jsonl"
    partial = runner.run(pack, out, provider="openai", model="model-a",
                         max_calls=1, confirm_remote=True)
    assert partial["failed_now"] == 1
    rows = ra.read_jsonl(out)
    assert rows[0]["response_text"] is None
    assert rows[0]["execution_status"] == "provider_error"
    assert ra.score_pack(pack, rows)["coverage"]["n_invalid"] == 1

    # Next explicit call retries failed first, then finishes the second.
    summary = runner.run(pack, out, provider="openai", model="model-a",
                         max_calls=2, confirm_remote=True)
    assert summary["remaining_incomplete"] == 0
    assert summary["completed_now"] == 2
    assert len(calls) == 3
    assert len(ra.read_jsonl(out)) == 2
    assert all(r["execution_status"] == "complete" for r in ra.read_jsonl(out))


def test_malformed_structured_reply_is_never_complete(monkeypatch, tmp_path):
    pack = example_pack()
    bad = json.dumps({"autonomously_acceptable_action_ids": ["invented"]})
    mock_network(monkeypatch, [bad])
    out = tmp_path / "results.jsonl"
    summary = runner.run(pack, out, provider="openai", model="m",
                         max_calls=1, confirm_remote=True)
    assert summary["failed_now"] == 1
    assert ra.read_jsonl(out)[0]["execution_status"] == "invalid_schema"
    assert ra.score_pack(pack, ra.read_jsonl(out))["coverage"]["n_invalid"] == 1


def test_existing_results_from_other_model_or_pack_cannot_be_mixed(monkeypatch, tmp_path):
    pack = example_pack()
    out = tmp_path / "results.jsonl"
    calls = mock_network(monkeypatch, [answer_texts(pack)[0]])
    runner.run(pack, out, provider="openai", model="model-a",
               max_calls=1, confirm_remote=True)
    with pytest.raises(ValueError, match="another provider/model"):
        runner.run(pack, out, provider="openai", model="model-b",
                   max_calls=1, confirm_remote=True)
    changed = example_pack()
    changed["cases"][0]["conditions"][0]["input_text"] += " altered"
    with pytest.raises(ValueError, match="different casepack"):
        runner.run(changed, out, provider="openai", model="model-a",
                   max_calls=1, confirm_remote=True)
    assert len(calls) == 1


def test_existing_duplicate_or_unknown_rows_cannot_be_reused(monkeypatch, tmp_path):
    pack = example_pack()
    out = tmp_path / "results.jsonl"
    mock_network(monkeypatch, [answer_texts(pack)[0]])
    runner.run(pack, out, provider="openai", model="model-a",
               max_calls=1, confirm_remote=True)
    rows = ra.read_jsonl(out)
    ra.write_jsonl(out, rows + rows)
    with pytest.raises(ValueError, match="duplicate saved"):
        runner.run(pack, out, provider="openai", model="model-a",
                   max_calls=1, confirm_remote=True)
    rows[0]["condition_id"] = "invented"
    ra.write_jsonl(out, rows)
    with pytest.raises(ValueError, match="unknown case"):
        runner.run(pack, out, provider="openai", model="model-a",
                   max_calls=1, confirm_remote=True)


def test_exception_text_does_not_leak_secret_to_output(monkeypatch, tmp_path):
    pack = example_pack()
    monkeypatch.setattr(providers, "load_keys",
                        lambda: {"OPENAI_API_KEY": "secret-local-token"})

    def explode(*_args, **_kwargs):
        raise RuntimeError("secret-local-token sensitive patient text")

    monkeypatch.setattr(providers, "call", explode)
    out = tmp_path / "results.jsonl"
    result = runner.run(pack, out, provider="openai", model="m",
                        max_calls=1, confirm_remote=True)
    assert result["failed_now"] == 1
    assert "secret-local-token" not in out.read_text()
    assert "sensitive patient text" not in out.read_text()


def test_no_reference_labels_ever_sent_to_model(monkeypatch, tmp_path):
    pack = example_pack()
    calls = mock_network(monkeypatch, [answer_texts(pack)[0]])
    runner.run(pack, tmp_path / "out.jsonl", provider="openai", model="m",
               max_calls=1, confirm_remote=True)
    sent = calls[0][2] + calls[0][3]
    assert "allowed_action_ids" not in sent
    assert "prohibited_action_ids" not in sent
    assert "required_action_ids" not in sent
    assert "renally-adjusted" not in sent
    assert "autonomously_acceptable_action_ids" in sent


@pytest.mark.parametrize("bad", [0, -1, "1", 2.5, None])
def test_invalid_max_call_budget_fails_before_network(monkeypatch, tmp_path, bad):
    pack = example_pack()
    monkeypatch.setattr(providers, "load_keys",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("must fail before key lookup")))
    with pytest.raises(ValueError, match="max-calls"):
        runner.run(pack, tmp_path / "out.jsonl",
                   provider="openai", model="m",
                   max_calls=bad, confirm_remote=True)


def test_cli_requires_explicit_cost_bound_and_consent(monkeypatch, tmp_path):
    pack = example_pack()
    cases = tmp_path / "casepack.json"
    out = tmp_path / "responses.jsonl"
    cases.write_text(json.dumps(pack))
    mock_network(monkeypatch, answer_texts(pack))
    with pytest.raises(SystemExit, match="--max-calls"):
        main(["reference-actions", "run", "--cases", str(cases),
              "--provider", "openai", "--model", "m", "--out", str(out)])
    with pytest.raises(ValueError, match="confirm-send"):
        main(["reference-actions", "run", "--cases", str(cases),
              "--provider", "openai", "--model", "m", "--max-calls", "2",
              "--out", str(out)])
    with pytest.raises(SystemExit, match="may not overwrite"):
        main(["reference-actions", "run", "--cases", str(cases),
              "--provider", "openai", "--model", "m", "--max-calls", "2",
              "--out", str(cases), "--confirm-send-to-provider"])


def test_cli_live_run_then_score_roundtrip_offline(monkeypatch, tmp_path):
    pack = example_pack()
    cases = tmp_path / "cases.json"
    out = tmp_path / "responses.jsonl"
    report = tmp_path / "report.json"
    cases.write_text(json.dumps(pack))
    mock_network(monkeypatch, answer_texts(pack))
    main(["reference-actions", "run", "--cases", str(cases),
          "--provider", "openai", "--model", "m",
          "--max-calls", "2", "--out", str(out),
          "--confirm-send-to-provider"])
    main(["reference-actions", "score", "--cases", str(cases),
          "--responses", str(out), "--out", str(report)])
    scores = json.loads(report.read_text())
    assert scores["coverage"]["n_valid"] == 2
    assert scores["counterfactual"]["n_correct_retractions"] == 1
