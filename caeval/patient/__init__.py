"""Patient-facing readiness substrate — multi-turn, world-based, paired.

NOT an adaptation of the completed-vignette evaluator. A patient episode is a
CONVERSATION against a hidden clinical world whose facts are disclosed only when
asked, so the thing measured is the TRAJECTORY: was the necessary history
obtained, was escalation timely, was the patient falsely reassured, was
safety-netting specific, did the system stay consistent across turns.

Design provenance (stated so the borrowing is explicit):
  * Gu et al. "The Illusion of Readiness" — modular stress tests over information
    removal, necessity, format sensitivity, distractors, evidence substitution and
    reasoning fidelity, reported disaggregated rather than as one accuracy number.
  * health-ai-readiness-robustness — open-ended missing-information probing,
    perturbation-validity auditing, blinded cross-provider judges.
  * clinical-evidence-sufficiency-llm — paired design, safety and helpfulness
    scored separately, explicit over-abstention controls.
  * Real-POCQi — specialty-matched blinded experts, independent rating dimensions
    rather than a single preference.

EVERY patient family stays `experimental`. This substrate makes patient evaluation
POSSIBLE to build; it does not make any patient claim valid.
"""
from .world import (  # noqa: F401
    DISPOSITIONS, ClinicalWorld, DisclosurePolicy, Fact, disposition_rank,
    is_undertriage, is_overtriage,
)
from .session import EpisodeTrace, PatientSimulator, run_episode  # noqa: F401
from .extraction import extract_actions, extract_disposition, extract_questions  # noqa: F401
from .scoring import paired_effect, score_episode  # noqa: F401
from .registry import PATIENT_FAMILY_MATURITY, CasePackSpec, TargetSpec  # noqa: F401
from .run import run_case_pack, summarize  # noqa: F401
from .interop import claim_inputs, review_queue, to_records  # noqa: F401
