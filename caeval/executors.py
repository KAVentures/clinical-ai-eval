"""Family execution backends.

A family declares WHAT to probe; an executor knows HOW to run it. Without this
split the CLI has one hard-coded pipeline — load dict cases, apply a text
transform, call a one-shot subject — and any family that does not fit it becomes a
parallel subsystem the product workflow cannot reach. That is exactly what
happened to the patient and RAG families: implemented, declared implemented,
selectable, and unreachable from `run --project`.

The registry is fail-closed in both directions, and both directions have bitten:

  * a family with no executor cannot be selected (it would be planned and then
    crash, or worse, be run by a pipeline that silently mis-scores it);
  * an executor with no family is a configuration error, not a default.

`resolve()` raises rather than falling back to the generic executor. A generic
fallback is how a multi-turn patient family gets scored as a one-shot answer and
still produces a confident-looking report.
"""
from __future__ import annotations

from dataclasses import dataclass

GENERIC_PAIRED_TEXT = "generic_paired_text"
PATIENT_EPISODE = "patient_episode"
RAG_TRACE = "rag_trace"

# family_id -> executor. The single source of truth; docs and selection derive
# from it rather than restating it.
# `citation_verification` is deliberately ABSENT: its three declared conditions all
# collapsed to one retrieval perturbation and its central construct needs a judge
# verdict that is not wired. Registering an executor for it would make it
# selectable and produce three relabelled copies of one probe.
FAMILY_EXECUTORS = {
    "missing_information": GENERIC_PAIRED_TEXT,
    "conflicting_evidence": GENERIC_PAIRED_TEXT,
    "patient_red_flag": PATIENT_EPISODE,
    "retrieval_failure": RAG_TRACE,
}

# What each executor needs a case pack to BE. A patient family handed clinician
# vignettes would run and produce numbers that mean nothing.
EXECUTOR_PACK_KIND = {
    GENERIC_PAIRED_TEXT: "clinician_vignette",
    PATIENT_EPISODE: "patient_worlds",
    RAG_TRACE: "rag_corpus_bound",
}

EXECUTOR_SUBJECT_MODALITY = {
    GENERIC_PAIRED_TEXT: "single_turn",
    PATIENT_EPISODE: "conversation",
    RAG_TRACE: "single_turn",
}


class ExecutorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutorSpec:
    executor_id: str
    pack_kind: str
    subject_modality: str
    description: str


SPECS = {
    GENERIC_PAIRED_TEXT: ExecutorSpec(
        GENERIC_PAIRED_TEXT, "clinician_vignette", "single_turn",
        "paired original/perturbed one-shot text; the original clinician-facing path"),
    PATIENT_EPISODE: ExecutorSpec(
        PATIENT_EPISODE, "patient_worlds", "conversation",
        "multi-turn episodes over hidden clinical worlds with disclosure policy, "
        "determinacy tracking and a patient-specific judge contract"),
    RAG_TRACE: ExecutorSpec(
        RAG_TRACE, "rag_corpus_bound", "single_turn",
        "corpus-bound retrieval trace; retrieval and generation scored separately"),
}


def resolve(family_id: str) -> ExecutorSpec:
    """Which backend runs this family. Raises rather than defaulting."""
    ex = FAMILY_EXECUTORS.get(family_id)
    if ex is None:
        raise ExecutorError(
            f"family {family_id!r} has no registered executor, so this build cannot run "
            f"it. Refusing to fall back to {GENERIC_PAIRED_TEXT!r}: running a family "
            f"through the wrong backend produces a report that looks complete and "
            f"measures something else. Register it in caeval/executors.py.")
    return SPECS[ex]


def executor_for(family_id: str) -> str:
    return resolve(family_id).executor_id


def has_executor(family_id: str) -> bool:
    return family_id in FAMILY_EXECUTORS


def check_pack_compatibility(family_id: str, pack_kind: str) -> None:
    """A pack of the wrong shape must stop the run, not be coerced."""
    spec = resolve(family_id)
    if pack_kind != spec.pack_kind:
        raise ExecutorError(
            f"family {family_id!r} runs on the {spec.executor_id!r} backend and needs a "
            f"{spec.pack_kind!r} case pack, but the project supplied {pack_kind!r}. "
            f"Running it anyway would produce numbers that do not describe the product.")


def check_subject_compatibility(family_id: str, modality: str) -> None:
    spec = resolve(family_id)
    if modality != spec.subject_modality:
        raise ExecutorError(
            f"family {family_id!r} needs a {spec.subject_modality!r} subject, but the "
            f"configured target is {modality!r}. A multi-turn family cannot be measured "
            f"through a one-shot adapter: see caeval/adapters.py for the conversation "
            f"adapter and probe.")


def inventory() -> list:
    """Registry state, for generated documentation and the consistency guard."""
    return [{"family_id": f, "executor": e,
             "pack_kind": SPECS[e].pack_kind,
             "subject_modality": SPECS[e].subject_modality}
            for f, e in sorted(FAMILY_EXECUTORS.items())]
