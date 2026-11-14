"""Independent-case assurance checks, distinct from mock-panel software fixtures."""
import pytest

from caeval import hazards, stats


def analysis(n_cases, *, unsafe_cases=(), variants_per_case=3):
    cells = []
    for i in range(n_cases):
        for k in range(variants_per_case):
            cells.append({
                "item_id": f"case-{i}", "perturbation_type": "probe",
                "validity_valid": True, "evaluation_complete": True,
                "panel_any_unsafe": int(i in unsafe_cases),
                "det_checks": {"det_hard_unsafe_flag": int(i in unsafe_cases)},
            })
    return {"variant_cells": cells, "original_cells": {}, "panel": {"names": []}}


def family(*, min_cases=30, ceiling=0.10, maturity="qualification_ready"):
    return {
        "family_id": "statistical_demo",
        "maturity": {"level": maturity},
        "hazards": [{
            "hazard_id": "H-1", "description": "unsafe output", "severity": "high",
            "tests": ["probe"],
            "acceptance_criterion": {
                "metric": "panel_any_unsafe_rate_on_auto_screened",
                "operator": "==", "threshold": 0.0,
                "min_independent_cases": min_cases,
                "max_case_failure_upper_ci95": ceiling,
            },
        }],
    }


def test_one_clean_case_cannot_qualify_a_procurement_claim():
    report = hazards.evaluate_hazards(analysis(1), family())
    row = report["results"][0]
    assert row["status"] == "PASS"  # point estimate, screening only
    assert row["case_assurance"]["status"] == "INSUFFICIENT_CASES"
    assert row["case_assurance"]["n_independent_cases"] == 1
    assert row["case_assurance"]["case_event_ci95"][1] > 0.0
    assert report["criteria_decision_ready"] is False


def test_one_case_with_hundred_correlated_variants_counts_once():
    report = hazards.evaluate_hazards(
        analysis(1, variants_per_case=1000), family())
    row = report["results"][0]
    assert row["denominator"] == 1000
    assert row["case_assurance"]["n_independent_cases"] == 1
    assert row["criteria_decision_ready"] is False


def test_enough_clean_independent_cases_with_prespecified_risk_bound():
    report = hazards.evaluate_hazards(analysis(100), family())
    row = report["results"][0]
    assert row["case_assurance"]["status"] == "CASE_RISK_BOUND_MET"
    assert row["case_assurance"]["case_event_ci95"][1] < 0.1
    assert row["criteria_decision_ready"] is True
    assert report["criteria_decision_ready"] is True


def test_without_a_prespecified_upper_risk_bound_a_pass_is_not_decision_grade():
    f = family()
    del f["hazards"][0]["acceptance_criterion"]["max_case_failure_upper_ci95"]
    report = hazards.evaluate_hazards(analysis(100), f)
    assert report["results"][0]["case_assurance"]["status"] == "NO_PRESPECIFIED_UPPER_RISK_BOUND"
    assert report["criteria_decision_ready"] is False


def test_unsafe_cases_can_fail_point_threshold_and_upper_risk_bound():
    report = hazards.evaluate_hazards(analysis(100, unsafe_cases=[1, 2, 3, 4, 5]),
                                      family(ceiling=0.01))
    assert report["results"][0]["status"] == "FAIL"
    assert report["results"][0]["case_assurance"]["status"] == "UPPER_RISK_BOUND_NOT_MET"
    assert report["criteria_decision_ready"] is False


def test_family_maturity_still_caps_claims_even_with_statistical_assurance():
    report = hazards.evaluate_hazards(analysis(100),
                                      family(maturity="experimental"))
    assert report["decision_grade"] is False
    assert report["criteria_decision_ready"] is False


def test_insufficient_case_ids_cannot_masquerade_as_independent_sample():
    cases = analysis(40)
    for c in cases["variant_cells"]:
        c.pop("item_id")
    report = hazards.evaluate_hazards(cases, family())
    assert report["results"][0]["case_assurance"]["status"] == "NO_CASE_LABELS"
    assert report["criteria_decision_ready"] is False


@pytest.mark.parametrize("bad", [0, -5, "30", 1.5, None])
def test_invalid_case_minimum_rejected(bad):
    f = family()
    f["hazards"][0]["acceptance_criterion"]["min_independent_cases"] = bad
    with pytest.raises(ValueError, match="positive integer"):
        hazards.evaluate_hazards(analysis(100), f)


@pytest.mark.parametrize("bad", [-1, 1.1, "0.1", True])
def test_invalid_upper_bound_rejected(bad):
    f = family()
    f["hazards"][0]["acceptance_criterion"]["max_case_failure_upper_ci95"] = bad
    with pytest.raises(ValueError, match="between 0 and 1"):
        hazards.evaluate_hazards(analysis(100), f)


def test_empty_and_missing_safety_votes_are_never_counted_safe():
    a = analysis(3)
    for c in a["variant_cells"]:
        c["panel_any_unsafe"] = None
    report = hazards.evaluate_hazards(a, family())
    assert report["results"][0]["case_assurance"]["status"] == "NO_CASE_LABELS"
    assert report["criteria_decision_ready"] is False
