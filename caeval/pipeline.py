"""Pipeline orchestration (EVAL_STANDARD.md §10), split into separable stages so
judging a different panel over frozen subject responses does not re-generate them:

    generate_responses  -> subject responses + validity audit + deterministic checks
    score_responses     -> panel scoring (>=2 distinct providers), paired vs original
    analyze             -> separated dimensions, disagreement, CIs, agreement, paired tests
    run                 -> generate + score + analyze (convenience)

No headline number is ever a single collapsed score (§0).
"""
from __future__ import annotations

import yaml

from . import providers, score as score_mod, stats
from .checks import deterministic_checks
from .disagreement import build_disagreement_rows, summarize_disagreement
from .harm_ontology import classify_failure
from .perturbations import apply_transform, manifest_row
from .util import repo_root
from .validity import audit_variant, stratum_counts

PRIMARY = "unsafe_overconfident"


# --------------------------------------------------------------------------
def load_family(family_id: str = "missing_information") -> dict:
    with open(repo_root() / "tests" / family_id / "family.yaml") as f:
        return yaml.safe_load(f)


def load_panel(config_path=None) -> dict:
    from .util import read_toml
    cfg = read_toml(config_path or (repo_root() / "configs" / "judge_panel.toml"))
    return {"min_distinct_providers": int(cfg.get("min_distinct_providers", 2)),
            "judges": cfg.get("judges", [])}


def assess_panel(panel: dict) -> dict:
    judges = panel["judges"]
    distinct = sorted({j.get("provider") for j in judges})
    all_mock = all(j.get("mock") for j in judges) if judges else True
    min_needed = panel.get("min_distinct_providers", 2)
    if len(distinct) < min_needed:
        raise ValueError(
            f"judge panel has {len(distinct)} distinct provider(s) {distinct}; "
            f"EVAL_STANDARD.md requires >= {min_needed} DIFFERENT providers (§7, §11).")
    if all_mock:
        level, note = "L0", (
            "Panel is synthetic (mock). Structurally exercises the L1 machinery (>=2 distinct "
            "providers, disagreement, validity gate, safety/helpfulness separated) but a mock "
            "judge CANNOT support a conclusion -> NON_CONFORMANT for any claim (§0). Swap in "
            ">=2 real different-provider judges for L1.")
    else:
        level, note = "L1", (
            "Automated screen: >=2 different-provider LLM judges. Conclusions must be worded as "
            "'automated screen suggests', never as findings, until a human review queue is "
            "completed and adjudicated (L2).")
    return {"distinct_providers": distinct, "all_mock": all_mock,
            "conformance_level": level, "note": note}


# --------------------------------------------------------------------------
def build_manifest(family: dict, cases: list[dict]) -> tuple[list[dict], dict]:
    originals = {c["item_id"]: c["input_text"] for c in cases}
    variant_rows: list[dict] = []
    for test in family.get("tests", []):
        test_id = test["test_id"]
        transform = test["transform"]
        fam_expected = test.get("expected_missing_evidence", "")
        severity = test.get("severity", "moderate")
        precondition = test.get("precondition")
        for case in cases:
            if precondition and not _precondition_met(precondition, case):
                continue
            result = apply_transform(transform, case["input_text"], str(case.get("ground_truth_label", "")))
            row = manifest_row(case, test_id, result)
            if fam_expected:
                row["expected_missing_evidence"] = fam_expected
            row["test_id"] = test_id
            row["transform"] = transform
            row["severity"] = severity
            variant_rows.append(row)
    return variant_rows, originals


def _precondition_met(precondition: str, case: dict) -> bool:
    text = (case["input_text"] + " " + str(case.get("ground_truth_label", ""))).lower()
    if "renal" in precondition.lower():
        return any(s in text for s in ("renal dose adjustment", "requires renal", "renally-adjusted", "renal adjustment"))
    if "contradiction" in precondition.lower() or "conflict" in precondition.lower():
        return True
    return True


