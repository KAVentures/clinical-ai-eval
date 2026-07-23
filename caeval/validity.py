"""Perturbation-validity audit — THE GATE (EVAL_STANDARD.md §5).
CANONICAL SOURCE (§11): health-ai-readiness-robustness/scripts/perturbation_validity.py.

For every generated variant, before it is scored, we record the three upstream
audit axes and the four schema-level checks, then compute headline quantities on
the VALIDATED subset only:

    trunc_form:   complete | midword       (grammatically natural boundary?)
    determinacy:  underdetermined | answerable  (safe answer impossible w/o clarifying?)
    task_type:    clinical | admin         (clinical decision vs rewriting/documentation)

    VALIDATED subset  ==  task_type == clinical  AND  determinacy == underdetermined
                          (plus trunc_form/schema gates the family requires)

CRITICAL DEPENDENCY (stated plainly, §5): at L1 these validity labels are
AUTOMATED (author-COI-equivalent — see `method="auto"` on every row). They are
NOT ground truth. At L2 a human confirms validity on the triage strata; any
variant this auto-labeler cannot confidently place (`ambiguous=True`) is EXCLUDED
from headline numbers and QUEUED for human review, never silently kept.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

# Admin/rewriting cues — if present, the task is documentation, not a clinical
# decision, and "appropriate uncertainty" is not the right bar (§5).
_ADMIN_CUES = ("rewrite", "summarize", "summarise", "draft a letter", "draft an email",
               "format this", "translate", "transcribe", "code this", "billing code",
               "icd-10", "cpt code", "proofread")


@dataclass
class ValidityLabel:
    perturbation_id: str
    perturbation_type: str
    trunc_form: str          # complete | midword  (structural)
    determinacy: str         # underdetermined | answerable  (STRUCTURAL proxy — not clinical)
    task_type: str           # clinical | admin  (structural)
    removed_evidence_is_load_bearing: bool   # STRUCTURAL proxy (an edit happened), NOT clinical
    result_is_coherent: bool
    result_is_underdetermined: bool          # STRUCTURAL proxy
    safe_response_is_definable: object       # None at L1 (auto-undecidable); bool only when a human sets it
    valid: bool              # passed the STRUCTURAL pre-filter (necessary, NOT sufficient for a headline)
    ambiguous: bool          # auto-labeler not confident -> human-review queue
    reasons: str
    method: str = "auto"     # auto (L1, structural) | human (L2, clinical)
    requires_human_validity_confirmation: bool = True   # ALWAYS true until a clinician confirms (§5)

    def as_dict(self) -> dict:
        return asdict(self)


def _ends_midword(text: str) -> bool:
    """True if the text appears cut mid-word (the truncation-probe failure the
    upstream audit guards against). Section-removal transforms end cleanly, so
    this is normally False for this family; we still check honestly."""
    t = (text or "").rstrip()
    if not t:
        return False
    if t.endswith(("[removed clinical evidence]", ".", "?", "!", ":", ")", '"')):
        return False
    last = t.split()[-1] if t.split() else ""
    # a bare alphabetic fragment with no terminal punctuation reads as a mid-word cut
    return last.isalpha() and len(last) <= 3


def audit_variant(row: dict, original_text: str, family: dict) -> ValidityLabel:
    """Auto-label one manifest row against the family's validity_checks (§5).

    `row` is a perturbation manifest row from perturbations.generate_perturbations.
    `original_text` is the unperturbed case text (to confirm the edit was real).
    """
    checks = (family or {}).get("validity_checks", {})
    allowed_trunc = set(checks.get("trunc_form", ["complete"]))
    allowed_determinacy = set(checks.get("determinacy", ["underdetermined"]))
    allowed_task = set(checks.get("task_type", ["clinical"]))

    ptype = row["perturbation_type"]
    text = row.get("input_text", "") or ""
    changed = text.strip() != (original_text or "").strip()
    expected = (row.get("expected_missing_evidence") or "").strip()
    removed_fields = (row.get("removed_fields") or "").strip()

    # --- axis 1: trunc_form ---
    trunc_form = "midword" if _ends_midword(text) else "complete"

    # --- axis 3: task_type (clinical vs admin) ---
    task_type = "admin" if any(cue in text.lower() for cue in _ADMIN_CUES) else "clinical"

    # --- schema checks (§3/§5) ---
    # IMPORTANT HONESTY FIX: this is a STRUCTURAL pre-filter, NOT clinical validation.
    # "removed_load_bearing" here only asserts the transform changed the text and named
    # some expected evidence — it does NOT establish that the removed evidence is
    # *clinically decision-relevant*, nor that the case is genuinely underdetermined
    # (the removed lab may be irrelevant, redundant, or one of several routes). True
    # clinical load-bearingness / determinacy / safe-response-definability can only be
    # confirmed by a human (L2, §5). So:
    #   - structural_removal is what we can auto-detect;
    #   - safe_response_is_definable is NOT auto-decidable -> None (human-required);
    #   - passing this pre-filter is NECESSARY, not sufficient, for a headline.
    structural_removal = bool(expected) and (changed or bool(removed_fields))
    removed_load_bearing = structural_removal  # structural proxy only (see note above)
    result_coherent = len(text.strip()) >= 20 and text.strip() != "[removed clinical evidence]"
    result_underdetermined = structural_removal  # structural proxy; clinical determinacy needs a human
    safe_response_definable = None   # NOT auto-decidable; confirmed by clinician at L2

    # --- axis 2: determinacy ---
    determinacy = "underdetermined" if result_underdetermined else "answerable"

    reasons = []
    if not changed:
        reasons.append("transform did not change the case text (nothing load-bearing removed)")
    if not expected:
        reasons.append("no expected_missing_evidence declared for this transform")
    if trunc_form not in allowed_trunc:
        reasons.append(f"trunc_form={trunc_form} not in {sorted(allowed_trunc)}")
    if determinacy not in allowed_determinacy:
        reasons.append(f"determinacy={determinacy} not in {sorted(allowed_determinacy)}")
    if task_type not in allowed_task:
        reasons.append(f"task_type={task_type} not in {sorted(allowed_task)}")
    if not result_coherent:
        reasons.append("result not coherent (empty or bare redaction marker)")

    # safe_response_definable is HUMAN-only and deliberately excluded from the auto gate.
    schema_ok = removed_load_bearing and result_coherent
    axes_ok = (trunc_form in allowed_trunc and determinacy in allowed_determinacy
               and task_type in allowed_task)
    valid = bool(schema_ok and axes_ok)   # = passed the STRUCTURAL pre-filter (necessary, not sufficient)

    # ambiguous == borderline cases the auto-labeler should not decide alone:
    # the edit changed the text but produced no declared missing evidence, or an
    # answerable-looking clinical edit. These go to human review (§5, §8).
    ambiguous = bool(changed and not valid and task_type == "clinical" and result_coherent)

    return ValidityLabel(
        perturbation_id=row["perturbation_id"],
        perturbation_type=ptype,
        trunc_form=trunc_form,
        determinacy=determinacy,
        task_type=task_type,
        removed_evidence_is_load_bearing=removed_load_bearing,
        result_is_coherent=result_coherent,
        result_is_underdetermined=result_underdetermined,
        safe_response_is_definable=safe_response_definable,
        valid=valid,
        ambiguous=ambiguous,
        reasons="; ".join(reasons),
    )


def audit_manifest(manifest: list[dict], originals: dict[str, str], family: dict) -> dict[str, ValidityLabel]:
    """Audit every non-`original` row. Returns {perturbation_id: ValidityLabel}.
    `originals` maps item_id -> unperturbed text."""
    out: dict[str, ValidityLabel] = {}
    for row in manifest:
        if row["perturbation_type"] == "original":
            continue
        out[row["perturbation_id"]] = audit_variant(row, originals.get(row["item_id"], ""), family)
    return out


# --------------------------------------------------------------------------
# Sensitivity reporting across strata — mirrors the upstream `strata`/`summ`
# structure so the report can show that the headline persists on the validated
# subset (the direction that supports validity, §5).
# --------------------------------------------------------------------------
def stratum_counts(labels: list[ValidityLabel]) -> dict:
    def frac(sel):
        return round(len(sel) / len(labels), 4) if labels else None
    strata = {
        "ALL": labels,
        "VALIDATED: clinical & underdetermined": [x for x in labels if x.task_type == "clinical" and x.determinacy == "underdetermined"],
        "clinical only": [x for x in labels if x.task_type == "clinical"],
        "admin/rewriting only": [x for x in labels if x.task_type == "admin"],
        "underdetermined only": [x for x in labels if x.determinacy == "underdetermined"],
        "answerable only": [x for x in labels if x.determinacy == "answerable"],
        "midword truncation": [x for x in labels if x.trunc_form == "midword"],
        "grammatically complete": [x for x in labels if x.trunc_form == "complete"],
    }
    return {k: {"n": len(v), "frac_of_all": frac(v),
                "n_valid": sum(1 for x in v if x.valid),
                "n_ambiguous_for_review": sum(1 for x in v if x.ambiguous)}
            for k, v in strata.items()}
