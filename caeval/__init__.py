"""caeval — reference implementation of the EVAL_STANDARD.md clinical-AI
evaluation protocol. Scope of THIS build: text-based, CLINICIAN-facing decision
support. Patient-facing evaluation is declared but NOT implemented and fails
closed (see tests/patient_red_flag/family.yaml).

The primary output is a SCREEN plus an EVIDENCE PACKAGE, never a
deployment-readiness verdict (EVAL_STANDARD.md §0). Every module names the ONE
upstream implementation it canonicalizes (§11); it does not reimplement logic
that a provenance repo already owns.

Provenance (see EVAL_STANDARD.md §11):
  transformations            -> clinical-evidence-sufficiency-llm/src/perturbations.py
  perturbation-validity audit-> health-ai-readiness-robustness/scripts/perturbation_validity.py
  scoring fields + judge     -> clinical-evidence-sufficiency-llm/src/score_outputs.py + prompts/judge_prompt.txt
  judge provider interface   -> clinical-ai-reconciliation/judge/providers.py
  disagreement export        -> clinical-ai-reconciliation/judge/export_disagreement.py
  agreement statistics       -> clinical-evidence-sufficiency-llm/src/reliability.py
  human-review sampling+blind-> clinical-ai-reconciliation/judge/sample_human_study.py + judge/blinding.py
  failure-mode taxonomy      -> Gu et al., Nature Medicine (health-ai-readiness) Table 1 harm ontology
"""

from .version import __version__, EVAL_STANDARD_VERSION, SCOPE  # noqa: F401
CONFORMANCE_LEVELS = ("L0", "L1", "L2")
