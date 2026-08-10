"""Multi-vendor procurement as a workflow, not just a comparison function.

`procurement.py` can compare results. This orchestrates getting them, which is
where the integrity properties actually live:

  * CONDITIONS ARE FROZEN BEFORE ANY VENDOR RUNS. The case pack, family set, judge
    panel and thresholds are hashed at `init` and every vendor run is checked
    against that hash. A buyer who tunes the pack after seeing vendor A has
    measured vendor B against a different bar.
  * THRESHOLDS ARE PREDECLARED. They are recorded at init, before any result
    exists, so a bar cannot be chosen to fit the product someone already prefers.
  * NO RANKING, NO RECOMMENDATION. Enforced by `procurement.compare` and by tests.

The dossier is what a committee reads. It states what was not measured as
prominently as what was.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import procurement
from .util import stable_hash_text, utc_now_iso

STATE_FILE = "procurement.json"


class ProcurementError(RuntimeError):
    pass


def _path(directory) -> Path:
    return Path(directory) / STATE_FILE


def load(directory) -> dict:
    f = _path(directory)
    if not f.exists():
        raise ProcurementError(f"no {STATE_FILE} in {directory}; run `procurement init` first")
    return json.loads(f.read_text())


def save(directory, state: dict) -> Path:
    f = _path(directory)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, indent=2))
    return f


def conditions_hash(conditions: dict) -> str:
    return stable_hash_text(json.dumps(conditions, sort_keys=True))[:16]


def init(directory, name: str, families: list, case_pack: dict, panel_config: str,
         hazards: list) -> dict:
    """Freeze the conditions. Everything after this is checked against them."""
    if _path(directory).exists():
        raise ProcurementError(f"{_path(directory)} exists; refusing to overwrite a "
                               f"frozen procurement — start a new one instead")
    if not hazards:
        raise ProcurementError(
            "a procurement needs PREDECLARED hazards with acceptance thresholds. Without "
            "them the bar is chosen after seeing the results, which is not a comparison.")
    for h in hazards:
        crit = h.get("acceptance_criterion", {})
        if not all(k in crit for k in ("metric", "operator", "threshold")):
            raise ProcurementError(
                f"hazard {h.get('hazard_id')!r} has no complete acceptance_criterion "
                f"(metric, operator, threshold)")
    conditions = {"families": sorted(families), "case_pack": case_pack,
                  "panel_config": panel_config, "hazards": hazards}
    state = {
        "name": name,
        "created_at": utc_now_iso(),
        "conditions": conditions,
        "conditions_hash": conditions_hash(conditions),
        "vendors": [],
        "results": {},
        "status": "open",
    }
    save(directory, state)
    return state


def add_vendor(directory, vendor_id: str, subject_spec: dict, blinded_label: str = "") -> dict:
    """Register a vendor. `blinded_label` is what reviewers see."""
    state = load(directory)
    if state["status"] != "open":
        raise ProcurementError(f"procurement is {state['status']}; cannot add vendors")
    if any(v["vendor_id"] == vendor_id for v in state["vendors"]):
        raise ProcurementError(f"vendor {vendor_id!r} already registered")
    label = blinded_label or f"Product {chr(ord('A') + len(state['vendors']))}"
    # Credentials never enter the stored state.
    from .adapters import redact
    state["vendors"].append({
        "vendor_id": vendor_id,
        "blinded_label": label,
        "subject_spec": redact(subject_spec),
        "registered_at": utc_now_iso(),
    })
    save(directory, state)
    return state


def ingest_package(directory, vendor_id: str, workspace) -> dict:
    """Attach a vendor result from a VERIFIED evidence package.

    This is the only ingestion path a buyer should use. `record_result` takes cells
    and an environment dict, which means the numbers can be edited between the run
    and the comparison — fine for a fixture, not for procurement.

    Here everything is EXTRACTED from the package and the package is verified first:

      1. `verify-package` must return VALID (nothing edited since the manifest);
      2. the recorded claim is RE-DERIVED from its axes, not read;
      3. family, target and case pack come from the package, not from the caller;
      4. the package digest is stored, so the comparison names exactly what it read.
    """
    import json as _json
    from pathlib import Path as _Path

    from . import claim as claim_mod
    from . import manifest as manifest_mod

    state = load(directory)
    if not any(v["vendor_id"] == vendor_id for v in state["vendors"]):
        raise ProcurementError(f"vendor {vendor_id!r} is not registered")

    ws = _Path(workspace)
    verdict = manifest_mod.verify_manifest(ws).get("verdict")
    if verdict != "VALID":
        raise ProcurementError(
            f"vendor {vendor_id!r}: evidence package at {ws} is {verdict}, not VALID. "
            f"A package whose artifacts do not match its manifest has been modified "
            f"since it was produced and cannot enter a comparison.")

    analysis = _json.loads((ws / "analysis.json").read_text())
    recorded = analysis.get("claim_authority") or {}
    if not recorded:
        raise ProcurementError(f"vendor {vendor_id!r}: package records no claim authority")
    rederived = claim_mod.compute(
        recorded.get("project_mode"), recorded.get("run_conformance"),
        recorded.get("family_maturity"), recorded.get("case_pack_authority", "unknown"),
        recorded.get("target_provenance", "unknown"))
    if rederived.effective_claim != recorded.get("effective_claim"):
        raise ProcurementError(
            f"vendor {vendor_id!r}: the package reports claim "
            f"{recorded.get('effective_claim')!r} but its own axes imply "
            f"{rederived.effective_claim!r}. Refusing a package whose claim has been "
            f"edited away from what it earned.")

    cells = [_json.loads(l) for l in (ws / "results.jsonl").read_text().splitlines()
             if l.strip()]
    man = _json.loads((ws / manifest_mod.MANIFEST_FILE).read_text())
    environment = {
        "conditions_hash": analysis.get("conditions_hash")
                           or _json.loads((ws / "run_meta.json").read_text()).get("conditions_hash"),
        "family_id": analysis.get("family_id"),
        "family_version": str((analysis.get("case_pack") or {}).get("version", "")),
        "case_pack_hash": (analysis.get("case_pack") or {}).get("content_hash"),
        "judge_panel_hash": stable_hash_text(json.dumps(sorted(analysis.get("panel", [])))),
        "judge_prompt_hash": (analysis.get("executor") or ""),
        "selection_rules_hash": recorded.get("family_maturity"),
        "eval_standard_version": (analysis.get("claim_authority") or {}).get("run_conformance"),
        "package_digest": man.get("manifest_hash") or man.get("package_hash"),
        "verify_verdict": verdict,
    }
    return record_result(directory, vendor_id, analysis["family_id"], cells, environment,
                         package=dict(environment,
                                      conformance=analysis.get("conformance_level"),
                                      claim=recorded,
                                      workspace=str(ws)))


def record_result(directory, vendor_id: str, family_id: str, cells: list,
                  environment: dict, package: dict | None = None) -> dict:
    """Attach one vendor's run. Refuses results produced under other conditions.

    Prefer `ingest_package()`: this entry point trusts the caller for the cells and
    the environment, which is adequate for a fixture and not for procurement.
    """
    state = load(directory)
    if not any(v["vendor_id"] == vendor_id for v in state["vendors"]):
        raise ProcurementError(f"vendor {vendor_id!r} is not registered")
    declared = environment.get("conditions_hash")
    if not declared:
        # An absent hash previously passed the guard entirely, so the frozen
        # conditions constrained only results that volunteered to be constrained.
        raise ProcurementError(
            f"vendor {vendor_id!r} supplied no conditions_hash. A result that does not "
            f"state which conditions produced it cannot be compared with one that does; "
            f"the frozen-conditions guarantee is not optional.")
    if declared != state["conditions_hash"]:
        raise ProcurementError(
            f"vendor {vendor_id!r} was evaluated under conditions {declared!r}, but this "
            f"procurement froze {state['conditions_hash']!r}. Comparing them would "
            f"confound the product with the evaluation environment.")
    state["results"].setdefault(vendor_id, {})[family_id] = {
        "cells": cells, "environment": environment, "recorded_at": utc_now_iso(),
        "package": package,
        # Provenance of the ingestion itself: a comparison should be able to say
        # whether a number came from a verified package or was handed in.
        "ingestion": "verified_package" if package else "caller_supplied"}
    save(directory, state)
    return state


def compare(directory, family_id: str) -> dict:
    """Build the buyer-facing comparison for one family."""
    state = load(directory)
    entries = []
    for v in state["vendors"]:
        res = state["results"].get(v["vendor_id"], {}).get(family_id)
        if res is None:
            continue
        entries.append({"product_id": v["blinded_label"],
                        "vendor_id": v["vendor_id"],
                        "cells": res["cells"],
                        "environment": res["environment"],
                        "family_maturity": "experimental"})
    if not entries:
        raise ProcurementError(f"no vendor results recorded for family {family_id!r}")
    hazards = [h for h in state["conditions"]["hazards"]
               if h.get("family", family_id) == family_id]
    out = procurement.compare(entries, hazards or state["conditions"]["hazards"])
    out["family_id"] = family_id
    out["conditions_hash"] = state["conditions_hash"]
    out["vendors_without_results"] = sorted(
        v["blinded_label"] for v in state["vendors"]
        if family_id not in state["results"].get(v["vendor_id"], {}))
    return out


def export_dossier(directory, out_path=None) -> str:
    """The committee document. States what was NOT measured as prominently as
    what was, and contains no ranking and no recommendation."""
    state = load(directory)
    L = [f"# Procurement dossier — {state['name']}", "",
         f"Conditions frozen: `{state['conditions_hash']}` at {state['created_at']}",
         f"Families: {', '.join(state['conditions']['families'])}",
         f"Case pack: `{state['conditions']['case_pack'].get('pack_id')}` "
         f"(clinician-reviewed: {state['conditions']['case_pack'].get('clinician_reviewed')})",
         "",
         "> This dossier contains **no combined score, no ranking and no buy/no-buy "
         "recommendation**. Weighting a missed red flag against an unnecessary referral "
         "is a clinical and organisational judgement that belongs to your committee; "
         "encoding it here would hide that judgement inside a number.", ""]
    for family_id in state["conditions"]["families"]:
        try:
            cmp_result = compare(directory, family_id)
        except ProcurementError as e:
            L += [f"## {family_id}", "", f"NOT MEASURED: {e}", ""]
            continue
        L += [procurement.render_markdown(cmp_result), ""]
        if cmp_result["vendors_without_results"]:
            L += [f"**Vendors with no result for this family:** "
                  f"{', '.join(cmp_result['vendors_without_results'])} — absence of a "
                  f"failure rate is not a passing rate.", ""]
    L += ["## Provenance", "",
          f"- vendors registered: {len(state['vendors'])}",
          f"- results recorded: {sum(len(v) for v in state['results'].values())}",
          "- every family involved is `experimental`: the measurement itself has not been "
          "calibrated against clinician judgement, so these rates describe the harness's "
          "behaviour as much as the products'."]
    text = "\n".join(L)
    if out_path:
        Path(out_path).write_text(text)
    return text
