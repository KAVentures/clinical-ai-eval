"""caeval — reference implementation of the EVAL_STANDARD.md clinical-AI
evaluation protocol (text-based, patient- and clinician-facing).

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

__version__ = "0.1.0"
CONFORMANCE_LEVELS = ("L0", "L1", "L2")
