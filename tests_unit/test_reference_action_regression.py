"""Exploratory action-level version regression must fail closed under missingness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from caeval import reference_action_regression as reg
from caeval import reference_actions as ra


PACK = json.loads((Path(__file__).resolve().parents[1]
                   / "casepacks/action_reference/public_dev/demo.json").read_text())


def _rows(*, missing_bad=False, label="baseline"):
    hash_ = ra.validate_pack(PACK)["pack_sha256"]
    rows = []
    for case in PACK["cases"]:
        menu = {a["id"] for a in case["candidate_actions"]}
        for cond in case["conditions"]:
            is_missing = cond["condition_id"] == "protocol_missing_renal"
            auto = {"unqualified_dose", "document_uncertainty"} if not is_missing else (
                {"unqualified_dose", "document_uncertainty"} if missing_bad
                else {"request_renal_result", "document_uncertainty"})
            rows.append({
                "case_id": case["case_id"], "condition_id": cond["condition_id"],
                "repeat": 1, "pack_sha256": hash_,
                "provider": "openai", "model": label,
                "response_text": {
                    "autonomously_acceptable_action_ids": sorted(auto),
                    "requires_resolution_or_deferral_action_ids": sorted(menu - auto),
                    "preferred_next_step_action_id": next(iter(sorted(auto))),
                },
            })
    return rows


def test_newly_prohibited_action_and_lost_retraction_are_reported():
    base = _rows()
    cand = _rows(missing_bad=True, label="candidate")
    result = reg.compare(PACK, base, cand)
    assert result["status"] == "COMPLETE_EXPLORATORY_COMPARISON"
    assert result["counts"]["newly_prohibited_autonomous_actions"] == 1
    assert result["counts"]["retraction_regressions"] == 1
    assert result["counts"]["newly_missed_required_actions"] == 1
    assert result["counts"]["regained_required_actions"] == 0
    assert result["case_level_new_prohibited_action_ci95"]["n_independent_cases"] == 1
    assert "EXPLORATORY" in reg.render_markdown(result)


def test_missing_candidate_is_not_assumed_safe_or_improved():
    result = reg.compare(PACK, _rows(), _rows(missing_bad=True)[0:1])
    assert result["status"] == "INCOMPLETE_NO_RELIABLE_OVERALL_COMPARISON"
    assert result["coverage"]["n_unpaired_slots"] == 1
    assert result["coverage"]["n_paired_complete_slots"] == 1
    assert result["case_level_new_prohibited_action_ci95"]["n_independent_cases"] == 0


def test_invalid_or_duplicate_rows_do_not_silently_pass():
    base = _rows()
    bad = _rows(missing_bad=True)
    bad[1]["response_text"] = "not-json"
    result = reg.compare(PACK, base, bad)
    assert result["status"].startswith("INCOMPLETE")
    assert result["coverage"]["candidate_invalid"]
    with pytest.raises(ValueError, match="duplicate response slot"):
        reg.compare(PACK, base, _rows() + _rows())


def test_mismatched_pack_hash_is_refused():
    candidate = _rows()
    candidate[0]["pack_sha256"] = "not_the_pack"
    with pytest.raises(ValueError, match="pack SHA-256 mismatch"):
        reg.compare(PACK, _rows(), candidate)


def test_cli_emits_machine_readable_report_and_nonzero_for_incomplete(tmp_path):
    from caeval.cli import main
    pack = tmp_path / "pack.json"
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    report = tmp_path / "compare.json"
    pack.write_text(json.dumps(PACK))
    ra.write_jsonl(before, _rows())
    ra.write_jsonl(after, _rows(missing_bad=True, label="candidate"))
    main(["reference-actions", "compare", "--cases", str(pack),
          "--baseline", str(before), "--candidate", str(after),
          "--out", str(report)])
    out = json.loads(report.read_text())
    assert out["counts"]["retraction_regressions"] == 1
    ra.write_jsonl(after, _rows()[:1])
    with pytest.raises(SystemExit, match="INCOMPLETE"):
        main(["reference-actions", "compare", "--cases", str(pack),
              "--baseline", str(before), "--candidate", str(after),
              "--out", str(report)])
