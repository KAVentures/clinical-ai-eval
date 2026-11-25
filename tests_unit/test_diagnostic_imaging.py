"""Synthetic-only checks of the offline diagnostic imaging research lane."""
import copy
import hashlib
import json
import pytest
from caeval import diagnostic_imaging as d


def spec():
    return {
        "study_id": "synthetic-study", "task_id": "fictional-binary-target", "modality": "synthetic",
        "reference_method": "fictional-consensus",
        "attestations": dict(authorized_local_research=True, deidentified_or_synthetic=True,
                             independent_reference=True, pre_exposure=True),
        "candidates": [{"id": "a", "version": "v1", "threshold_bp": 5000},
                       {"id": "b", "version": "v1", "threshold_bp": 6000}],
        "strata": ["group-x", "empty"],
        "cases": [{"case_id": "c" + str(i), "cluster_id": "cluster" + str(i // 2),
                   "asset_sha256": hashlib.sha256(str(i).encode()).hexdigest(),
                   "reference": ref, "strata": ["group-x"] if i < 2 else []}
                  for i, ref in enumerate(["positive", "positive", "negative", "negative", "unresolved"])]
    }


def study():
    return d.freeze(spec(), private_nonce="1" * 64, confirmed=True)


def submission(plan, cid, values):
    response = d.template(plan, cid)
    cases = {slot: int(case["case_id"][1:]) for slot, case in d._slots(plan).items()}
    for row in response["predictions"]:
        value = values[cases[row["slot"]]]
        if isinstance(value, int):
            row.update(status="scored", score_bp=value)
        elif value == "abstained":
            row.update(status="abstained")
    return response


def test_prospective_confirmation_and_private_exchange():
    with pytest.raises(ValueError):
        d.freeze(spec())
    plan = study()
    ex = d.exchange(plan, "a")
    assert ex["plan_digest"] == plan["digest"]
    assert set(ex) == {"schema", "plan_digest", "candidate", "task_id", "modality", "cases"}
    assert all(set(c) == {"slot", "asset_sha256"} for c in ex["cases"])
    other = d.freeze(spec(), private_nonce="2" * 64, confirmed=True)
    assert not ({c["slot"] for c in ex["cases"]} &
                {c["slot"] for c in d.exchange(other, "a")["cases"]})
    assert "private_nonce" not in json.dumps(ex)


def test_normalized_plan_and_rejected_inputs():
    s = spec()
    s["cases"].reverse()
    s["candidates"].reverse()
    s["strata"].reverse()
    assert d.freeze(s, private_nonce="1" * 64, confirmed=True) == study()
    for mutate in [
        lambda s: s["attestations"].update(pre_exposure=False),
        lambda s: s["cases"][0].update(strata=["undeclared"]),
        lambda s: s["cases"][1].update(asset_sha256=s["cases"][0]["asset_sha256"]),
        lambda s: s["cases"][1].update(case_id=s["cases"][0]["case_id"]),
        lambda s: s["candidates"][0].update(threshold_bp=True),
        lambda s: s["cases"][0].update(patient_name="forbidden"),
    ]:
        bad = spec()
        mutate(bad)
        with pytest.raises(ValueError):
            d.freeze(bad, confirmed=True)


def test_missingness_bounds_and_auc_and_paired_comparison():
    p = study()
    a = submission(p, "a", [9000, "abstained", 2000, 5000, 7000])
    b = submission(p, "b", [5000, 6000, 2000, None, 9000])
    report = d.assess(p, [b, a])
    assert report["roster_complete"] and not report["decision_grade"]
    am = report["candidates"][0]["metrics"]["overall"]
    assert am["counts"] == dict(tp=1, tn=1, fp=1, fn=0, positive_missing=1, negative_missing=0,
                                unresolved_reference=1, abstained=1, unavailable=0, scored=4)
    assert am["sensitivity_scored_only"] == 1
    assert am["sensitivity_resolved_reference_bounds"] == [0.5, 1]
    assert am["specificity_resolved_reference_bounds"] == [0.5, 0.5]
    assert am["auc_scored_resolved_only"] == 1
    assert am["score_coverage"] == 0.8
    assert am["n_clusters"] == 3
    bm = report["candidates"][1]["metrics"]["overall"]
    assert bm["specificity_resolved_reference_bounds"] == [0.5, 1]
    paired = report["comparisons"][0]["metrics"]["overall"]
    assert paired["n_resolved"] == 4 and paired["n_paired"] == 2
    assert paired["paired_accuracy_difference_a_minus_b"] == 0.5
    assert paired["all_resolved_accuracy_difference_bounds"] == [-0.25, 0.25]
    empty = report["candidates"][0]["metrics"]["stratum:empty"]
    assert empty["score_coverage"] is None and empty["auc_scored_resolved_only"] is None
    assert report["candidates"][0]["metrics"]["stratum:group-x"]["n_clusters"] == 1


def test_exact_ties_and_all_missing_reference_denominators():
    p = study()
    a = submission(p, "a", [5000, 5000, 5000, 5000, None])
    b = d.template(p, "b")
    report = d.assess(p, [a, b])
    assert report["candidates"][0]["metrics"]["overall"]["auc_scored_resolved_only"] == 0.5
    bm = report["candidates"][1]["metrics"]["overall"]
    assert bm["sensitivity_resolved_reference_bounds"] == [0, 1]
    assert bm["specificity_resolved_reference_bounds"] == [0, 1]
    assert bm["sensitivity_scored_only"] is None
    assert bm["auc_scored_resolved_only"] is None


def test_incomplete_roster_suppresses_all_comparisons():
    p = study()
    report = d.assess(p, [d.template(p, "a")])
    assert not report["roster_complete"]
    assert report["candidates"][1]["status"] == "not_submitted"
    assert report["comparisons"] == []
    assert d.assess(p, [])["comparisons"] == []


def test_prediction_inventory_and_version_fail_closed():
    p = study()
    for change in [
        lambda r: r["candidate"].update(version="v2"),
        lambda r: r["candidate"].update(threshold_bp=5001),
        lambda r: r["predictions"].pop(),
        lambda r: r["predictions"][0].update(slot=r["predictions"][1]["slot"]),
        lambda r: r["predictions"][0].update(status="scored", score_bp=True),
        lambda r: r["predictions"][0].update(status="abstained", score_bp=1),
        lambda r: r.update(plan_digest="0" * 64),
    ]:
        bad = d.template(p, "a")
        change(bad)
        with pytest.raises(ValueError):
            d.assess(p, [bad])
    a = d.template(p, "a")
    with pytest.raises(ValueError):
        d.assess(p, [a, a])


def test_offline_replay_tampering_and_external_digest():
    p = study()
    a, b = d.template(p, "a"), d.template(p, "b")
    evidence = d.package(p, [b, a])
    assert evidence == d.package(p, [a, b])
    assert d.verify(evidence, evidence["digest"])["expected_digest_matched"]
    assert not d.verify(evidence)["expected_digest_matched"]
    with pytest.raises(ValueError):
        d.verify(evidence, "0" * 64)
    for path in ("plan", "report", "submissions"):
        bad = copy.deepcopy(evidence)
        if path == "plan":
            bad[path]["spec"]["cases"][0]["reference"] = "negative"
        elif path == "report":
            bad[path]["roster_complete"] = False
        else:
            bad[path][0]["predictions"][0].update(status="scored", score_bp=1234)
        bad["digest"] = d._digest({k: v for k, v in bad.items() if k != "digest"})
        with pytest.raises(ValueError):
            d.verify(bad)


def test_offline_cli_round_trip(tmp_path, capsys):
    def write(name, data):
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)
    def invoke(*args):
        import argparse
        parser = argparse.ArgumentParser()
        d.configure(parser)
        d.cli(parser.parse_args(args))
        return json.loads(capsys.readouterr().out)
    plan = invoke("freeze", write("spec.json", spec()), "--confirm-prospective")
    plan_file = write("private.json", plan)
    assert invoke("exchange", plan_file, "a") == d.exchange(plan, "a")
    pred = invoke("template", plan_file, "a")
    evidence = invoke("assess", plan_file, write("pred.json", pred))
    assert invoke("verify", write("evidence.json", evidence), "--expected-digest",
                  evidence["digest"])["status"] == "REPLAY_VERIFIED"
    bad = tmp_path / "bad.json"
    bad.write_text('{"a": 1, "a": 2}')
    with pytest.raises(ValueError):
        d._read(bad)
