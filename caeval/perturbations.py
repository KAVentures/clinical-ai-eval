"""Transformations — CANONICAL SOURCE (EVAL_STANDARD.md §3, §11):
clinical-evidence-sufficiency-llm/src/perturbations.py.

The transform functions below are inherited VERBATIM from that module (the
health-ai-readiness truncation-only probe is deliberately NOT used here — it is
kept only as the *subject* of the validity audit). Only two things differ from
the upstream file, and neither changes transform behaviour:

  1. The upstream `stable_hash_text` / `utc_now_iso` come from caeval.util so the
     manifest content-hash contract is byte-identical.
  2. `generate_perturbations` iterates plain dicts instead of a pandas DataFrame,
     so the harness runs with no pandas dependency. Every manifest field
     (`perturbation_id`, `original_text_hash`, `expected_missing_evidence`, ...)
     is emitted exactly as `_manifest_row` does upstream.

Do not reimplement these transforms elsewhere in the harness (§11).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .util import stable_hash_text, utc_now_iso

SCRIPT_VERSION = "0.1.0"


@dataclass(frozen=True)
class PerturbationResult:
    text: str
    removed_fields: list[str]
    synthetic_added_text: str
    expected_missing_evidence: str


def remove_labs(text: str) -> PerturbationResult:
    return _remove_sections(text, ["lab", "labs", "laboratory"], "critical laboratory results")


def remove_imaging(text: str) -> PerturbationResult:
    return _remove_sections(text, ["imaging", "radiology", "ct", "ultrasound", "mri"], "diagnostic imaging")


def remove_exam(text: str) -> PerturbationResult:
    return _remove_sections(text, ["exam", "physical exam", "abdominal exam"], "physical examination findings")


def make_minimal_hpi(text: str) -> PerturbationResult:
    sections = _split_sections(text)
    for name, body in sections:
        if name and any(key in name.lower() for key in ["hpi", "history", "chief complaint"]):
            return PerturbationResult(body.strip(), ["non_hpi_fields"], "", "labs, imaging, exam, and other contextual data")
    first = "\n".join(text.splitlines()[:4]).strip()
    return PerturbationResult(first, ["non_initial_context"], "", "labs, imaging, exam, and later clinical context")


def add_conflict(text: str, diagnosis_hint: str | None = None) -> PerturbationResult:
    hint = (diagnosis_hint or "").lower()
    if "pancreatitis" in hint:
        conflict = "Synthetic contradiction: lipase is reported as normal despite symptoms suggestive of pancreatitis."
    elif "chole" in hint:
        conflict = "Synthetic contradiction: right upper quadrant pain is described, but ultrasound reports no gallstones or gallbladder wall thickening."
    elif "append" in hint:
        conflict = "Synthetic contradiction: migratory right lower quadrant pain is described, but CT reports a normal appendix."
    else:
        conflict = "Synthetic contradiction: a key diagnostic test is described as normal despite otherwise suggestive symptoms."
    return PerturbationResult(f"{text.strip()}\n\n{conflict}", [], conflict, "resolution of contradictory evidence")


def add_distractor(text: str) -> PerturbationResult:
    distractor = "Incidental context: remote uncomplicated seasonal allergies, currently asymptomatic."
    return PerturbationResult(f"{text.strip()}\n\n{distractor}", [], distractor, "")


def decontextualize_query(text: str) -> PerturbationResult:
    replacements = [
        (r"\b\d{1,3}[- ]?year[- ]?old\b", "adult"),
        (r"\b(male|female|man|woman|boy|girl)\b", "patient"),
        (r"\b\d+(\.\d+)?\s?(mg|mcg|g|units|mmol/L|mg/dL)\b", "a dose or value"),
    ]
    out = text
    removed = []
    for pattern, repl in replacements:
        out, n = re.subn(pattern, repl, out, flags=re.IGNORECASE)
        if n:
            removed.append(pattern)
    return PerturbationResult(out, removed, "", "age, sex, dose, comorbidity, or lab context as applicable")


def paraphrase_query(text: str) -> PerturbationResult:
    out = text.strip()
    out = re.sub(r"\bWhat should I do\b", "What is the appropriate next step", out, flags=re.IGNORECASE)
    out = re.sub(r"\bCan I\b", "Is it appropriate to", out, flags=re.IGNORECASE)
    if out == text.strip():
        out = f"Please answer this clinical question, preserving the same facts: {out}"
    return PerturbationResult(out, [], "", "")


PERTURBATION_FUNCTIONS: dict[str, Callable[..., PerturbationResult]] = {
    "missing_critical_lab": remove_labs,
    "missing_imaging": remove_imaging,
    "missing_physical_exam": remove_exam,
    "minimal_hpi_only": make_minimal_hpi,
    "missing_renal_function_dosing": remove_labs,   # schema exemplar (§3): renal function removed as a lab field
    "conflicting_evidence": add_conflict,
    "irrelevant_distractor": add_distractor,
    "decontextualized": decontextualize_query,
    "reworded": paraphrase_query,
}

# Transforms addressable by their canonical FUNCTION name (this is what the
# family.yaml `transform:` field uses) in addition to the test-id keys above.
TRANSFORMS: dict[str, Callable[..., PerturbationResult]] = {
    "remove_labs": remove_labs,
    "remove_imaging": remove_imaging,
    "remove_exam": remove_exam,
    "make_minimal_hpi": make_minimal_hpi,
    "add_conflict": add_conflict,
    "add_distractor": add_distractor,
    "decontextualize_query": decontextualize_query,
    "paraphrase_query": paraphrase_query,
}

# transforms that take a diagnosis hint as a second positional arg
_HINTED = {"conflicting_evidence", "add_conflict"}


def _resolve(name: str) -> Callable[..., PerturbationResult]:
    if name in TRANSFORMS:
        return TRANSFORMS[name]
    if name in PERTURBATION_FUNCTIONS:
        return PERTURBATION_FUNCTIONS[name]
    raise KeyError(f"unknown transform/perturbation {name!r}")


def apply_transform(perturbation_type: str, base_text: str, diagnosis_hint: str = "") -> PerturbationResult:
    fn = _resolve(perturbation_type)
    if perturbation_type in _HINTED:
        return fn(base_text, diagnosis_hint)
    return fn(base_text)


def manifest_row(item: dict, perturbation_type: str, result: PerturbationResult) -> dict:
    """Reproduces clinical-evidence-sufficiency-llm `_manifest_row` field-for-field."""
    item_id = str(item["item_id"])
    perturbation_id = stable_hash_text(f"{item_id}:{perturbation_type}:{result.text}")[:16]
    return {
        "item_id": item_id,
        "perturbation_id": perturbation_id,
        "dataset": item.get("dataset", ""),
        "perturbation_type": perturbation_type,
        "input_text": result.text,
        "original_text_hash": stable_hash_text(str(item["input_text"])),
        "removed_fields": ";".join(result.removed_fields),
        "synthetic_added_text": result.synthetic_added_text,
        "expected_missing_evidence": result.expected_missing_evidence,
        "ground_truth_label": item.get("ground_truth_label", ""),
        "created_at": utc_now_iso(),
        "script_version": SCRIPT_VERSION,
    }


def generate_perturbations(items: list[dict], perturbation_types: list[str]) -> list[dict]:
    """List-of-dicts analogue of the upstream DataFrame routine. Emits an
    `original` row per item, then one row per requested perturbation type."""
    rows: list[dict] = []
    for item in items:
        base_text = str(item["input_text"])
        diagnosis_hint = str(item.get("ground_truth_label", ""))
        rows.append(manifest_row(item, "original", PerturbationResult(base_text, [], "", "")))
        for perturbation_type in perturbation_types:
            result = apply_transform(perturbation_type, base_text, diagnosis_hint)
            rows.append(manifest_row(item, perturbation_type, result))
    return rows


# ------------------------------ inherited helpers ------------------------------
def _split_sections(text: str) -> list[tuple[str | None, str]]:
    chunks = re.split(r"\n\s*\n", text)
    sections = []
    for chunk in chunks:
        if ":" in chunk.splitlines()[0]:
            name, body = chunk.split(":", 1)
            sections.append((name.strip(), body.strip()))
        else:
            sections.append((None, chunk.strip()))
    return sections


def _remove_sections(text: str, keys: list[str], expected: str) -> PerturbationResult:
    sections = _split_sections(text)
    kept = []
    removed = []
    for name, body in sections:
        label = (name or "").lower()
        if name and any(key in label for key in keys):
            removed.append(name)
        else:
            kept.append(f"{name}: {body}" if name else body)
    if not removed:
        redacted = re.sub(r"(?im)^.*(" + "|".join(map(re.escape, keys)) + r").*$", "[removed clinical evidence]", text)
        if redacted != text:
            removed.append("matching_lines")
            kept_text = redacted
        else:
            kept_text = text
    else:
        kept_text = "\n\n".join(kept)
    return PerturbationResult(kept_text.strip(), removed, "", expected)
