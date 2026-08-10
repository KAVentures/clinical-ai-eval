"""The assurance lifecycle every executor must pass through.

v0.16 connected the user journey to the executors, and then the executors stopped.
The generic backend went generation -> panel -> claim authority -> review manifest
-> adjudication -> assessment manifest -> verify-package. The patient and RAG
backends wrote their own analysis, their own Markdown, and a HARDCODED `L0`.

That is the same defect one layer deeper: reachable, but outside the machinery that
makes a result trustworthy. In particular a hardcoded conformance is a claim made
by a literal rather than derived from what actually happened — the failure this
repository exists to prevent.

This module is the shared tail. An executor supplies:

    cells      per-unit records with binary fields the panel can score
    artifacts  whatever else it produced (episodes, traces)
    summary    its own domain summary, reported alongside, never instead

and the lifecycle supplies conformance, claim authority over all five axes, the
review manifest and signed packets, and the assessment manifest that
`verify-package` re-derives its verdict from.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import claim as claim_mod
from . import manifest as manifest_mod
from .util import utc_now_iso


def conformance_from(panel: dict | None, cells: list, l2_gate_passed: bool = False) -> str:
    """DERIVE the conformance level; never assert it.

    L0  no panel scored, fewer than two distinct BLINDED providers, or an all-mock
        panel — a synthetic judge structurally exercises the L1 machinery but
        cannot support a conclusion (EVAL_STANDARD.md §0)
    L1  >=2 distinct real blinded providers actually scored the cells
    L2  L1 plus a passed human adjudication gate

    All three conditions were wrong in v0.17 and every one failed OPEN:
    rubric-aware judges counted toward the quorum (the +64pp cueing gap this repo
    measured, re-imported), and a mock panel of `mock_a`/`mock_b` earned L1 while
    the generic pipeline correctly called the identical panel L0.
    """
    judges = (panel or {}).get("judges", []) or []
    # HEADLINE = BLINDED ONLY. A cued judge sees the defect specification; counting
    # it is counting one evaluator twice, once with the answer.
    blinded = [j for j in judges if j.get("mode", "blinded") == "blinded"]
    providers = {j.get("provider") or j.get("name") for j in blinded}
    all_mock = all(j.get("mock") for j in blinded) if blinded else True
    scored = [c for c in cells if c.get("panel_scored")]
    if not blinded or len(providers) < 2 or not scored or all_mock:
        return "L0"
    return "L2" if l2_gate_passed else "L1"


def panel_participation(panel: dict | None, cells: list) -> dict:
    """Which judges ACTUALLY scored — not which were configured.

    v0.17 recorded the configured panel in `analysis.json` and `provenance.json`
    even when no judge ran (the RAG executor never invokes one), so the evidence
    package named a panel that scored nothing.
    """
    judges = (panel or {}).get("judges", []) or []
    names = set()
    for c in cells:
        names |= set((c.get("panel_labels") or {}).keys())
    return {
        "configured": [j.get("name") for j in judges],
        "actually_scored": sorted(names),
        "panel_ran": bool(names),
        "n_cells_scored": sum(1 for c in cells if c.get("panel_scored")),
        "note": ("" if names else
                 "No judge scored any cell in this run: the configured panel is "
                 "recorded for provenance only and contributed nothing to these "
                 "results."),
    }


def finalize(ws, *, project, family_id: str, family: dict, executor_id: str,
             cells: list, summary: dict, pack_descriptor: dict,
             target_descriptor: dict, panel: dict | None = None,
             review_queue: list | None = None, binding: dict | None = None,
             artifacts: dict | None = None, l2_gate_passed: bool = False) -> dict:
    """Run the shared tail and emit an evidence package.

    Every executor calls this. Nothing here is executor-specific: the domain
    summary is carried through untouched, but conformance, claim authority, the
    review queue and the manifest are computed the same way for all of them.
    """
    ws.ensure()
    out = Path(ws.path)
    artifacts = artifacts or {}
    review_queue = review_queue or []

    for name, payload in artifacts.items():
        f = out / name
        if isinstance(payload, list):
            f.write_text("\n".join(json.dumps(x) for x in payload))
        elif isinstance(payload, str):
            f.write_text(payload)
        else:
            f.write_text(json.dumps(payload, indent=2))

    (out / "results.jsonl").write_text("\n".join(json.dumps(c) for c in cells))

    # responses.jsonl is the RAW subject output per unit, kept separate from the
    # scored cells so a re-judge can run against frozen responses without
    # regenerating them — the property that makes judging separable from
    # generation, and it must hold for every executor, not just the generic one.
    (out / "responses.jsonl").write_text("\n".join(json.dumps({
        "unit_id": c.get("item_id") or c.get("perturbation_id"),
        "perturbation_id": c.get("perturbation_id"),
        "perturbation_type": c.get("perturbation_type"),
        "response_text": c.get("response_text", ""),
    }) for c in cells))

    conformance = conformance_from(panel, cells, l2_gate_passed)
    participation = panel_participation(panel, cells)
    authority = claim_mod.compute(
        project_mode=getattr(project, "mode", "demonstration"),
        run_conformance=conformance,
        family_maturity=(family.get("maturity", {}) or {}).get("level", "experimental"),
        case_pack_authority=claim_mod.pack_authority(pack_descriptor),
        target_provenance=claim_mod.target_provenance(target_descriptor))

    _write_review_csv(out / "human_review.csv", review_queue)

    analysis = {
        "family_id": family_id,
        "executor": executor_id,
        "conformance_level": conformance,
        "claim_authority": authority.as_dict(),
        "case_pack": pack_descriptor,
        "target": target_descriptor,
        # Record what RAN, not what was configured.
        "panel": participation["actually_scored"],
        "panel_participation": participation,
        "n_cells": len(cells),
        "n_review_selected": len(review_queue),
        # The executor's own summary travels alongside, never replacing the axes.
        "summary": summary,
        "generated_at": utc_now_iso(),
    }
    (out / "analysis.json").write_text(json.dumps(analysis, indent=2))

    (out / "provenance.json").write_text(json.dumps(_provenance(
        family_id, executor_id, pack_descriptor, target_descriptor, panel,
        participation), indent=2))
    (out / "limitations.md").write_text(_limitations(analysis, authority))

    report_md = out / "final_report.md"
    report_md.write_text(_render(project, analysis, summary))

    ws.write_run_meta({
        "family_id": family_id, "executor": executor_id,
        "subject_spec": target_descriptor, "case_pack": pack_descriptor,
        "conformance_level": conformance,
        "claim_authority": authority.as_dict(),
        "panel": analysis["panel"],
    })
    if binding is not None:
        (out / "plan_binding.json").write_text(json.dumps(binding, indent=2))

    # The assessment manifest is what `verify-package` re-derives its verdict from.
    # Without it a run has no independently checkable evidence package at all.
    man = manifest_mod.build_manifest(out)
    return {
        "final_report_md": str(report_md),
        "conformance_level": conformance,
        "claim_label": authority.label,
        "claim_authority": authority.as_dict(),
        "n_review_selected": len(review_queue),
        "manifest": man,
        "summary": summary,
    }


def _provenance(family_id, executor_id, pack_descriptor, target_descriptor, panel,
                participation) -> dict:
    from .version import EVAL_STANDARD_VERSION, __version__
    import subprocess
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        commit = "unknown"
    return {
        "harness_version": __version__,
        "eval_standard_version": EVAL_STANDARD_VERSION,
        "git_commit": commit,
        "family_id": family_id,
        "executor": executor_id,
        "case_pack": pack_descriptor,
        "target": target_descriptor,
        "panel": [{"name": j.get("name"), "provider": j.get("provider"),
                   "model": j.get("model"), "mode": j.get("mode", "blinded"),
                   "mock": bool(j.get("mock")),
                   # explicit, because a configured judge that never ran must not
                   # read as evidence that it did
                   "scored_this_run": j.get("name") in participation["actually_scored"]}
                  for j in (panel or {}).get("judges", [])],
        "panel_participation": participation,
        "generated_at": utc_now_iso(),
    }


def _limitations(analysis: dict, authority) -> str:
    a = authority.as_dict()
    L = ["# Limitations", "",
         f"This run is `{a['effective_claim']}` — **{a['label']}**. The limiting axis "
         f"is `{a['limiting_axis']}`.", "",
         "## What this run does NOT establish", ""]
    if a["family_maturity"] == "experimental":
        L.append("- The test family is `experimental`: the MEASUREMENT itself has not "
                 "been calibrated against clinician judgement, so these rates describe "
                 "the harness's behaviour as much as the product's.")
    if a["target_provenance"] != "real":
        L.append(f"- Target provenance is `{a['target_provenance']}`: no conclusion about "
                 f"any real product follows from this run.")
    if a["case_pack_authority"] in ("demonstration_fixture", "unreviewed", "unknown"):
        L.append(f"- Case-pack authority is `{a['case_pack_authority']}`: the cases were "
                 f"not clinician-authored and reviewed, so the run cannot show the "
                 f"product is safe on clinically representative cases.")
    if analysis["conformance_level"] == "L0":
        part = analysis.get("panel_participation", {})
        if not part.get("panel_ran"):
            L.append("- Conformance L0: NO judge scored any cell in this run. The results "
                     "are deterministic measurements only.")
        else:
            L.append("- Conformance L0: the panel that scored these cells was synthetic "
                     "(mock) or had fewer than two distinct blinded providers, so it "
                     "cannot support a conclusion.")
    elif analysis["conformance_level"] == "L1":
        L.append("- Conformance L1: automated judges only. No human adjudication has "
                 "confirmed these labels.")
    cov = (analysis.get("summary") or {}).get("coverage") or {}
    if cov.get("stress_cells_skipped"):
        L.append(f"- {cov['stress_cells_skipped']} of {cov['stress_cells_possible']} stress "
                 f"cells could not run. Skipped cells are NOT passes.")
    L += ["", f"Blocked uses: {', '.join(a['blocked_claims'])}."]
    return "\n".join(L)


def _write_review_csv(path: Path, queue: list) -> None:
    """The BLINDED queue. Reviewers never see the required answer or the stress
    condition — a cued reviewer is not measuring the product (the +64pp cueing gap
    this repo already measured for judges applies to humans too)."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["unit_id", "review_reasons", "content_to_review",
                    "reviewer_unsafe", "reviewer_notes"])
        for q in queue:
            w.writerow([q.get("unit_id", ""), ";".join(q.get("strata", [])),
                        q.get("content", ""), "", ""])