# --------------------------------------------------------------------------
# STAGE 1: generate subject responses (+ validity audit + deterministic checks)
# --------------------------------------------------------------------------
def generate_responses(subject_fn, family: dict, cases: list[dict]) -> list[dict]:
    variant_rows, originals = build_manifest(family, cases)
    validity = {r["perturbation_id"]: audit_variant(r, originals.get(r["item_id"], ""), family)
                for r in variant_rows}

    rows: list[dict] = []
    # originals first (paired baseline)
    for case in cases:
        rec = {**case, "cell_id": f"{case['item_id']}::original",
               "perturbation_type": "original", "expected_missing_evidence": ""}
        resp = subject_fn(rec)
        rows.append({
            "cell_id": rec["cell_id"], "item_id": case["item_id"], "kind": "original",
            "perturbation_type": "original", "severity": "n/a",
            "input_text": case["input_text"], "expected_missing_evidence": "",
            "response_text": resp, "paired_original_cell_id": rec["cell_id"],
            "det_checks": deterministic_checks(resp, "original", ""),
        })
    # variants
    for r in variant_rows:
        vl = validity[r["perturbation_id"]]
        cell_id = f"{r['item_id']}::{r['test_id']}::{r['perturbation_id'][:8]}"
        rec = {"item_id": r["item_id"], "input_text": r["input_text"], "cell_id": cell_id,
               "perturbation_type": r["test_id"], "expected_missing_evidence": r.get("expected_missing_evidence", ""),
               "ground_truth_label": r.get("ground_truth_label", "")}
        resp = subject_fn(rec)
        rows.append({
            "cell_id": cell_id, "item_id": r["item_id"], "kind": "variant",
            "perturbation_type": r["test_id"], "transform": r["transform"], "severity": r["severity"],
            "input_text": r["input_text"], "expected_missing_evidence": r.get("expected_missing_evidence", ""),
            "response_text": resp, "perturbation_id": r["perturbation_id"],
            "paired_original_cell_id": f"{r['item_id']}::original",
            "validity_valid": vl.valid, "validity_ambiguous": vl.ambiguous,
            "determinacy": vl.determinacy, "trunc_form": vl.trunc_form, "task_type": vl.task_type,
            "validity_reasons": vl.reasons,
            "det_checks": deterministic_checks(resp, r["test_id"], r.get("expected_missing_evidence", "")),
        })
    return rows


# --------------------------------------------------------------------------
# STAGE 2: score frozen responses with the judge panel
# --------------------------------------------------------------------------
def score_responses(response_rows: list[dict], panel: dict, keys: dict | None = None) -> list[dict]:
    judges = panel["judges"]
    originals_scored: dict[str, dict] = {}
    scored: list[dict] = []

    # score originals first so variants can pair against them
    for row in [r for r in response_rows if r["kind"] == "original"]:
        cell = _score_cell(row, judges, keys)
        originals_scored[row["cell_id"]] = cell
        scored.append(cell)
    for row in [r for r in response_rows if r["kind"] == "variant"]:
        cell = _score_cell(row, judges, keys)
        ob = originals_scored.get(row["paired_original_cell_id"])
        cell["orig_panel_any_unsafe"] = ob["panel_any_unsafe"] if ob else None
        cell["flip_safe_to_unsafe"] = int(bool(ob) and ob["panel_any_unsafe"] == 0 and cell["panel_any_unsafe"] == 1)
        cell["flip_unsafe_to_safe"] = int(bool(ob) and ob["panel_any_unsafe"] == 1 and cell["panel_any_unsafe"] == 0)
        judge_names = [j["name"] for j in judges]
        cell["failure_modes"] = classify_failure(_panel_majority_score(cell, judge_names), row["perturbation_type"]) if cell["panel_any_unsafe"] else []
        scored.append(cell)
    return scored


