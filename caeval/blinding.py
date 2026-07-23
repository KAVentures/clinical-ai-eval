"""Blinded rendering of an answer — CANONICAL SOURCE (§8, §11):
clinical-ai-reconciliation/judge/blinding.py.

`render_blinded_answer` is inherited VERBATIM: it strips a provider's
self-identifying brand/domain (OpenEvidence is the only upstream provider that
self-identifies) while keeping citation structure so verifiability stays rateable.

`blinded_review_row` is the harness-level blinding the human-review queue needs
(§8): beyond brand redaction, it WITHHOLDS the columns that would unblind the
reviewer — subject model/vendor, arm/condition, and every judge label — leaving
only the case, the perturbation description, and the answer text to rate.
"""
import re

_DOMAIN = re.compile(r'(https?://)?(www\.)?openevidence\.com', re.I)
_BRAND = re.compile(r'open\s*evidence', re.I)

# Columns that must never reach a blinded reviewer (they encode the answer's
# provenance, the automated verdict the reviewer is meant to check, OR a CUE about
# what the perturbation removed — a reviewer must independently determine what is
# clinically missing, not confirm the intended failure. So perturbation_type and
# expected_missing_evidence are withheld from the safety-review packet.
_UNBLINDED_KEYS = {
    "subject", "subject_model", "model", "vendor", "provider", "arm", "condition",
    "perturbation_arm", "perturbation_type", "transform", "expected_missing_evidence",
    "judge", "judge_name", "judge_label", "judge_verdict",
    "unsafe_overconfident", "correct_abstention", "asks_for_missing_info",
    "identifies_removed_evidence", "potentially_harmful_treatment",
    "guideline_concordant_next_step", "excessive_abstention", "confidence_level",
    "panel_majority_unsafe", "any_judge_unsafe", "panel_any_unsafe", "disagreement",
    "det_checks", "failure_modes", "validity_valid", "validity_ambiguous",
}


def render_blinded_answer(answer_markdown):
    t = str(answer_markdown)
    t = _DOMAIN.sub('https://redacted-source.example', t)   # keep the citation, drop the identifying host
    t = _BRAND.sub('[redacted source]', t)
    return t


def blinded_review_row(row: dict) -> dict:
    """Project a full result row down to what a blinded clinician reviewer sees."""
    out = {}
    for k, v in row.items():
        if k in _UNBLINDED_KEYS:
            continue
        out[k] = v
    if "response_text" in row:
        out["response_text"] = render_blinded_answer(row["response_text"])
    return out
