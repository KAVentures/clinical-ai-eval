"""Regression tests for evidence/conformance consistency across execution paths.

These are software integrity guards, not clinical evidence. In particular, a
synthetic judge may help test the pipeline but cannot establish an L1 result.
"""
from __future__ import annotations

import pytest

from caeval import lifecycle, pipeline
from caeval.cli import _score_patient_cells
from caeval.patient import judging
from caeval.util import read_toml, repo_root


def judge(name, provider, *, mock=False, mode="blinded"):
    return {"name": name, "provider": provider, "model": name,
            "mode": mode, "mock": mock}


def panel(*judges, required=1):
    return {"min_distinct_providers": required, "judges": list(judges)}


def scored_cell(*judges):
    return {"item_id": "sample", "panel_scored": True,
            "panel_labels": {j["name"]: {"under_triage": 0} for j in judges}}


@pytest.mark.parametrize("configured", [
    panel(judge("mock", "mock_a", mock=True)),
    panel(judge("real", "provider_a"),
          judge("mock", "mock_b", mock=True), required=2),
    panel(judge("mock", "mock_a", mock=True),
          judge("real_cued", "provider_b", mode="rubric_aware")),
])
def test_mock_blinded_votes_never_upgrade_evidence(configured):
    assert pipeline.assess_panel(configured)["conformance_level"] == "L0"
    assert lifecycle.conformance_from(configured, [
        scored_cell(*configured["judges"])], l2_gate_passed=True) == "L0"


def test_one_real_blinded_judge_is_valid_automated_screen_not_clinical_validation():
    one = judge("real_a", "provider_a")
    configured = panel(one, required=1)
    assert pipeline.assess_panel(configured)["conformance_level"] == "L1"
    assert lifecycle.conformance_from(configured, [scored_cell(one)]) == "L1"
    assert lifecycle.conformance_from(
        configured, [scored_cell(one)], l2_gate_passed=True) == "L2"


def test_cued_mock_does_not_invalidate_a_real_blinded_headline():
    real = judge("real", "provider_a")
    cued_mock = judge("mock_cued", "mock", mock=True, mode="rubric_aware")
    configured = panel(real, cued_mock)
    assert pipeline.assess_panel(configured)["conformance_level"] == "L1"
    assert lifecycle.conformance_from(configured, [scored_cell(real)]) == "L1"


def test_required_quorum_is_preserved_and_only_observed_votes_count():
    a, b = judge("a", "provider_a"), judge("b", "provider_b")
    configured = panel(a, b, required=2)
    assert pipeline.assess_panel(configured)["conformance_level"] == "L1"
    assert lifecycle.conformance_from(configured, [scored_cell(a, b)]) == "L1"
    assert lifecycle.conformance_from(configured, [scored_cell(a)]) == "L0"
    # A caller cannot claim evidence merely by setting a boolean.
    assert lifecycle.conformance_from(configured, [
        {"item_id": "sample", "panel_scored": True}]) == "L0"


def test_no_successful_judge_label_is_not_an_l1_screen():
    real = judge("real", "provider_a")
    assert lifecycle.conformance_from(
        panel(real), [{"item_id": "sample", "panel_scored": False,
                       "panel_labels": {"real": {"under_triage": 0}}}]) == "L0"


def test_invalid_panel_quorums_rejected():
    real = judge("real", "provider_a")
    with pytest.raises(ValueError, match=">= 1"):
        pipeline.assess_panel(panel(real, required=0))
    with pytest.raises(ValueError, match="requires >= 2"):
        pipeline.assess_panel(panel(real, required=2))


def test_patient_executor_respects_configured_distinct_provider_quorum(monkeypatch):
    monkeypatch.setattr(judging, "score_episode",
                        lambda _judge, _payload: ({"under_triage": 0}, {}))
    a = judge("real", "provider_a")
    inputs = [{"item_id": "episode", "judge_payload": {"conversation": []}}]
    one = _score_patient_cells(inputs, panel(a, required=1))
    assert one[0]["panel_scored"] is True
    assert lifecycle.conformance_from(panel(a), one) == "L1"
    two_required = _score_patient_cells(inputs, panel(a, required=2))
    assert two_required[0]["panel_scored"] is False


def test_strict_panel_config_parsing(tmp_path):
    defaults = read_toml(repo_root() / "configs" / "judge_panel.toml")
    assert defaults["min_distinct_providers"] == 1
    assert len(defaults["judges"]) == 4

    bad = tmp_path / "bad.toml"
    bad.write_text("min_distinct_providers = 1\nnot actually a valid TOML line\n")
    with pytest.raises(ValueError, match="line 2"):
        read_toml(bad)

    bad.write_text("min_distinct_providers = 1\nmin_distinct_providers = 2\n")
    with pytest.raises(ValueError, match="Duplicate TOML key"):
        read_toml(bad)


def test_unobserved_configured_panel_is_l0_even_with_real_judge_names():
    a = judge("a", "provider_a")
    configured = panel(a)
    info = pipeline.assess_panel(configured)
    assert info["conformance_level"] == "L1"  # configuration only
    assert pipeline.observed_panel_conformance(info, [])["conformance_level"] == "L0"
    assert pipeline.observed_panel_conformance(info, [
        {"kind": "variant", "evaluation_complete": False,
         "judge_scores": {"a": {"unsafe_overconfident": 0}}}
    ])["conformance_level"] == "L0"
    assert pipeline.observed_panel_conformance(info, [
        {"kind": "original", "evaluation_complete": True,
         "judge_scores": {"a": {"unsafe_overconfident": 0}}}
    ])["conformance_level"] == "L0"
    assert pipeline.observed_panel_conformance(info, [
        {"kind": "variant", "evaluation_complete": True,
         "judge_scores": {"a": {"unsafe_overconfident": 0}}}
    ])["conformance_level"] == "L1"


def test_no_cued_or_unknown_judge_may_fabricate_observed_l1():
    configured = panel(judge("real", "provider_a"),
                       judge("mock_cued", "mock_a",
                             mock=True, mode="rubric_aware"))
    info = pipeline.assess_panel(configured)
    assert pipeline.observed_panel_conformance(info, [
        {"kind": "variant", "evaluation_complete": True,
         "judge_scores": {"mock_cued": {"unsafe_overconfident": 0}}}
    ])["conformance_level"] == "L0"
