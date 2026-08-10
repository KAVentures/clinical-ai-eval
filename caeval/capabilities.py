"""What this build can actually run — DERIVED, never hand-written.

Documentation drift was a real defect here: the README described two implemented
families while the code shipped five; PRODUCT_V1.md said patient evaluation did
not exist after it did. A user could not determine what was supported.

Four registries must agree, and disagreement is a build failure, not a doc nit:

  SDK declaration   tests/<family>/family.yaml exists and parses
  selection rules   selection_rules.yaml marks it implemented
  executor          caeval/executors.py knows how to run it
  maturity          the family declares a maturity level

A family implemented in the SDK but absent from selection is unreachable. One
selected with no executor crashes the run — or is silently run by the wrong
backend, which is worse.
"""
from __future__ import annotations

from . import executors, selection
from .util import repo_root


def _family_ids() -> list:
    d = repo_root() / "tests"
    return sorted(p.name for p in d.iterdir() if (p / "family.yaml").exists())


def table() -> list:
    import yaml
    rules = selection.load_rules().get("suites", {})
    rows = []
    for fid in _family_ids():
        try:
            fam = yaml.safe_load((repo_root() / "tests" / fid / "family.yaml").read_text())
        except Exception as e:  # noqa: BLE001
            rows.append({"family_id": fid, "error": f"unparsable declaration: {e}"})
            continue
        meta = rules.get(fid, {})
        rows.append({
            "family_id": fid,
            "declared_in_sdk": bool(fam and fam.get("family_id")),
            "selectable": bool(meta.get("implemented")),
            "executor": (executors.FAMILY_EXECUTORS.get(fid) or None),
            "maturity": ((fam or {}).get("maturity") or {}).get("level"),
            "conformance_ceiling": (fam or {}).get("conformance_ceiling_this_build", "L2"),
            "audiences": (fam or {}).get("audiences", []),
            "profiles": (fam or {}).get("applies_to_profiles", []),
            "blocked_reason": meta.get("blocked_reason", ""),
        })
    return rows


def check_consistency() -> list:
    """Every disagreement between the registries. Empty means consistent."""
    problems = []
    for row in table():
        fid = row["family_id"]
        if row.get("error"):
            problems.append(f"{fid}: {row['error']}")
            continue
        if row["selectable"] and not row["executor"]:
            problems.append(
                f"{fid}: selection marks it implemented but no executor is registered — "
                f"it would be planned and then crash, or be run by the wrong backend")
        if row["selectable"] and not row["maturity"]:
            problems.append(f"{fid}: runnable but declares no maturity level")
        if row["executor"] and not row["selectable"]:
            problems.append(
                f"{fid}: has an executor but selection marks it not implemented — the "
                f"capability exists and no user journey reaches it")
        if row["selectable"] and not row["profiles"]:
            problems.append(
                f"{fid}: runnable but applies_to_profiles is empty, so nothing routes to it")
    for fid in executors.FAMILY_EXECUTORS:
        if fid not in {r["family_id"] for r in table()}:
            problems.append(f"{fid}: has an executor but no family declaration")
    return problems


def render_markdown() -> str:
    rows = table()
    L = ["## What this build can run", "",
         "Generated from the family declarations, selection rules, executor registry "
         "and maturity levels. Do not hand-edit.", "",
         "| family | runnable | executor | maturity | max conformance | audiences |",
         "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (not x.get("selectable"), x["family_id"])):
        if r.get("error"):
            L.append(f"| `{r['family_id']}` | ERROR | — | — | — | {r['error']} |")
            continue
        L.append(f"| `{r['family_id']}` | {'yes' if r['selectable'] else 'no'} | "
                 f"{r['executor'] or '—'} | {r['maturity'] or '—'} | "
                 f"{r.get('conformance_ceiling') or '—'} | "
                 f"{', '.join(r['audiences']) or '—'} |")
    blocked = [r for r in rows if not r.get("selectable") and not r.get("error")]
    if blocked:
        L += ["", "### Declared but not runnable", ""]
        L += [f"- `{r['family_id']}`: {r['blocked_reason'] or 'no reason recorded'}"
              for r in blocked]
    ceilinged = [r for r in rows if r.get("conformance_ceiling") not in (None, "L2")]
    if ceilinged:
        L += ["", "### Conformance ceilings in this build", ""]
        L += [f"- `{r['family_id']}`: cannot exceed **{r['conformance_ceiling']}** — "
              f"`judge`/`report`/`adjudicate` are implemented for the generic "
              f"backend only, so human adjudication (L2) is unreachable."
              for r in ceilinged]
    L += ["", "**Every family is `experimental`.** None has been calibrated against "
          "clinician judgement, so no result from this build can support a published "
          "finding, a procurement decision, or a release gate."]
    return "\n".join(L)
