"""Evidence package (EVAL_STANDARD.md §9). The evidence package IS the product; a
dashboard is optional. Emits results.jsonl, human_review.csv, limitations.md,
final_report.md, provenance.json. The report's FIRST paragraph always states that
the output is a screen + evidence package, never a deployment-readiness verdict
(§0), and declares the conformance level (§1). If an adjudication_report.json is
present in <out>/adjudication (from the L2 `adjudicate` stage), its findings are
folded into the report and the level is upgraded within audited scope.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import __version__, score as score_mod
from .review import select_for_review, write_blinded_csv
from .util import stable_hash_text, utc_now_iso


def _pct(x):
    return "n/a" if x is None else f"{x:.0%}"


def _ci(d):
    if not d or d.get("ci95") in (None, [None, None]):
        return "n/a"
    lo, hi = d["ci95"]
    return f"{d['rate']:.0%} [{lo:.0%},{hi:.0%}]" if d.get("rate") is not None else "n/a"


def build_evidence_package(run_result: dict, family: dict, out_dir: str) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    subject_spec = run_result["subject_spec"]

    # results.jsonl
    with open(out / "results.jsonl", "w") as f:
        for cell in run_result["variant_cells"]:
            f.write(json.dumps({**_clean(cell), "arm": subject_spec.get("arm", "")}) + "\n")
        for cell in run_result["original_cells"].values():
            f.write(json.dumps({**_clean(cell), "arm": subject_spec.get("arm", "")}) + "\n")

    # human_review.csv (blinded, §8)
    high_sev = run_result["dimensions"]["high_severity_fields"]
    review_cells = []
    for cell in run_result["variant_cells"]:
        hs = {fld: int(any(s.get(fld) == 1 for s in cell["judge_scores"].values())) for fld in high_sev}
        review_cells.append({**cell, **hs, "arm": subject_spec.get("arm", "")})
    selected = select_for_review(review_cells, _ValidityView(run_result["validity"]),
                                 high_sev, set(run_result["validated_cell_ids"]))
    n_review = write_blinded_csv(selected, str(out / "human_review.csv"))

    # provenance + limitations + report
    (out / "provenance.json").write_text(json.dumps(_provenance(run_result, family), indent=2))
    (out / "limitations.md").write_text(_limitations_md(run_result))

    adjudication = _load_adjudication(out)
    (out / "final_report.md").write_text(_final_report_md(run_result, family, n_review, selected, adjudication))

    return {
        "out_dir": str(out), "results_jsonl": str(out / "results.jsonl"),
        "human_review_csv": str(out / "human_review.csv"), "n_review_selected": n_review,
        "final_report_md": str(out / "final_report.md"), "limitations_md": str(out / "limitations.md"),
        "provenance_json": str(out / "provenance.json"),
        "conformance_level": adjudication["level"] if adjudication else run_result["panel"]["conformance_level"],
    }


def _clean(cell: dict) -> dict:
    return {k: v for k, v in cell.items() if not k.startswith("_")}


class _ValidityView:
    def __init__(self, d): self._d = d
    def get(self, pid):
        v = self._d.get(pid)
        return None if v is None else type("VL", (), {"ambiguous": v["ambiguous"], "valid": v["valid"]})()


def _load_adjudication(out: Path):
    p = out / "adjudication" / "adjudication_report.json"
    return json.loads(p.read_text()) if p.exists() else None


def _provenance(run_result: dict, family: dict) -> dict:
    from .score import load_judge_prompt
    cells = run_result["variant_cells"]
    return {
        "generated_at": utc_now_iso(), "harness_version": __version__, "eval_standard": "v0.1",
        "git_commit": "(not a git repo — record commit here when versioned)",
        "target": run_result["subject_spec"], "family_id": run_result["family_id"],
        "inference_settings": {"note": "subject/judge temperature & effort are set per adapter; "
                               "mock adapters are deterministic (no sampling)."},
        "panel": {"judges": [{"name": j["name"], "provider": j["provider"], "model": j.get("model"),
                              "mock": bool(j.get("mock"))} for j in run_result["panel"]["judges"]],
                  "distinct_providers": run_result["panel"]["distinct_providers"],
                  "min_distinct_providers": run_result["panel"]["min_distinct_providers"],
                  "conformance_level": run_result["panel"]["conformance_level"]},
        "judge_prompt_sha256": stable_hash_text(load_judge_prompt()),
        "scoring_fields": score_mod.SCORING_FIELDS, "review_sampling_seed": 62,
        "panel_agreement": run_result.get("panel_agreement"),
        "counts": {"cases": run_result["n_cases"], "variants_generated": run_result["n_variants_generated"],
                   "variants_validated": run_result["n_variants_validated"],
                   "variants_excluded": run_result["n_variants_excluded"]},
        "case_content_hashes": sorted({c["item_id"] + ":" + stable_hash_text(c["input_text"])[:16] for c in cells}),
        "exclusions": [{"cell": c["cell_id"],
                        "reason": run_result["validity"].get(c.get("perturbation_id"), {}).get("reasons") or "failed validity gate"}
                       for c in cells if not c.get("validity_valid")],
    }


def _limitations_md(run_result: dict) -> str:
    all_mock = run_result["panel"]["all_mock"]
    L = ["# limitations.md", "",
         "Restated every run (EVAL_STANDARD.md §13). This tool produces a **screen plus an "
         "evidence package, never a deployment-readiness verdict** (§0).", ""]
    if all_mock:
        L += ["## THIS RUN USED A SYNTHETIC (MOCK) JUDGE PANEL",
              "- Judges are deterministic mock providers, not real LLMs. Every number is illustrative "
              "machinery output, NOT a finding. The run is `NON_CONFORMANT` for any claim (below L1).",
              "- The subject is a deliberately-constructed mock product (harness self-validation, §12).", ""]
    L += ["## Standing weaknesses (§13)",
          "- A voluntary open protocol may be admired but unused; adoption is a procurement/standards motion.",
          "- Teams are not incentivized to uncover their own failures.",
          "- The process is model-call expensive.",
          "- Valid clinical cases are scarce and intended-use specification is effortful.",
          "- Fully automated conclusions remain unreliable — hence L2 exists, and no run outputs a "
          "deployment-readiness verdict.", "",
          "## Method-specific caveats",
          "- Validity labels here are AUTOMATED (author-COI-equivalent, §5). Only L2 has a human confirm "
          "validity on the triage strata.",
          "- The automated label is a high-sensitivity / low-specificity screen (§7): a 'safe' automated "
          "verdict is weaker evidence than an 'unsafe' flag.",
          "- The `conflicting_evidence` family inherits the canonical `add_conflict`, whose non-hinted "
          "branch appends a GENERIC contradiction; treat that arm as exploratory and grounded only where "
          "a diagnosis hint matched (disclosed, matching the evidence-sufficiency study's finding).",
          "- Tests can target the same removed section on the same case (e.g. missing_critical_lab and "
          "missing_renal_function_dosing both remove labs on the renal case); counted as distinct tests, "
          "disclosed here, not silently merged."]
    return "\n".join(L) + "\n"


def _final_report_md(run_result, family, n_review, selected, adjudication) -> str:
    p = run_result["panel"]
    level = adjudication["level"] if adjudication else p["conformance_level"]
    dims = run_result["dimensions"]
    subject = run_result["subject_spec"]
    L = [f"# Clinical-AI evaluation screen — {subject.get('name','target')} (arm: {subject.get('arm','n/a')})", ""]
    L += [f"**This is a SCREEN plus an evidence package, not a deployment-readiness verdict (EVAL_STANDARD.md §0).** "
          f"Conformance level: **{level}**. {p['note']}", ""]
    L += [f"- Family: `{run_result['family_id']}`  ·  cases: {run_result['n_cases']}  ·  variants generated: "
          f"{run_result['n_variants_generated']}  ·  validated (headline subset): {run_result['n_variants_validated']}  ·  "
          f"excluded/queued: {run_result['n_variants_excluded']}",
          f"- Judge panel ({len(p['names'])}, distinct providers {p['distinct_providers']}): {', '.join(p['names'])}", ""]

    L += ["## The two non-negotiable claims (§0)",
          "1. **Safety and helpfulness are scored separately and never collapsed.**",
          "2. **The evaluator is part of the measurement** — >=2 different-provider judges, disagreement "
          "reported, and a human-review queue for any headline.", ""]

    L += ["## Separated scoring dimensions on the VALIDATED subset (per judge, Wilson 95% CI on safety)",
          "Never summed into a single score (§6).", "",
          "| judge | unsafe_overconf [CI] | harmful_tx [CI] | guideline_next | identifies_missing | asks_missing | correct_abstention | EXCESSIVE_abstention |",
          "|---|---|---|---|---|---|---|---|"]
    for j in p["names"]:
        d = dims["per_judge"][j]
        L.append(f"| {j} | {_ci(d['safety_ci']['unsafe_overconfident'])} | {_ci(d['safety_ci']['potentially_harmful_treatment'])} | "
                 f"{_pct(d['helpfulness']['guideline_concordant_next_step'])} | {_pct(d['helpfulness']['identifies_removed_evidence'])} | "
                 f"{_pct(d['helpfulness']['asks_for_missing_info'])} | {_pct(d['abstention_guard']['correct_abstention'])} | "
                 f"{_pct(d['abstention_guard']['excessive_abstention'])} |")
    L += ["", "**Baseline (originals, fully-specified):** " + ", ".join(
        f"{j}: unsafe {_pct(dims['per_judge'][j]['baseline_unsafe_on_originals'])}, "
        f"excessive-abstention {_pct(dims['per_judge'][j]['excessive_abstention_on_originals'])}" for j in p["names"]),
        "", "_The excessive-abstention column distinguishes a genuinely safer system from one that just "
        "refuses everything (which scores 'safe' on overconfidence but lights up this column and the baseline)._", ""]

    L += ["## Paired effect of the perturbation (McNemar exact, per judge)",
          "Original vs validated variant (§6). b = safe→unsafe; c = unsafe→safe.", "",
          "| judge | b | c | net new unsafe | McNemar p |", "|---|---|---|---|---|"]
    for j in p["names"]:
        m = dims["paired_mcnemar_counts"][j]
        L.append(f"| {j} | {m['orig_safe_to_variant_unsafe']} | {m['orig_unsafe_to_variant_safe']} | "
                 f"{m['net_new_unsafe_under_perturbation']} | {m['mcnemar_exact_p']} |")
    L.append("")

    ds = run_result["disagreement_summary_validated"]
    ag = run_result.get("panel_agreement", {})
    L += ["## Evaluator-sensitivity / judge disagreement (§7)",
          f"- Validated cells: {ds['n_cells']}  ·  per-judge unsafe rate: { {k: _pct(v) for k, v in ds['per_judge_unsafe_rate'].items()} }",
          f"- Disagreement rate: {_pct(ds['disagreement_rate'])}  ·  panel-any-unsafe {_pct(ds['panel_any_unsafe_rate'])} "
          f"vs panel-majority-unsafe {_pct(ds['panel_majority_unsafe_rate'])}",
          f"- Inter-judge agreement: pairwise Cohen κ {ag.get('pairwise_cohen_kappa')}"
          + (f", Krippendorff α {ag.get('krippendorff_alpha')}" if ag.get('krippendorff_alpha') is not None else "")
          + f", mean % agreement {_pct(ag.get('mean_pairwise_percent_agreement'))}",
          f"- Solo unsafe flags by judge (direction of disagreement): {ds['solo_unsafe_flag_by_judge']}",
          f"- {ds['note']}", ""]

    L += ["## Deterministic checks on the validated subset (§7 — judge-independent signals)",
          "Regex-level corroboration, not a final label. Rates over validated variants:", ""]
    for k, v in (run_result.get("deterministic_checks_summary") or {}).items():
        L.append(f"- `{k}`: {_pct(v)}")
    L.append("")

    L += ["## Perturbation-validity sensitivity (§5)",
          "| stratum | n | n_valid | n_ambiguous_for_review |", "|---|---|---|---|"]
    for k, v in run_result["validity_strata"].items():
        L.append(f"| {k} | {v['n']} | {v['n_valid']} | {v['n_ambiguous_for_review']} |")
    L.append("")

    fm = {}
    for c in run_result["variant_cells"]:
        for f_ in c.get("failure_modes", []):
            fm[f_] = fm.get(f_, 0) + 1
    L += ["## Confirmed-unsafe failure modes (Gu et al. harm ontology, §11)",
          ("- " + ", ".join(f"`{k}`: {v}" for k, v in sorted(fm.items()))) if fm else "- none flagged by the panel.", ""]

    reasons = {}
    for s in selected:
        for r in s.get("review_reasons", []):
            reasons[r] = reasons.get(r, 0) + 1
    L += ["## Human-review queue (§8)",
          f"- {n_review} cells selected for BLINDED human review -> `human_review.csv`. Reasons: {reasons}"]
    if adjudication:
        L += ["", "## Human adjudication — L2 (§1, §8)", adjudication["summary_md"]]
    else:
        L += ["- L2 (findings, not just 'automated screen suggests') requires this queue COMPLETED and "
              "adjudicated (`caeval.cli adjudicate`). NOT yet done for this run."]
    L += ["", "## Evidence package (§9)",
          "- `results.jsonl` · `human_review.csv` · `limitations.md` · `provenance.json` · this report.",
          "", "_See `limitations.md` — restated every run._"]
    return "\n".join(L) + "\n"
