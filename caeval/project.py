"""User projects — the real intended-use intake that replaces demo-driven planning.

Until v0.7 the CLI's `plan`, `inspect` and `init` all read a hardcoded
`DEMO_TARGET_META`, so a new user could "plan an assessment" that silently
described the demo product rather than theirs. That is the single blocker to
self-service: everything downstream (suite selection, audience gate, hazards,
report scope) derives from the intake, so a demo intake produces a confidently
wrong evaluation plan.

A PROJECT is a versioned directory the user owns:

    <project>/
      project.yaml        intended use + subject connector + panel choice
      out/                run workspaces and evidence packages

`project.yaml` is validated BEFORE anything runs. Validation is fail-closed and
answers-aware: an unanswered mandatory intake question blocks planning rather than
defaulting, because "we didn't ask" and "the answer is no" are different and only
one of them is safe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .intake import INTAKE_QUESTIONS, TARGET_PROFILES

PROJECT_FILE = "project.yaml"
SCHEMA_VERSION = "1.0"

# Answers that mean "not answered". Treated as MISSING, never as a default.
_UNANSWERED_TOKENS = {"", "todo", "(not provided)", "fill me", "?", "n/a", "none", "tbd"}


def _is_unanswered(value) -> bool:
    """True when a field is not genuinely answered.

    Must inspect the RAW value first: str(None) is "None" and str(False) is
    "False", both of which look like answers to a naive string check. This is the
    same coercion trap that let certificate_id=True pass in v0.6 (CORRECTIONS.md).
    """
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return str(value).strip().lower() in _UNANSWERED_TOKENS

# Intake questions whose answer changes the evaluation plan. All are mandatory.
MANDATORY_INTAKE = list(INTAKE_QUESTIONS)

# Additional decision-relevant questions the product asks a clinical-safety lead.
MANDATORY_GOVERNANCE = [
    "influences_diagnosis_or_treatment",   # yes | no
    "clinician_reviews_every_response",    # yes | no
    "regulated_medical_device",            # yes | no | unsure
    "deployment_jurisdictions",            # free text, e.g. "SE, EU"
]

VALID_MODES = ("demonstration", "internal_regression", "calibrated_assessment",
               "procurement_comparison", "surveillance")

# What each mode is allowed to say. Enforced at report time (see maturity.py for
# the family-level gate; this is the RUN-level counterpart).
MODE_LABELS = {
    "demonstration":          "DEMONSTRATION — NOT CLINICAL EVIDENCE",
    "internal_regression":    "INTERNAL REGRESSION SCREEN",
    "calibrated_assessment":  "CALIBRATED ASSESSMENT WITHIN THE STATED SCOPE",
    "procurement_comparison": "COMPARATIVE PROCUREMENT EVIDENCE — NOT REGULATORY CERTIFICATION",
    "surveillance":           "POST-DEPLOYMENT SURVEILLANCE SCREEN",
}


class ProjectError(RuntimeError):
    """The project definition is missing, invalid, or incomplete."""


@dataclass
class Project:
    path: Path
    data: dict = field(default_factory=dict)

    # ---------- identity ----------
    @property
    def name(self) -> str:
        return str(self.data.get("project", {}).get("name", "")) or self.path.name

    @property
    def mode(self) -> str:
        return str(self.data.get("project", {}).get("mode", "demonstration"))

    @property
    def profiles(self) -> list:
        return list(self.data.get("target_profile", {}).get("types", []))

    @property
    def subject(self) -> dict:
        return dict(self.data.get("subject", {}))

    @property
    def target_meta(self) -> dict:
        """The shape `intake.build_eval_plan` expects — derived from the USER's
        project, never from a demo constant."""
        t = self.data.get("target", {})
        meta = {"name": t.get("name", self.name),
                "version": t.get("version", "unspecified"),
                "endpoint": self.subject.get("url", self.subject.get("kind", "(unspecified)")),
                "profiles": self.profiles,
                "input_modalities": self.data.get("target_profile", {}).get("input_modalities", ["free_text"]),
                "output_actions": self.data.get("target_profile", {}).get("output_actions", [])}
        meta.update({q: self.data.get("intake", {}).get(q, "") for q in MANDATORY_INTAKE})
        return meta

    # ---------- validation ----------
    def validate(self) -> list:
        """Return a list of blocking problems. Empty list == usable."""
        problems = []
        d = self.data

        if str(d.get("schema_version", "")) != SCHEMA_VERSION:
            problems.append(f"schema_version must be {SCHEMA_VERSION!r} "
                            f"(got {d.get('schema_version')!r})")

        if not d.get("project", {}).get("name"):
            problems.append("project.name is required")

        if self.mode not in VALID_MODES:
            problems.append(f"project.mode {self.mode!r} not in {list(VALID_MODES)}")

        # --- target profile ---
        if not self.profiles:
            problems.append("target_profile.types is required — the evaluation plan is "
                            "derived from it (§2); nothing can be selected without it")
        for p in self.profiles:
            if p not in TARGET_PROFILES:
                problems.append(f"unknown target profile {p!r}; known: {sorted(TARGET_PROFILES)}")

        # --- intake: unanswered is NOT a default ---
        intake = d.get("intake", {}) or {}
        for q in MANDATORY_INTAKE:
            if _is_unanswered(intake.get(q)):
                problems.append(f"intake.{q} is unanswered — an unanswered intake question "
                                f"blocks planning; it is not the same as answering 'no'")
        gov = d.get("governance", {}) or {}
        for q in MANDATORY_GOVERNANCE:
            if _is_unanswered(gov.get(q)):
                problems.append(f"governance.{q} is unanswered")

        # --- subject connector ---
        s = self.subject
        kind = s.get("kind")
        if not kind:
            problems.append("subject.kind is required (mock|openai|anthropic|xai|google|http|manual)")
        cp = self.data.get("case_pack", {}) or {}
        if not (cp.get("source") or any(
                isinstance(v, dict) and v.get("source") for v in cp.values())):
            problems.append(
                "case_pack.source is required — a run against unspecified cases cannot "
                "describe your product. Use builtin:demo_clinician / builtin:public_smoke "
                "for a demonstration, or point at a directory you author.")
        elif kind == "http" and not s.get("url"):
            problems.append("subject.kind=http requires subject.url")
        elif kind in ("openai", "anthropic", "xai", "google") and not s.get("model"):
            problems.append(f"subject.kind={kind} requires subject.model")
        elif kind == "manual" and not s.get("responses_file"):
            problems.append("subject.kind=manual requires subject.responses_file")

        # --- mode/subject coherence: the guard that stops a demo masquerading ---
        if self.mode != "demonstration" and kind == "mock":
            problems.append(f"mode {self.mode!r} uses subject.kind='mock'. A mock subject "
                            f"produces synthetic fixtures and can only support "
                            f"mode='demonstration'.")
        if self.mode in ("calibrated_assessment", "procurement_comparison"):
            # The error text promised ">=2 named clinicians" while the check only
            # tested for a non-empty list, so ONE reviewer satisfied it and the
            # message described a gate that was not enforced.
            reviewers = [str(r).strip() for r in
                         (d.get("clinical_review", {}).get("reviewers") or []) if str(r).strip()]
            if len(reviewers) < 2:
                problems.append(
                    f"mode {self.mode!r} requires at least TWO named clinicians in "
                    f"clinical_review.reviewers (found {len(reviewers)}); a single "
                    f"reviewer yields no inter-rater agreement, so the run cannot carry "
                    f"a calibrated or procurement claim")
            if len(set(reviewers)) != len(reviewers):
                problems.append(
                    "clinical_review.reviewers contains duplicates: the same person "
                    "listed twice is one reviewer, not two")
            review_cfg = d.get("clinical_review", {}) or {}
            resolution_mode = str(review_cfg.get("resolution_mode", "consensus")).strip().lower()
            if resolution_mode not in {"consensus", "third_reviewer"}:
                problems.append(
                    "clinical_review.resolution_mode must be 'consensus' or 'third_reviewer'")
            tie = str(review_cfg.get("tie_reviewer", "")).strip()
            if resolution_mode == "third_reviewer":
                if not tie:
                    problems.append(
                        f"mode {self.mode!r} with resolution_mode='third_reviewer' requires "
                        "clinical_review.tie_reviewer")
                elif tie in reviewers:
                    problems.append(
                        "clinical_review.tie_reviewer is also a primary reviewer; someone "
                        "cannot independently adjudicate a tie they are party to")

        # --- product identity: evidence must bind to a specific tested product ---
        target = d.get("target", {}) or {}
        for f in ("name", "version"):
            if not str(target.get(f, "")).strip():
                problems.append(
                    f"target.{f} is required: an evidence package that does not name the "
                    f"exact product and version it tested cannot be bound to anything")
        if self.mode != "demonstration" and not str(target.get("vendor", "")).strip():
            problems.append(
                f"target.vendor is required for mode {self.mode!r}: a comparative or "
                f"calibrated claim must identify whose product was tested")
        return problems

    def require_valid(self) -> None:
        problems = self.validate()
        if problems:
            raise ProjectError(
                f"project at {self.path} is not usable:\n  - " + "\n  - ".join(problems))

    def claim_label(self) -> str:
        return MODE_LABELS.get(self.mode, MODE_LABELS["demonstration"])


# --------------------------------------------------------------------------
def load(path: str | Path) -> Project:
    p = Path(path)
    f = p / PROJECT_FILE if p.is_dir() else p
    if not f.exists():
        raise ProjectError(
            f"no {PROJECT_FILE} at {f}. Create one with:  clinical-ai-eval project init <dir>")
    data = yaml.safe_load(f.read_text()) or {}
    if not isinstance(data, dict):
        raise ProjectError(f"{f} is not a YAML mapping")
    return Project(path=f.parent, data=data)


def template(name: str = "my-clinical-ai", mode: str = "demonstration") -> dict:
    """A project skeleton whose intake answers are deliberately EMPTY.

    They are not prefilled with plausible defaults: a prefilled intake is how a
    user ends up with an authoritative-looking plan they never actually answered.
    `target validate` names every unanswered question.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {"name": name, "mode": mode,
                    "_mode_options": list(VALID_MODES),
                    "_claim_label": MODE_LABELS.get(mode)},
        "target": {"name": "", "version": "", "vendor": ""},
        "target_profile": {
            "types": [],
            "_options": sorted(TARGET_PROFILES),
            "input_modalities": ["free_text"],
            "output_actions": [],
        },
        "intake": {q: "" for q in MANDATORY_INTAKE},
        "governance": {
            "influences_diagnosis_or_treatment": "",
            "clinician_reviews_every_response": "",
            "regulated_medical_device": "",
            "deployment_jurisdictions": "",
        },
        "subject": {
            "kind": "mock",
            "_kind_options": ["mock", "openai", "anthropic", "xai", "google", "http", "manual"],
            "arm": "flawed",
            "_http_example": {"kind": "http", "url": "https://your-product/answer",
                              "prompt_field": "question", "answer_path": "data.text",
                              "headers": {"Authorization": "Bearer ${YOUR_ENV_VAR}"},
                              "timeout": 120},
        },
        # Set when this run is a vendor submission to a frozen procurement. The
        # hash is issued by `procurement init` and travels into the evidence
        # package, so ingestion can check the run was produced under the same
        # conditions as every other vendor's.
        "procurement": {"conditions_hash": ""},
        "panel": {"config": "configs/judge_panel.toml"},
        "clinical_review": {
            "reviewers": [],
            "resolution_mode": "consensus",
            "tie_reviewer": "",
        },
        # The pack the run actually executes. Until v0.16 project-bound runs used
        # built-in demo vignettes regardless of what the project said, so an
        # evidence package described an assessment of fixtures rather than of the
        # user's cases. `expected_kind` must match the selected family's executor.
        "case_pack": {
            # A bare `source` applies to every selected family. If your product
            # selects families needing different pack kinds (a clinician-RAG
            # product does), key them by kind instead:
            #   case_pack:
            #     clinician_vignette: {source: ./packs/vignettes}
            #     rag_corpus_bound:   {source: ./packs/corpus}
            "source": "builtin:demo_clinician",
            "_options": ["builtin:demo_clinician  (clinician_vignette, demonstration only)",
                         "builtin:public_smoke    (patient_worlds, demonstration only)",
                         "./packs/my-pack         (a directory you author)"],
            "pack_id": "",
            "version": "",
            "expected_kind": "",
            "_kind_options": ["clinician_vignette", "patient_worlds", "rag_corpus_bound"],
        },
    }


def write_template(directory: str | Path, name: str, mode: str = "demonstration") -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    f = d / PROJECT_FILE
    if f.exists():
        raise ProjectError(f"{f} already exists; refusing to overwrite")
    f.write_text(yaml.safe_dump(template(name, mode), sort_keys=False, default_flow_style=False))
    (d / "out").mkdir(exist_ok=True)
    return f
