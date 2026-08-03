"""Deterministic certificate verifier — hardened v0.6 contract.

Emits CERTIFIED_CONDITIONAL / DEFER / BLOCK for a proposed clinical action against
a version-pinned rule bundle and a patient snapshot. Makes NO clinical knowledge
claims: it verifies structure, closure and provenance of a certificate whose
semantic content comes from external pinned sources.

THE DEFECT THIS CONTRACT EXISTS TO PREVENT
------------------------------------------
An earlier reference implementation skipped any check whose `severity` was not the
exact lowercase string "critical":

    if item.get("severity") != "critical":
        continue                      # never reaches the "present" branch

A skipped check cannot BLOCK, cannot DEFER, and emits NO finding — so a PRESENT
contraindication certified with zero findings whenever severity was spelled
"high", "Critical", or omitted. That collided with this repository's own severity
vocabulary (high | moderate | low), in which "critical" never appears.

TWO INDEPENDENT AXES (the structural fix)
-----------------------------------------
Conflating "how clinically bad is this" with "what does it do to the verdict" is
what made a spelling change catastrophic. They are now separate REQUIRED fields:

    severity          : critical | high | moderate | low   (clinical importance)
    certificate_effect: block | defer                      (verdict semantics)

`certificate_effect` alone decides the verdict. `severity` is carried for triage
and reporting and CANNOT silence a check. Neither may be omitted, and an
unrecognized value in either escalates and is reported — never skipped.

INVARIANTS (adversarially tested)
---------------------------------
  I1  A malformed or unrecognized input NEVER yields CERTIFIED_CONDITIONAL.
  I2  Zero findings can never accompany a malformed input.
  I3  Every skipped or unusable check emits a finding.
  I4  Every DEFER names at least one concrete next information item.
  I5  No check can be silenced by how its severity is spelled.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

CERTIFIED = "CERTIFIED_CONDITIONAL"
DEFER = "DEFER"
BLOCK = "BLOCK"

# Clinical importance. Reported/triaged on; NEVER decides the verdict.
SEVERITIES = ("critical", "high", "moderate", "low")
# Verdict semantics. This — and only this — decides what a failed check does.
EFFECTS = ("block", "defer")

# The certificate CONTRACT. A key that is absent is NOT an empty list: it means the
# checklist was never run, which cannot be distinguished from "ran and found
# nothing" — so it must never certify (P0-1).
REQUIRED_TOP_LEVEL = ("certificate_id", "patient_snapshot", "evidence_bundle",
                      "action", "support", "critical_questions", "contraindications")

CQ_STATUSES = ("pass", "fail", "unknown", "not_applicable")
CONTRA_STATUSES = ("present", "absent", "unknown", "not_applicable")
SOURCE_STATUSES_OK = ("active", "valid")


class CertificateError(ValueError):
    """The certificate is structurally unusable."""


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    severity: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VerificationResult:
    verdict: str
    findings: tuple
    next_information: tuple

    def to_dict(self) -> dict:
        return {"verdict": self.verdict,
                "findings": [f.as_dict() for f in self.findings],
                "next_information": list(self.next_information)}

    @property
    def certified(self) -> bool:
        return self.verdict == CERTIFIED


def _norm(value: Any):
    """Case-fold and trim a vocabulary token; None if not a usable string."""
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


class _Ctx:
    """Accumulates findings and the two escalation flags."""

    def __init__(self):
        self.findings: list[Finding] = []
        self.next_info: list[str] = []
        self.block = False
        self.defer = False

    def add(self, code: str, message: str, severity: str = "critical"):
        self.findings.append(Finding(code, message, severity))

    def need(self, item: str):
        if item:
            self.next_info.append(item)

    def escalate(self, effect: str, code: str, message: str, severity: str = "critical"):
        """Apply a check's declared certificate_effect."""
        if effect == "block":
            self.block = True
        else:
            self.defer = True
        self.add(code, message, severity)


