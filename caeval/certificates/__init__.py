"""Evidence-grounded decision certificates (the ClinArgCert layer).

DETERMINISTIC side of the deterministic-vs-judge split (EVAL_STANDARD.md §7).
Emits CERTIFIED_CONDITIONAL / DEFER / BLOCK for a proposed clinical action
against a version-pinned rule bundle and a patient snapshot.

STATUS: the verifier and the minimum-information solver are implemented and
adversarially tested. The `decision_certifiability` FAMILY remains BLOCKED
because rule bundles, provenance chains, action extraction and clinician-authored
critical-question sets do not exist yet. Implementing a verifier does not make the
measurement valid — see tests/decision_certifiability/family.yaml.
"""
from .verifier import (  # noqa: F401
    BLOCK, CERTIFIED, DEFER, EFFECTS, SEVERITIES,
    CertificateError, Finding, VerificationResult, verify_certificate,
)
from .mmip import (  # noqa: F401
    UNKNOWN, MMIPError, greedy_query_set, is_decision_determining, minimum_query_sets,
)
