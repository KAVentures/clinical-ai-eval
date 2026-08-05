"""Single version source. Package metadata, provenance and docs derive from here."""
__version__ = "0.16.0"
EVAL_STANDARD_VERSION = "0.6"
# Do not hand-maintain a capability list here: run `clinical-ai-eval capabilities`,
# which is generated from the family declarations, selection rules, executor
# registry and maturity levels, and is enforced against the README by CI.
SCOPE = ("text-based clinical AI. Clinician-facing: missing-information, "
         "conflicting-evidence, retrieval-failure and citation-verification. "
         "Patient-facing: multi-turn triage. Every family is experimental; none is "
         "validated. See `clinical-ai-eval capabilities` for the authoritative list.")