def _score_cell(row: dict, judges: list[dict], keys) -> dict:
    record = {"item_id": row["item_id"], "input_text": row["input_text"],
              "perturbation_type": row["perturbation_type"],
              "expected_missing_evidence": row["expected_missing_evidence"],
              "response_text": row["response_text"]}
    judge_scores, judge_errors = {}, {}
    for j in judges:
        sc, meta = providers.score_response(j, record, keys)
        if sc is None:
            judge_errors[j["name"]] = str(meta)[:200]
        else:
            judge_scores[j["name"]] = sc
    labels = [s[PRIMARY] for s in judge_scores.values()]
    n, n_unsafe = len(labels), sum(1 for v in labels if v == 1)
    return {
        "cell_id": row["cell_id"], "item_id": row["item_id"], "kind": row["kind"],
        "perturbation_type": row["perturbation_type"], "severity": row["severity"],
        "input_text": row["input_text"], "expected_missing_evidence": row["expected_missing_evidence"],
        "response_text": row["response_text"], "perturbation_id": row.get("perturbation_id"),
        "transform": row.get("transform"), "det_checks": row.get("det_checks", {}),
        "validity_valid": row.get("validity_valid"), "validity_ambiguous": row.get("validity_ambiguous"),
        "determinacy": row.get("determinacy"), "trunc_form": row.get("trunc_form"), "task_type": row.get("task_type"),
        "judge_scores": judge_scores, "judge_errors": judge_errors,
        "panel_any_unsafe": int(n_unsafe > 0),
        "panel_majority_unsafe": int(n and n_unsafe * 2 > n),
        "disagreement": int(0 < n_unsafe < n),
        "potentially_harmful_treatment": int(any(s.get("potentially_harmful_treatment") == 1 for s in judge_scores.values())),
    }


def _panel_majority_score(cell: dict, judge_names: list[str]) -> dict:
    scores = list(cell["judge_scores"].values())
    out = {f: (int(sum(s.get(f, 0) for s in scores) * 2 > len(scores)) if scores else 0)
           for f in score_mod.BINARY_FIELDS}
    out["quote_support"] = scores[0].get("quote_support", "") if scores else ""
    return out


# --------------------------------------------------------------------------
# STAGE 3: analyze (separated dimensions, disagreement, CIs, agreement, paired)
# --------------------------------------------------------------------------
def analyze(scored: list[dict], response_rows: list[dict], family: dict,
            subject_spec: dict, panel: dict) -> dict:
    panel_info = assess_panel(panel)
    judge_names = [j["name"] for j in panel["judges"]]
    originals = {c["cell_id"]: c for c in scored if c["kind"] == "original"}
    variants = [c for c in scored if c["kind"] == "variant"]
    validated = [c for c in variants if c.get("validity_valid")]
    excluded = [c for c in variants if not c.get("validity_valid")]

    # validity strata (§5) from the response rows' labels
    v_labels = [type("VL", (), {"trunc_form": r["trunc_form"], "determinacy": r["determinacy"],
                                "task_type": r["task_type"], "valid": r["validity_valid"],
                                "ambiguous": r["validity_ambiguous"]})()
                for r in response_rows if r["kind"] == "variant"]
    validity_strata = stratum_counts(v_labels)

    disagreement_rows = build_disagreement_rows([{**c, "arm": subject_spec.get("arm", "")} for c in variants], judge_names, PRIMARY)
    disagreement_summary = summarize_disagreement(disagreement_rows, judge_names, PRIMARY)
    dis_validated = summarize_disagreement(
        build_disagreement_rows([{**c, "arm": subject_spec.get("arm", "")} for c in validated], judge_names, PRIMARY),
        judge_names, PRIMARY)

    dimensions = _dimension_report(validated, originals, judge_names, family)
    agreement = _panel_agreement(validated, judge_names)
    det_summary = _det_summary(validated)

    return {
        "subject_spec": subject_spec, "family_id": family.get("family_id"),
        "panel": {"judges": panel["judges"], "names": judge_names, **panel_info,
                  "min_distinct_providers": panel.get("min_distinct_providers", 2)},
        "n_cases": len({c["item_id"] for c in scored}),
        "n_variants_generated": len(variants),
        "n_variants_validated": len(validated),
        "n_variants_excluded": len(excluded),
        "validity": {r["perturbation_id"]: {
            "perturbation_type": r["perturbation_type"], "valid": r["validity_valid"],
            "ambiguous": r["validity_ambiguous"], "determinacy": r["determinacy"],
            "trunc_form": r["trunc_form"], "task_type": r["task_type"], "reasons": r["validity_reasons"]}
            for r in response_rows if r["kind"] == "variant"},
        "validity_strata": validity_strata,
        "original_cells": originals,
        "variant_cells": variants,
        "validated_cell_ids": [c["cell_id"] for c in validated],
        "disagreement_rows": disagreement_rows,
        "disagreement_summary_all_variants": disagreement_summary,
        "disagreement_summary_validated": dis_validated,
        "dimensions": dimensions,
        "panel_agreement": agreement,
        "deterministic_checks_summary": det_summary,
    }


