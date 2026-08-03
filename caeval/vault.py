"""Private evaluation vault — narrow interface over hidden case state.

The public engine never sees hidden cases directly. It holds opaque case
references and asks the vault for exactly the payload a given consumer is
entitled to. That is what makes blinding structural rather than a convention:

    consumer                 sees
    ----------------------   --------------------------------------------------
    evaluated system         ONLY the clinician/patient-facing input
    blinded judge            case-as-shown + product response
    rubric-aware judge       + the defect specification
    blinded adjudicator      case-as-shown + product response (never arm/defect)
    analysis                 labels — ONLY after the run is locked

Backends: `DirectoryVault` (a separate private repo or encrypted local dir) is
the first implementation. Hospital-grade key management is explicitly out of
scope; the point is that the boundary EXISTS and is enforced in one place.

FAIL CLOSED: an unknown role, an unauthorized payload request, or a label reveal
before the analysis lock raises. There is no "just this once" path.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .util import stable_hash_text, utc_now_iso

# Who may ask for what. The evaluated system is deliberately the most restricted.
ROLE_ENTITLEMENTS = {
    "evaluated_system":    {"subject"},
    "blinded_judge":       {"subject", "response"},
    "rubric_aware_judge":  {"subject", "response", "rubric"},
    "blinded_adjudicator": {"subject", "response"},
    "defect_implementer":  {"subject", "rubric", "defect"},   # builds them; never adjudicates
    "analysis":            {"subject", "response", "rubric", "defect", "labels"},
    "harness_maintainer":  {"metadata"},                      # structure only, no case content
}


class VaultError(RuntimeError):
    """Unauthorized access, missing vault, or premature label reveal."""


@dataclass(frozen=True)
class CaseRef:
    """An OPAQUE handle. Carries no clinical content and no defect status."""
    case_id: str
    suite_id: str
    content_hash: str

    def __repr__(self) -> str:  # never print content
        return f"CaseRef({self.suite_id}/{self.case_id}@{self.content_hash[:8]})"


class Vault:
    """Interface. Any backend implements these five methods."""

    def list_suite_metadata(self) -> list[dict]: raise NotImplementedError
    def materialize_run(self, run_id: str, authorized_role: str) -> list[CaseRef]: raise NotImplementedError
    def get_subject_payload(self, case_id: str) -> dict: raise NotImplementedError
    def get_rubric_payload(self, case_id: str, evaluator_mode: str) -> dict: raise NotImplementedError
    def reveal_labels(self, run_id: str, after_analysis_lock: bool = False) -> dict: raise NotImplementedError


class DirectoryVault(Vault):
    """Filesystem-backed vault. Point CAEVAL_VAULT at a SEPARATE private repo or an
    encrypted directory — never a gitignored subdirectory of the public repo (too
    easy to lose, commit by accident, or read from the wrong process).

    Layout:
        <vault>/suites/<suite_id>/suite.json         suite metadata (non-secret)
        <vault>/suites/<suite_id>/cases/<case_id>.json   case + defect + labels
        <vault>/runs/<run_id>.json                   run manifest + lock state
    """

    def __init__(self, root: str | os.PathLike | None = None):
        root = root or os.environ.get("CAEVAL_VAULT")
        if not root:
            raise VaultError(
                "no vault configured. Set CAEVAL_VAULT to a PRIVATE directory "
                "(separate repo or encrypted volume), not a path inside the public repo.")
        self.root = Path(root)
        if not self.root.exists():
            raise VaultError(f"vault path does not exist: {self.root}")

    # ---------------- structure ----------------
    def _suite_dir(self, suite_id: str) -> Path:
        return self.root / "suites" / suite_id

    def _case_path(self, case_id: str) -> Path:
        for sdir in (self.root / "suites").glob("*"):
            p = sdir / "cases" / f"{case_id}.json"
            if p.exists():
                return p
        raise VaultError(f"case {case_id!r} not in vault")

    def _load_case(self, case_id: str) -> dict:
        return json.loads(self._case_path(case_id).read_text())

    def _run_path(self, run_id: str) -> Path:
        return self.root / "runs" / f"{run_id}.json"

    # ---------------- the five methods ----------------
    def list_suite_metadata(self) -> list[dict]:
        """Non-secret metadata only: counts, hazards covered, lock state. Never case text."""
        out = []
        for sdir in sorted((self.root / "suites").glob("*")):
            meta_p = sdir / "suite.json"
            if not meta_p.exists():
                continue
            meta = json.loads(meta_p.read_text())
            cases = list((sdir / "cases").glob("*.json"))
            out.append({
                "suite_id": meta.get("suite_id", sdir.name),
                "purpose": meta.get("purpose", ""),
                "family_id": meta.get("family_id"),
                "n_cases": len(cases),
                "hazards_covered": meta.get("hazards_covered", []),
                "locked": bool(meta.get("locked", False)),
                "created_at": meta.get("created_at"),
            })
        return out

    def materialize_run(self, run_id: str, authorized_role: str) -> list[CaseRef]:
        """Return OPAQUE refs for a run. No clinical content, no defect status."""
        if authorized_role not in ROLE_ENTITLEMENTS:
            raise VaultError(f"unknown role {authorized_role!r}; expected one of {sorted(ROLE_ENTITLEMENTS)}")
        rp = self._run_path(run_id)
        if not rp.exists():
            raise VaultError(f"no run manifest {run_id!r} in vault")
        run = json.loads(rp.read_text())
        refs = []
        for case_id in run["case_ids"]:
            case = self._load_case(case_id)
            refs.append(CaseRef(case_id=case_id, suite_id=case.get("suite_id", ""),
                                content_hash=stable_hash_text(case.get("input_text", ""))))
        return refs

    def get_subject_payload(self, case_id: str) -> dict:
        """What the EVALUATED SYSTEM receives: the facing input and nothing else."""
        case = self._load_case(case_id)
        return {"case_id": case_id, "input_text": case["input_text"]}

    def get_rubric_payload(self, case_id: str, evaluator_mode: str) -> dict:
        """What an EVALUATOR receives, by mode.

        blinded      -> case-as-shown only (must infer what is missing)
        rubric_aware -> + the defect specification (a defect DETECTOR, not a
                        clinical-quality estimate; excluded from the headline)
        """
        if evaluator_mode not in ("blinded", "rubric_aware"):
            raise VaultError(f"unknown evaluator mode {evaluator_mode!r}")
        case = self._load_case(case_id)
        payload = {"case_id": case_id, "input_text": case["input_text"]}
        if evaluator_mode == "rubric_aware":
            payload["defect_specification"] = {
                "hazard_id": case.get("hazard", {}).get("hazard_id"),
                "defect_class": case.get("defect", {}).get("defect_class"),
                "expected_missing_evidence": case.get("expected_missing_evidence", ""),
                "unacceptable_behaviors": case.get("unacceptable_behaviors", []),
            }
        return payload

    def reveal_labels(self, run_id: str, after_analysis_lock: bool = False) -> dict:
        """Ground-truth defect labels. REFUSED until the analysis is locked, so the
        analysis cannot be tuned after seeing which arms carry defects."""
        rp = self._run_path(run_id)
        if not rp.exists():
            raise VaultError(f"no run manifest {run_id!r} in vault")
        run = json.loads(rp.read_text())
        if not after_analysis_lock:
            raise VaultError(
                f"refusing to reveal labels for run {run_id!r}: caller did not assert the "
                f"analysis lock. Labels are revealed ONLY after the analysis plan is locked.")
        if not run.get("analysis_locked"):
            raise VaultError(
                f"run {run_id!r} is not analysis-locked (analysis_locked=false). Lock the "
                f"preregistered analysis plan before revealing defect labels.")
        labels = {}
        for case_id in run["case_ids"]:
            case = self._load_case(case_id)
            labels[case_id] = {
                "defect_status": case.get("defect", {}).get("defect_status", "none"),
                "defect_class": case.get("defect", {}).get("defect_class"),
                "hazard_id": case.get("hazard", {}).get("hazard_id"),
                "implementation_author": case.get("defect", {}).get("implementation_author"),
            }
        return {"run_id": run_id, "revealed_at": utc_now_iso(),
                "analysis_lock_hash": run.get("analysis_lock_hash"), "labels": labels}


# --------------------------------------------------------------------------
def authorize(role: str, payload_kind: str) -> None:
    """Central entitlement check. Fail closed on anything not explicitly allowed."""
    if role not in ROLE_ENTITLEMENTS:
        raise VaultError(f"unknown role {role!r}")
    if payload_kind not in ROLE_ENTITLEMENTS[role]:
        raise VaultError(
            f"role '{role}' is not entitled to '{payload_kind}' "
            f"(entitled to: {sorted(ROLE_ENTITLEMENTS[role])}). This boundary is what makes "
            f"blinding structural; there is no override.")


def lock_hash(payload: dict) -> str:
    """Content hash of a preregistered analysis plan — the thing a label reveal is
    checked against, so 'we locked it' is verifiable rather than asserted."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
