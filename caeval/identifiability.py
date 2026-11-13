"""Formal tools for clinical-safety label identifiability.

This module is intentionally dependency-free.  It implements the finite-world
version of the hidden-fact impossibility theorem documented in
``research/clinical_safety_nonidentifiability/MANUSCRIPT.md``.

The central distinction is between:

* the *observed case* exposed to a model or evaluator; and
* the complete clinical world, which may contain action-critical facts omitted
  from that case.

An unsafe/safe label is identifiable exactly when it is constant on every fiber
of the observation map.  No choice of judge model, panel size, voting rule, or
sampling temperature can recover a label that is not a function of the judges'
inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Dict, Hashable, Iterable, List, Mapping, Optional, Sequence, Tuple


UNKNOWN_TOKENS = frozenset({None, "UNKNOWN", "ABSENT", "UNOBSERVED"})


class IdentifiabilityError(ValueError):
    """Raised when a finite-world instance is malformed or too large."""


@dataclass(frozen=True)
class ClinicalWorld:
    """One complete clinical world compatible with an observed case.

    ``labels`` maps a proposed action identifier to ``0`` (not unsafe) or ``1``
    (unsafe).  The theorem is agnostic to how the label was established; in a
    real study that judgment must come from a validated rule bundle or clinician
    adjudication rather than this module.
    """

    world_id: str
    observation: Hashable
    facts: Mapping[str, Hashable]
    labels: Mapping[str, int]

    def unsafe_label(self, action: str) -> int:
        if action not in self.labels:
            raise IdentifiabilityError(
                f"world {self.world_id!r} has no label for action {action!r}"
            )
        value = self.labels[action]
        if value not in (0, 1):
            raise IdentifiabilityError(
                f"world {self.world_id!r}, action {action!r}: label must be 0 or 1"
            )
        return int(value)


@dataclass(frozen=True)
class Witness:
    """Two evaluator-indistinguishable worlds with opposite safety labels."""

    left_world_id: str
    right_world_id: str
    observation: Hashable
    action: str
    left_label: int
    right_label: int


@dataclass(frozen=True)
class Pair:
    """A dangerous pair that a sufficient question set must separate."""

    left: ClinicalWorld
    right: ClinicalWorld


def _as_world_list(worlds: Iterable[ClinicalWorld]) -> List[ClinicalWorld]:
    result = list(worlds)
    ids = [world.world_id for world in result]
    if len(ids) != len(set(ids)):
        raise IdentifiabilityError("world_id values must be unique")
    return result


def observation_fibers(
    worlds: Iterable[ClinicalWorld],
) -> Dict[Hashable, List[ClinicalWorld]]:
    """Group complete worlds by the observed case exposed to the evaluator."""

    fibers: Dict[Hashable, List[ClinicalWorld]] = {}
    for world in _as_world_list(worlds):
        fibers.setdefault(world.observation, []).append(world)
    return fibers


def dangerous_pairs(worlds: Iterable[ClinicalWorld], action: str) -> List[Pair]:
    """Return same-observation world pairs with opposite unsafe labels."""

    pairs: List[Pair] = []
    for fiber in observation_fibers(worlds).values():
        for left, right in combinations(fiber, 2):
            if left.unsafe_label(action) != right.unsafe_label(action):
                pairs.append(Pair(left=left, right=right))
    return pairs


def find_witnesses(worlds: Iterable[ClinicalWorld], action: str) -> List[Witness]:
    """Enumerate all finite counterexamples to response-only identifiability."""

    witnesses: List[Witness] = []
    for pair in dangerous_pairs(worlds, action):
        witnesses.append(
            Witness(
                left_world_id=pair.left.world_id,
                right_world_id=pair.right.world_id,
                observation=pair.left.observation,
                action=action,
                left_label=pair.left.unsafe_label(action),
                right_label=pair.right.unsafe_label(action),
            )
        )
    return witnesses


def is_identifiable(worlds: Iterable[ClinicalWorld], action: str) -> bool:
    """Whether a perfect evaluator using only observation + action can exist."""

    return not dangerous_pairs(worlds, action)


def randomized_worst_case_error_lower_bound(
    worlds: Iterable[ClinicalWorld], action: str
) -> float:
    """Minimax error lower bound for any randomized evaluator.

    If an indistinguishable opposite-label pair exists, every randomized
    evaluator makes error at least 1/2 on one member of that pair.  If no such
    pair exists, the information-theoretic lower bound is zero.
    """

    return 0.5 if dangerous_pairs(worlds, action) else 0.0


def randomized_bayes_error_lower_bound(
    worlds: Iterable[ClinicalWorld], action: str, unsafe_prior: float = 0.5
) -> float:
    """Bayes error on an indistinguishable opposite-label pair.

    ``unsafe_prior`` is the conditional probability of the unsafe world within
    the witness pair.  The optimal constant prediction incurs
    ``min(p, 1-p)`` error.
    """

    if not 0.0 <= unsafe_prior <= 1.0:
        raise IdentifiabilityError("unsafe_prior must lie in [0, 1]")
    if not dangerous_pairs(worlds, action):
        return 0.0
    return min(unsafe_prior, 1.0 - unsafe_prior)


def _known(value: Any) -> bool:
    try:
        return value not in UNKNOWN_TOKENS
    except TypeError:
        # Unhashable values are still concrete answers.
        return True


def query_separates(
    pair: Pair,
    query: str,
    *,
    unknown_counts_as_answer: bool = False,
) -> bool:
    """Whether a query distinguishes a dangerous pair.

    By default UNKNOWN/ABSENT/UNOBSERVED do not count as informative answers,
    matching the fail-closed semantics used by the repository's minimum-
    information solver.
    """

    left_value = pair.left.facts.get(query, "ABSENT")
    right_value = pair.right.facts.get(query, "ABSENT")
    if not unknown_counts_as_answer and (
        not _known(left_value) or not _known(right_value)
    ):
        return False
    return left_value != right_value


def is_critical_question_closure(
    worlds: Iterable[ClinicalWorld],
    action: str,
    queries: Sequence[str],
    *,
    unknown_counts_as_answer: bool = False,
) -> bool:
    """Whether ``queries`` make the unsafe label identifiable.

    This implements the exact dangerous-pair criterion: every same-observation,
    opposite-label pair must be separated by at least one query.
    """

    query_tuple = tuple(dict.fromkeys(queries))
    for pair in dangerous_pairs(worlds, action):
        if not any(
            query_separates(
                pair,
                query,
                unknown_counts_as_answer=unknown_counts_as_answer,
            )
            for query in query_tuple
        ):
            return False
    return True


def minimum_critical_question_closures(
    worlds: Iterable[ClinicalWorld],
    action: str,
    candidate_queries: Sequence[str],
    *,
    max_queries: int = 24,
    unknown_counts_as_answer: bool = False,
) -> List[Tuple[str, ...]]:
    """Return every minimum-cardinality sufficient query set.

    Exhaustive search is deliberate and auditable for small instances.  The
    decision problem is NP-complete; callers must explicitly increase
    ``max_queries`` before attempting larger instances.
    """

    world_list = _as_world_list(worlds)
    queries = tuple(dict.fromkeys(candidate_queries))
    if len(queries) > max_queries:
        raise IdentifiabilityError(
            f"refusing exhaustive search over {len(queries)} queries; "
            f"max_queries={max_queries}"
        )

    for size in range(len(queries) + 1):
        solutions: List[Tuple[str, ...]] = []
        for subset in combinations(queries, size):
            if is_critical_question_closure(
                world_list,
                action,
                subset,
                unknown_counts_as_answer=unknown_counts_as_answer,
            ):
                solutions.append(subset)
        if solutions:
            return solutions
    return []


def certified_label(
    worlds: Iterable[ClinicalWorld],
    action: str,
    observation: Hashable,
    answers: Mapping[str, Hashable],
    *,
    unknown_counts_as_answer: bool = False,
) -> Optional[int]:
    """Return the unique label implied by observation + answers, else ``None``.

    This function does not decide whether the supplied answers are clinically
    trustworthy.  It only checks logical identification within the supplied
    finite world model.
    """

    candidates: List[ClinicalWorld] = []
    for world in _as_world_list(worlds):
        if world.observation != observation:
            continue
        compatible = True
        for query, answer in answers.items():
            if not unknown_counts_as_answer and not _known(answer):
                continue
            if world.facts.get(query, "ABSENT") != answer:
                compatible = False
                break
        if compatible:
            candidates.append(world)

    if not candidates:
        return None
    labels = {world.unsafe_label(action) for world in candidates}
    if len(labels) != 1:
        return None
    return next(iter(labels))


def hitting_set_reduction(
    universe: Sequence[str],
    subsets: Mapping[str, Iterable[str]],
    *,
    action: str = "proposed_action",
) -> Tuple[List[ClinicalWorld], Tuple[str, ...]]:
    """Reduce a finite Hitting Set instance to critical-question closure.

    For each universe element ``e`` the construction creates two worlds sharing
    one observation and carrying opposite labels.  Query ``q`` separates that
    pair exactly when ``e`` belongs to subset ``q``.  Therefore a query set closes
    every observation iff the corresponding subsets hit every universe element.
    """

    elements = tuple(dict.fromkeys(universe))
    query_names = tuple(subsets.keys())
    normalized = {name: set(values) for name, values in subsets.items()}

    unknown_elements = set().union(*normalized.values()) - set(elements) if normalized else set()
    if unknown_elements:
        raise IdentifiabilityError(
            f"subsets contain elements outside universe: {sorted(unknown_elements)!r}"
        )

    worlds: List[ClinicalWorld] = []
    for element in elements:
        safe_facts: Dict[str, int] = {query: 0 for query in query_names}
        unsafe_facts: Dict[str, int] = {
            query: int(element in normalized[query]) for query in query_names
        }
        observation = f"hitting-set-element:{element}"
        worlds.extend(
            [
                ClinicalWorld(
                    world_id=f"{element}:safe",
                    observation=observation,
                    facts=safe_facts,
                    labels={action: 0},
                ),
                ClinicalWorld(
                    world_id=f"{element}:unsafe",
                    observation=observation,
                    facts=unsafe_facts,
                    labels={action: 1},
                ),
            ]
        )
    return worlds, query_names


__all__ = [
    "ClinicalWorld",
    "IdentifiabilityError",
    "Pair",
    "Witness",
    "certified_label",
    "dangerous_pairs",
    "find_witnesses",
    "hitting_set_reduction",
    "is_critical_question_closure",
    "is_identifiable",
    "minimum_critical_question_closures",
    "observation_fibers",
    "query_separates",
    "randomized_bayes_error_lower_bound",
    "randomized_worst_case_error_lower_bound",
]