def run(subject_fn, subject_spec: dict, family: dict, cases: list[dict], panel: dict,
        keys: dict | None = None) -> dict:
    assess_panel(panel)  # enforce >=2 distinct providers before doing any work
    responses = generate_responses(subject_fn, family, cases)
    scored = score_responses(responses, panel, keys)
    result = analyze(scored, responses, family, subject_spec, panel)
    result["_response_rows"] = responses
    return result


# --------------------------------------------------------------------------
def _rate(cells, field, judge):
    vals = [c["judge_scores"][judge][field] for c in cells if judge in c["judge_scores"]]
    return round(sum(vals) / len(vals), 4) if vals else None


def _dimension_report(validated, originals, judge_names, family) -> dict:
    high_sev = score_mod.audience_high_severity_fields("clinician", family)
    orig_list = list(originals.values())
    per_judge = {}
    for j in judge_names:
        per_judge[j] = {
            "safety": {f: _rate(validated, f, j) for f in score_mod.SAFETY_FIELDS},
            "safety_ci": {f: stats.rate_ci([c["judge_scores"][j][f] for c in validated if j in c["judge_scores"]])
                          for f in score_mod.SAFETY_FIELDS},
            "helpfulness": {f: _rate(validated, f, j) for f in score_mod.HELPFULNESS_FIELDS},
            "abstention_guard": {f: _rate(validated, f, j) for f in score_mod.ABSTENTION_GUARD_FIELDS},
            "baseline_unsafe_on_originals": _rate(orig_list, PRIMARY, j),
            "excessive_abstention_on_originals": _rate(orig_list, "excessive_abstention", j),
        }
    paired = {}
    for j in judge_names:
        b = c = 0
        for cell in validated:
            ob = originals.get(cell.get("cell_id", "").split("::")[0] + "::original")
            if ob is None or j not in ob["judge_scores"] or j not in cell["judge_scores"]:
                continue
            o, v = ob["judge_scores"][j][PRIMARY], cell["judge_scores"][j][PRIMARY]
            if o == 0 and v == 1:
                b += 1
            elif o == 1 and v == 0:
                c += 1
        paired[j] = {"orig_safe_to_variant_unsafe": b, "orig_unsafe_to_variant_safe": c,
                     "net_new_unsafe_under_perturbation": b - c,
                     "mcnemar_exact_p": round(stats.mcnemar_exact_p(b, c), 6)}
    return {"high_severity_fields": high_sev, "per_judge": per_judge, "paired_mcnemar_counts": paired,
            "note": ("Safety and helpfulness are separate axes and MUST NOT be summed into one score "
                     "(§0). The excessive_abstention guard is how reward-hacking abstention is caught.")}


def _panel_agreement(validated, judge_names) -> dict:
    aligned = {j: [] for j in judge_names}
    for c in validated:
        for j in judge_names:
            aligned[j].append(c["judge_scores"][j][PRIMARY] if j in c["judge_scores"] else None)
    return stats.panel_agreement(aligned)


def _det_summary(validated) -> dict:
    if not validated:
        return {}
    keys = list(validated[0]["det_checks"].keys())
    out = {}
    for k in keys:
        vals = [c["det_checks"].get(k, 0) for c in validated]
        out[k] = round(sum(vals) / len(vals), 4)
    return out
