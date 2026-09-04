"""Test-family plugin SDK — schema-first, deliberately minimal.

The two shipped families were embedded in assumptions spread across selection,
validity, scoring, review and reporting. This SDK gives a family ONE declaration
and ONE runtime interface, so a new family (patient-facing, RAG, scribe) is an
additive plugin rather than a new special case.

Scope discipline: this is NOT a plugin ecosystem. There is no dynamic discovery,
no entry points, no dependency resolution. A family is a YAML declaration plus
(optionally) a Python subclass. `YamlFamily` implements the whole interface from
the declaration alone, which is what both shipped families need.

ACCEPTANCE TEST for this SDK: migrating `missing_information` and
`conflicting_evidence` through it must reproduce the generated fixture block
byte-for-byte (tests_unit/test_family_sdk.py).

A family declares `required_capabilities`; the runtime refuses to run one whose
capabilities are not provided by this build (fail closed) — that is how the SDK
represents future breadth without pretending unsupported modules work.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from . import checks, hazards as hazards_mod, maturity as maturity_mod, providers, review as review_mod
from .perturbations import apply_transform, manifest_row
from .preconstructed import build_manifest_row as build_preconstructed_manifest_row
from .score import BINARY_FIELDS, family_audience_support
from .util import repo_root
from .validity import audit_variant

# Capabilities THIS BUILD provides. A family requiring anything else fails closed.
PROVIDED_CAPABILITIES = {
    "text_single_turn",          # one-shot text case -> text response
    "section_removal",           # remove_labs / remove_imaging / remove_exam / minimal HPI
    "contradiction_injection",   # add_conflict
    "paired_original_variant",   # paired scoring against the unperturbed case
    "preconstructed_variant_import", # externally authored manifestations under framework contracts
    "binary_safety_fields",      # the BINARY_FIELDS scoring schema
    "certificate_verification",  # caeval/certificates/verifier.py (v0.6)
    "minimum_information_solver",# caeval/certificates/mmip.py (v0.6)
    "underdetermination_witness",# mmip.witness_of_underdetermination (v0.8)
    # --- v0.12 patient readiness substrate (caeval/patient/) ---------------
    # Providing a capability means the MACHINERY exists and is unit-tested. It does
    # NOT mean the resulting measurement is clinician-calibrated: every patient
    # family remains `experimental` and cannot carry a claim on this evidence.
    "multi_turn_dialogue",       # patient/session.py — deterministic simulated patient
    "red_flag_schema",           # patient/scoring.py — missed_red_flag, over_reassurance
    "history_acquisition",       # patient/world.py — asked_keys / fact ledger
    "escalation_grading",        # patient/world.py — five-level disposition taxonomy
    # --- v0.15 clinical RAG bundle (caeval/rag/) ---------------------------
    "retrieval_corpus",          # rag/corpus.py + rag/retriever.py — pinned and hashed
    "citation_resolution",       # rag/probes.py check_citations — existence + currency only
}

# Capabilities named by families we intend to support later but do NOT yet.
KNOWN_UNPROVIDED = {
    "source_transcripts": "scribe families need source transcripts",
    "multi_version_diff": "version regression needs two product versions",
    "rule_bundle": "no version-pinned executable clinical rule bundle exists",
    "provenance_chain": "no fact/rule provenance store or replay exists",
    "action_extraction": ("patient/extraction.py extracts TRIAGE dispositions, not "
                          "clinician-facing proposed actions; decision_certifiability "
                          "needs the latter"),
    "citation_support_adjudication": ("deciding whether a cited document SUPPORTS a "
                                     "claim is deferred by check_citations() as "
                                     "`unverified_support`; no judge or clinician "
                                     "verdict is wired, so `unsupported_claim_rate` "
                                     "cannot be computed"),
    "distinct_citation_probes": ("the three declared citation conditions currently "
                                 "collapse to one retrieval perturbation and differ "
                                 "only by label"),
    "critical_question_closure": "no clinician-authored critical-question sets exist",
}

REQUIRED_TOP_LEVEL = [
    "family_id", "version", "intended_uses", "audiences", "maturity", "hazards",
    "case_schema", "transformations", "validity_protocol", "evaluators", "metrics",
    "acceptance_criteria", "review_routing", "required_capabilities",
]


class FamilyDefinitionError(RuntimeError):
    """The family declaration is invalid or incomplete."""


class UnsupportedCapabilityError(RuntimeError):
    """The family requires a capability this build does not provide (fail closed)."""


class EvaluationFamily:
    """Runtime interface every family implements."""

    def __init__(self, definition: dict):
        self.d = definition

    # -- identity --
    @property
    def family_id(self) -> str:
        return self.d.get("family_id", "")

    @property
    def version(self) -> str:
        return str(self.d.get("version", "0"))

    # -- interface --
    def validate_definition(self) -> None: raise NotImplementedError
    def validate_case(self, case: dict) -> None: raise NotImplementedError
    def generate_variants(self, case: dict, seed: int = 0) -> list[dict]: raise NotImplementedError
    def ingest_preconstructed_variant(self, case: dict, variant: dict, require_reviewed: bool = False) -> dict: raise NotImplementedError
    def run_deterministic_checks(self, case: dict, response: str) -> dict: raise NotImplementedError
    def build_judge_input(self, record: dict, mode: str) -> str: raise NotImplementedError
    def calculate_metrics(self, results: dict) -> dict: raise NotImplementedError
    def evaluate_hazards(self, results: dict) -> dict: raise NotImplementedError
    def select_human_review(self, results: dict, cells: list[dict]) -> list[dict]: raise NotImplementedError


class YamlFamily(EvaluationFamily):
    """Declaration-driven family. Delegates to the canonical modules (§11) so a
    migrated family behaves EXACTLY as it did before the SDK existed."""

    # ---------------- definition ----------------
    def validate_definition(self) -> None:
        missing = [k for k in REQUIRED_TOP_LEVEL if k not in self.d]
        if missing:
            raise FamilyDefinitionError(
                f"family '{self.d.get('family_id','<unnamed>')}' is missing required "
                f"declaration key(s): {missing}")
        maturity_mod.family_maturity(self.d)          # raises on an unknown level
        # audience bars must be scorable where claimed supported
        for aud, s in family_audience_support(self.d).items():
            if s["supported"] and s["unscorable_fields"]:
                raise FamilyDefinitionError(
                    f"{self.family_id}/{aud} claims supported but names unscorable "
                    f"fields {s['unscorable_fields']}")
        # every hazard needs a predeclared criterion
        for hz in self.d.get("hazards", []):
            crit = hz.get("acceptance_criterion", {})
            for key in ("metric", "operator", "threshold"):
                if key not in crit:
                    raise FamilyDefinitionError(
                        f"{self.family_id}: hazard {hz.get('hazard_id')} missing "
                        f"acceptance_criterion.{key} (criteria must be PREDECLARED)")
        self.check_capabilities()

    def check_capabilities(self) -> None:
        required = set(self.d.get("required_capabilities", []))
        missing = sorted(required - PROVIDED_CAPABILITIES)
        if missing:
            why = "; ".join(f"{m}: {KNOWN_UNPROVIDED.get(m, 'not provided by this build')}"
                            for m in missing)
            raise UnsupportedCapabilityError(
                f"family '{self.family_id}' requires capability/ies {missing} that this "
                f"build does not provide -> refusing to run it. Reasons: {why}. "
                f"This is a fail-closed scope guard, not a bug: the family is a declared "
                f"design target whose machinery does not exist yet.")

    def supported(self) -> tuple[bool, str]:
        try:
            self.check_capabilities()
            return True, ""
        except UnsupportedCapabilityError as e:
            return False, str(e)

    # ---------------- cases ----------------
    def validate_case(self, case: dict) -> None:
        schema = self.d.get("case_schema", {})
        for field in schema.get("required_fields", []):
            if not str(case.get(field, "")).strip():
                raise FamilyDefinitionError(
                    f"{self.family_id}: case {case.get('item_id','<no id>')} missing "
                    f"required field '{field}'")

    # ---------------- variants ----------------
    def generate_variants(self, case: dict, seed: int = 0) -> list[dict]:
        self.check_capabilities()
        rows = []
        for test in self.d.get("tests", []):
            precondition = test.get("precondition")
            if precondition and not self._precondition_met(precondition, case):
                continue
            result = apply_transform(test["transform"], case["input_text"],
                                     str(case.get("ground_truth_label", "")))
            row = manifest_row(case, test["test_id"], result)
            if test.get("expected_missing_evidence"):
                row["expected_missing_evidence"] = test["expected_missing_evidence"]
            row["test_id"] = test["test_id"]
            row["transform"] = test["transform"]
            row["severity"] = test.get("severity", "moderate")
            rows.append(row)
        return rows

    def ingest_preconstructed_variant(
        self, case: dict, variant: dict, require_reviewed: bool = False
    ) -> dict:
        """Normalize an externally authored manifestation into this family.

        This is the supported qualification-study path for case-specific,
        clinician-reviewed variants. It deliberately does not call the family's
        built-in deterministic transform.
        """
        self.check_capabilities()
        self.validate_case(case)
        cfg = self.d.get("preconstructed_variants") or {}
        if cfg.get("supported") is not True:
            raise FamilyDefinitionError(
                f"{self.family_id}: preconstructed variants are not declared supported"
            )
        return build_preconstructed_manifest_row(
            self.family_id,
            case,
            variant,
            default_test_id=str(cfg.get("test_id") or f"preconstructed_{self.family_id}"),
            default_severity=str(cfg.get("severity") or "high"),
            require_reviewed=require_reviewed,
        )

    def _precondition_met(self, precondition: str, case: dict) -> bool:
        text = (case["input_text"] + " " + str(case.get("ground_truth_label", ""))).lower()
        p = precondition.lower()
        if "renal" in p:
            return any(s in text for s in ("renal dose adjustment", "requires renal",
                                           "renally-adjusted", "renal adjustment"))
        return True

    def audit_variant(self, row: dict, original_text: str):
        return audit_variant(row, original_text, self.d)

    # ---------------- evaluation ----------------
    def run_deterministic_checks(self, case: dict, response: str) -> dict:
        return checks.deterministic_checks(response, case.get("perturbation_type", "original"),
                                           case.get("expected_missing_evidence", ""))

    def build_judge_input(self, record: dict, mode: str) -> str:
        return providers.format_judge_user(record, mode)

    # ---------------- analysis ----------------
    def calculate_metrics(self, results: dict) -> dict:
        return results.get("dimensions", {})

    def evaluate_hazards(self, results: dict) -> dict:
        return hazards_mod.evaluate_hazards(results, self.d)

    def select_human_review(self, results: dict, cells: list[dict]) -> list[dict]:
        routing = self.d.get("review_routing", {})
        high_sev = results.get("dimensions", {}).get("high_severity_fields", [])
        validity = _ValidityView(results.get("validity", {}))
        conclusion_ids = set(results.get("validated_cell_ids", []))
        if routing.get("calibration_sample_size"):
            ids = sorted(conclusion_ids)
            k = int(routing["calibration_sample_size"])
            if len(ids) > k:
                step = len(ids) / k
                conclusion_ids = {ids[int(i * step)] for i in range(k)}
        return review_mod.select_for_review(cells, validity, high_sev, conclusion_ids)


class _ValidityView:
    def __init__(self, d): self._d = d
    def get(self, pid):
        v = self._d.get(pid)
        return None if v is None else type("VL", (), {"ambiguous": v["ambiguous"], "valid": v["valid"]})()


# --------------------------------------------------------------------------
def families_dir() -> Path:
    return repo_root() / "tests"


def load_family_definition(family_id: str) -> dict:
    path = families_dir() / family_id / "family.yaml"
    if not path.exists():
        raise FamilyDefinitionError(f"no family declaration at {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def load(family_id: str, validate: bool = True) -> YamlFamily:
    fam = YamlFamily(load_family_definition(family_id))
    if validate:
        fam.validate_definition()
    return fam


def list_families() -> list[str]:
    return sorted(p.name for p in families_dir().iterdir()
                  if p.is_dir() and (p / "family.yaml").exists())


def family_status() -> list[dict]:
    """Inspectable status of every declared family: maturity + capability support."""
    out = []
    for fid in list_families():
        d = load_family_definition(fid)
        fam = YamlFamily(d)
        ok, why = fam.supported()
        try:
            lvl = maturity_mod.family_maturity(d)
        except Exception:  # noqa: BLE001
            lvl = "unknown"
        out.append({"family_id": fid, "version": fam.version, "maturity": lvl,
                    "runnable": ok, "blocked_reason": why,
                    "required_capabilities": d.get("required_capabilities", []),
                    "intended_uses": d.get("intended_uses", [])})
    return out
