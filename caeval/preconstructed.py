"""First-class import path for externally authored clinical variants.

Clinical-AI-Eval has two intentionally distinct manifestation paths:

1. built-in deterministic transforms, useful for development/smoke testing; and
2. preconstructed variants authored outside the harness (for example by a study
   authoring model or clinician) and then brought under the SAME content-addressed
   manifest, structural-validity and evaluation contracts.

Importing a preconstructed variant does NOT make it clinically valid. Review status
is provenance only; claim maturity still depends on the family declaration and
human evidence.
"""
from __future__ import annotations

from .perturbations import PerturbationResult, manifest_row

ALLOWED_REVIEW_STATUS = {"unreviewed", "clinician_reviewed"}


class PreconstructedVariantError(ValueError):
    pass


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if str(x).strip()]
    s = str(value).strip()
    return [s] if s else []


def build_manifest_row(
    family_id: str,
    case: dict,
    variant: dict,
    *,
    default_test_id: str,
    default_severity: str = "high",
    require_reviewed: bool = False,
) -> dict:
    """Normalize one externally authored variant into Clinical-AI-Eval's manifest.

    Required case fields: item_id, input_text.
    Required variant fields: input_text, expected_missing_evidence,
    construction_provenance, review_status.

    review_status=clinician_reviewed records that an external review occurred; the
    harness does not authenticate that reviewer or infer clinical validity from it.
    """
    item_id = str(case.get("item_id", "")).strip()
    original = str(case.get("input_text", ""))
    if not item_id or not original.strip():
        raise PreconstructedVariantError("case requires non-empty item_id and input_text")

    if variant.get("family_id") not in (None, "", family_id):
        raise PreconstructedVariantError(
            f"variant family_id {variant.get('family_id')!r} does not match {family_id!r}"
        )

    text = str(variant.get("input_text", ""))
    expected = str(variant.get("expected_missing_evidence", "")).strip()
    provenance = str(variant.get("construction_provenance", "")).strip()
    review_status = str(variant.get("review_status", "")).strip()

    if not text.strip():
        raise PreconstructedVariantError("preconstructed variant input_text is empty")
    if text.strip() == original.strip():
        raise PreconstructedVariantError("preconstructed variant does not change the source case")
    if not expected:
        raise PreconstructedVariantError("expected_missing_evidence is required")
    if not provenance:
        raise PreconstructedVariantError("construction_provenance is required")
    if review_status not in ALLOWED_REVIEW_STATUS:
        raise PreconstructedVariantError(
            f"review_status must be one of {sorted(ALLOWED_REVIEW_STATUS)}"
        )
    if require_reviewed and review_status != "clinician_reviewed":
        raise PreconstructedVariantError(
            "this import path requires review_status='clinician_reviewed'"
        )

    test_id = str(variant.get("test_id") or default_test_id).strip()
    severity = str(variant.get("severity") or default_severity).strip()
    removed_fields = _as_list(variant.get("removed_fields"))
    synthetic_added_text = str(variant.get("synthetic_added_text", ""))

    result = PerturbationResult(
        text=text,
        removed_fields=removed_fields,
        synthetic_added_text=synthetic_added_text,
        expected_missing_evidence=expected,
    )
    row = manifest_row(case, test_id, result)
    row.update({
        "family_id": family_id,
        "test_id": test_id,
        "transform": "preconstructed",
        "severity": severity,
        "variant_source": "preconstructed",
        "construction_provenance": provenance,
        "review_status": review_status,
        "reviewer_count": int(variant.get("reviewer_count") or 0),
        "reviewer_role": str(variant.get("reviewer_role", "")),
        "safe_response_strategy": str(variant.get("safe_response_strategy", "")),
        "source_variant_id": str(variant.get("source_variant_id", "")),
    })
    return row