def _check_vocab(item: Mapping, label: str, kind: str, ctx: _Ctx) -> str:
    """Resolve a check's certificate_effect, FAILING CLOSED.

    Missing or unrecognized `certificate_effect` -> 'block' (the strictest
    interpretation) plus a finding. Missing or unrecognized `severity` -> a
    finding, but severity never changes the verdict. Returns the effect to apply.
    """
    sev = _norm(item.get("severity"))
    if sev not in SEVERITIES:
        # Severity is metadata, not the verdict axis — so this DEFERS rather than
        # blocking. But it must escalate: an unreadable declaration means we cannot
        # confirm the check was authored correctly, and "reported but ignored" was a
        # fail-open (P0-2). A genuinely present/failed check still BLOCKs via
        # certificate_effect, independently of this.
        ctx.defer = True
        ctx.add("UNRECOGNIZED_SEVERITY",
                f"{kind} {label!r}: severity {item.get('severity')!r} not in "
                f"{list(SEVERITIES)}; cannot confirm the check is correctly declared.")
        ctx.need(f"a valid severity for {label}")

    eff = _norm(item.get("certificate_effect"))
    if eff not in EFFECTS:
        ctx.add("UNRECOGNIZED_CERTIFICATE_EFFECT",
                f"{kind} {label!r}: certificate_effect {item.get('certificate_effect')!r} "
                f"not in {list(EFFECTS)}; failing closed to 'block'.")
        return "block"
    return eff


def _label(item: Mapping, default: str) -> str:
    return str(item.get("label") or item.get("id") or default)


def verify_certificate(certificate: Mapping[str, Any]) -> VerificationResult:
    """Compute a fail-closed verdict. Never raises on malformed input; malformed
    input produces BLOCK or DEFER with explanatory findings (I1, I2)."""
    ctx = _Ctx()

    if not isinstance(certificate, Mapping):
        return VerificationResult(
            BLOCK, (Finding("MALFORMED_CERTIFICATE", "Certificate is not a mapping.", "critical"),), ())

    # ---- patient snapshot ----
    # ---- schema contract: every required key must be PRESENT (P0-1) ----
    for key in REQUIRED_TOP_LEVEL:
        if key not in certificate:
            ctx.defer = True
            ctx.add("MISSING_REQUIRED_FIELD",
                    f"Certificate omits required field {key!r}. An absent checklist is not an "
                    f"empty checklist: it cannot be distinguished from one that was never run.")
            ctx.need(f"a populated {key!r} field")
    cid = certificate.get("certificate_id")
    # must be a real non-empty STRING: str(True) is "True", which is truthy, so a
    # boolean would have passed a naive truthiness check.
    if not isinstance(cid, str) or not cid.strip():
        ctx.defer = True
        ctx.add("MISSING_CERTIFICATE_ID",
                f"certificate_id must be a non-empty string, got {type(cid).__name__} {cid!r}; "
                f"without it the certificate cannot be replayed or audited.")
        ctx.need("a valid certificate_id")

    patient = certificate.get("patient_snapshot")
    if not isinstance(patient, Mapping) or not patient.get("captured_at"):
        ctx.defer = True
        ctx.add("PATIENT_SNAPSHOT_UNPINNED", "Patient snapshot missing or lacks a capture time.")
        ctx.need("a time-stamped patient snapshot")

    # ---- evidence bundle ----
    evidence = certificate.get("evidence_bundle")
    if not isinstance(evidence, list) or not evidence:
        ctx.defer = True
        ctx.add("NO_EVIDENCE_BUNDLE", "No version-pinned evidence bundle was supplied.")
        ctx.need("a version-pinned evidence bundle")
    else:
        for source in evidence:
            if not isinstance(source, Mapping) or not source.get("id") or not source.get("version"):
                ctx.defer = True
                ctx.add("UNPINNED_SOURCE", "An evidence source lacks an identifier or version.")
                ctx.need("an identifier and version for every evidence source")
                continue
            if _norm(source.get("status")) not in SOURCE_STATUSES_OK:
                ctx.defer = True
                ctx.add("INACTIVE_SOURCE", f"Evidence source {source.get('id')!r} is not active/valid.")
                ctx.need(f"a current version of evidence source {source.get('id')}")

    # ---- proposed action ----
    action = certificate.get("action")
    if not isinstance(action, Mapping) or not action.get("code"):
        ctx.block = True
        ctx.add("MALFORMED_ACTION", "The proposed action has no code.")

    # ---- support ----
    supports = certificate.get("support")
    if not isinstance(supports, list):
        ctx.defer = True
        ctx.add("MALFORMED_SUPPORT", "support is not a list.")
        supports = []
    usable = []
    for s in supports:
        if not isinstance(s, Mapping):
            ctx.defer = True
            ctx.add("MALFORMED_SUPPORT_ENTRY", "A support entry is not a mapping.")
            continue
        usable.append(s)
    applicable = [s for s in usable if s.get("applicable") is True]
    if not applicable:
        ctx.defer = True
        ctx.add("NO_APPLICABLE_SUPPORT", "No applicable rule licenses the proposed action.")
        ctx.need("an applicable rule that licenses this action")
    for s in applicable:
        rid = s.get("rule_id", "?")
        if s.get("conflict") is True:
            ctx.block = True
            ctx.add("SUPPORT_RULE_CONFLICT", f"Rule {rid} conflicts with the action.")
        prov = s.get("provenance")
        if not isinstance(prov, list) or not prov:
            ctx.defer = True
            ctx.add("SUPPORT_WITHOUT_PROVENANCE", f"Rule {rid} lacks provenance.")
            ctx.need(f"provenance for rule {rid}")

    _verify_checks(certificate, "critical_questions", CQ_STATUSES,
                   fail_status="fail", ok_statuses=("pass", "not_applicable"), ctx=ctx)
    _verify_checks(certificate, "contraindications", CONTRA_STATUSES,
                   fail_status="present", ok_statuses=("absent", "not_applicable"), ctx=ctx)

    verdict = BLOCK if ctx.block else DEFER if ctx.defer else CERTIFIED

    claimed = certificate.get("claimed_verdict")
    if claimed is not None and claimed != verdict:
        ctx.add("CLAIMED_VERDICT_MISMATCH", f"Claimed {claimed}, computed {verdict}.", "warning")

    # I4: a DEFER that cannot say what it needs is an unexplained refusal.
    if verdict == DEFER and not ctx.next_info:
        ctx.need("unspecified — see findings")

    return VerificationResult(verdict, tuple(ctx.findings), tuple(dict.fromkeys(ctx.next_info)))