def _render(project, analysis: dict, summary: dict) -> str:
    a = analysis["claim_authority"]
    L = [f"# Assessment — {getattr(project, 'name', 'run')}", "",
         f"Family: `{analysis['family_id']}` (executor: `{analysis['executor']}`, "
         f"maturity: {a['family_maturity']})",
         f"Conformance: **{analysis['conformance_level']}** "
         f"(derived from the panel that actually ran, not asserted)",
         f"Case pack: `{analysis['case_pack'].get('pack_id')}` "
         f"(authority: {a['case_pack_authority']})",
         f"Target: `{analysis['target'].get('target_id') or analysis['target'].get('name')}` "
         f"(provenance: {a['target_provenance']})", "",
         f"## Claim authority: {a['label']}", "",
         f"Effective claim `{a['effective_claim']}`, limited by **{a['limiting_axis']}**.", "",
         f"- Permitted: {', '.join(a['permitted_claims']) or 'nothing'}",
         f"- Blocked: {', '.join(a['blocked_claims'])}", "",
         "> Safety and usefulness are reported separately and are never combined into "
         "a single score.", ""]
    for section in ("safety", "usefulness", "retrieval", "generation"):
        block = summary.get(section)
        if isinstance(block, dict) and block:
            L += [f"## {section.title()}", "", "| endpoint | rate |", "|---|---|"]
            L += [f"| {k} | {v:.1%} |" if isinstance(v, (int, float)) else f"| {k} | {v} |"
                  for k, v in block.items()]
            L.append("")
    cov = summary.get("coverage")
    if isinstance(cov, dict):
        L += ["## Coverage", "",
              f"- run: {cov.get('stress_cells_run')} / {cov.get('stress_cells_possible')}",
              f"- skipped: {cov.get('stress_cells_skipped')}"]
        if cov.get("note"):
            L += ["", f"> {cov['note']}"]
        L.append("")
    L += ["## Review queue", "",
          f"{analysis['n_review_selected']} unit(s) routed for human review."]
    return "\n".join(L)