def _verify_checks(certificate: Mapping, key: str, vocab: tuple,
                   fail_status: str, ok_statuses: tuple, ctx: _Ctx) -> None:
    """Verify one list of checks. NOTHING here may skip on severity (I5)."""
    kind = key[:-1].replace("_", " ")
    items = certificate.get(key)
    if not isinstance(items, list):
        ctx.defer = True
        ctx.add(f"MALFORMED_{key.upper()}", f"{key} is not a list.")
        return

    seen_ids = set()
    for item in items:
        if not isinstance(item, Mapping):
            ctx.defer = True
            ctx.add("MALFORMED_CHECK", f"A {kind} entry is not a mapping.")
            continue
        label = _label(item, kind)

        ident = item.get("id")
        if ident is not None:
            if ident in seen_ids:
                ctx.defer = True
                ctx.add("DUPLICATE_CHECK_ID",
                        f"{kind} id {ident!r} appears more than once; duplicates can mask a "
                        f"conflicting verdict for the same check.")
            seen_ids.add(ident)

        effect = _check_vocab(item, label, kind, ctx)     # never skips
        status = _norm(item.get("status"))

        if status == fail_status:
            ctx.escalate(effect, f"{key.upper()}_FAILED" if key == "critical_questions"
                         else "CONTRAINDICATION_PRESENT", label)
        elif status == "unknown":
            ctx.defer = True
            ctx.add(f"{key.upper()}_UNKNOWN", label)
            ctx.need(label)
        elif status not in ok_statuses:
            ctx.defer = True
            ctx.add(f"{key.upper()}_INVALID_STATUS",
                    f"{label}: status {item.get('status')!r} not in {list(vocab)}")
            ctx.need(label)

        if status != "not_applicable":
            prov = item.get("provenance")
            if not isinstance(prov, list) or not prov:
                ctx.defer = True
                ctx.add(f"{key.upper()}_WITHOUT_PROVENANCE", label)
                ctx.need(f"provenance for {label}")
