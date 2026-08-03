"""Minimum-information acquisition for a DEFER.

Answers: *what is the smallest additional information that would decide whether
this action can be certified or must be blocked?*

PRIOR ART — STATED PLAINLY, NOT A NOVELTY CLAIM
-----------------------------------------------
This is the classical **Minimum Test Set / Test Cover / minimum test collection**
problem: choose the fewest (or cheapest) binary tests that separate every pair of
items. It is NP-hard, with an existing approximation literature; the greedy
`O(log n)` set-cover argument applies, and it is set-cover-hard to approximate
better than `(1-o(1)) ln n`. The ADAPTIVE variant (ask, observe, then choose the
next question) is Optimal Decision Tree, also hard to approximate. The
cost-weighted variant is value-of-information.

The contribution here is the CLINICAL INSTANTIATION — deriving the world set from
a version-pinned rule bundle plus clinician-authored critical questions and using
the solution as an evaluation endpoint — never the combinatorial problem itself.

SEMANTICS THAT MATTER CLINICALLY
--------------------------------
* `UNKNOWN` (or an absent answer) is NOT a test result. Treating "not measured" as
  a value would let an unmeasured query look decision-determining and would
  UNDERSTATE the information actually required — the worst failure direction here.
* Cardinality is the wrong default objective: a records lookup, a serum test and an
  invasive procedure are not one unit each. Pass `costs=` for the weighted problem.
* "No questions required" (`[()]`) and "no solution exists" (`[]`) are DIFFERENT
  outcomes and must never be conflated: the first means the case is already
  decided, the second means no available question can decide it.
* This solver is NON-ADAPTIVE (one batch of questions). Adaptive acquisition would
  usually need fewer questions in expectation; that is a separate problem.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable, Mapping, Sequence

UNKNOWN = "UNKNOWN"

# Above this many candidate queries, exhaustive search is refused unless the
# caller explicitly opts in — better to say "too large" than to hang.
EXHAUSTIVE_QUERY_LIMIT = 22


class MMIPError(ValueError):
    """The world set or cost model is unusable."""


def _answer(world: Mapping[str, Any], query: str) -> Any:
    answers = world.get("answers", {})
    if not isinstance(answers, Mapping):
        return UNKNOWN
    return answers.get(query, UNKNOWN)


def _is_safe(world: Any) -> bool:
    """Fail closed on a world that does not declare its safety."""
    if not isinstance(world, Mapping):
        raise MMIPError("each world must be a mapping")
    if "safe" not in world:
        raise MMIPError("world is missing the required 'safe' field; without it the "
                        "safe/unsafe pairs that must be distinguished are undefined")
    return bool(world["safe"])


def validate_worlds(worlds: Sequence[Mapping[str, Any]]) -> None:
    """Validate the world schema up front rather than mid-search."""
    if not isinstance(worlds, Sequence) or isinstance(worlds, (str, bytes)):
        raise MMIPError("worlds must be a sequence of mappings")
    for w in worlds:
        _is_safe(w)
        answers = w.get("answers", {})
        if not isinstance(answers, Mapping):
            raise MMIPError("world 'answers' must be a mapping when present")


def distinguishes(left: Mapping, right: Mapping, query: str) -> bool:
    """True only when BOTH worlds give a KNOWN, differing answer."""
    a, b = _answer(left, query), _answer(right, query)
    if a == UNKNOWN or b == UNKNOWN:
        return False
    return a != b


def _undecided_pairs(worlds: Sequence[Mapping]) -> list[tuple[Mapping, Mapping]]:
    return [(l, r) for l, r in combinations(worlds, 2) if _is_safe(l) != _is_safe(r)]


def is_decision_determining(worlds: Sequence[Mapping], selected_queries: Iterable[str]) -> bool:
    """Do the selected queries separate every safe/unsafe pair?"""
    selected = tuple(selected_queries)
    return all(any(distinguishes(l, r, q) for q in selected)
               for l, r in _undecided_pairs(worlds))


def resolvable(worlds: Sequence[Mapping], candidate_queries: Sequence[str]) -> bool:
    """Could the FULL candidate set decide the case? If not, no subset can."""
    return is_decision_determining(worlds, tuple(dict.fromkeys(candidate_queries)))


def minimum_query_sets(
    worlds: Sequence[Mapping[str, Any]],
    candidate_queries: Sequence[str],
    costs: Mapping[str, float] | None = None,
    allow_large: bool = False,
) -> list[tuple[str, ...]]:
    """Exact solver for small instances. Returns EVERY optimal set, so ties are
    explicit rather than silently arbitrated.

    Returns:
        ``[()]``  no questions required — the case is already decided
        ``[]``    NO SOLUTION — some safe/unsafe pair is indistinguishable by every
                  candidate query (a genuinely unresolvable case; the caller must
                  widen the question set, not ask more of these)
        else      all optimal (minimum-cardinality, or minimum-cost) query sets

    Complexity: ``C(n, k)`` at optimum size ``k`` — cheap when few questions
    resolve the case, expensive when many do. The operative bound is the SIZE OF
    THE ANSWER, not the number of candidate queries. Use `greedy_query_set` for
    large instances.
    """
    validate_worlds(worlds)
    unique = tuple(dict.fromkeys(candidate_queries))

    if not worlds:
        return [tuple()]
    safeties = [_is_safe(w) for w in worlds]
    if all(s == safeties[0] for s in safeties):
        return [tuple()]                       # already decided — distinct from []

    if not resolvable(worlds, unique):
        return []                              # no solution exists — distinct from [()]

    if len(unique) > EXHAUSTIVE_QUERY_LIMIT and not allow_large:
        raise MMIPError(
            f"{len(unique)} candidate queries exceeds the exhaustive limit of "
            f"{EXHAUSTIVE_QUERY_LIMIT}. Use greedy_query_set() for an approximate "
            f"answer, or pass allow_large=True to force exhaustive search.")

    if costs is None:
        for size in range(len(unique) + 1):
            sols = [s for s in combinations(unique, size) if is_decision_determining(worlds, s)]
            if sols:
                return sols
        return []

    missing = [q for q in unique if q not in costs]
    if missing:
        raise MMIPError(f"no cost supplied for queries: {missing}")
    for q in unique:
        c = costs[q]
        if not isinstance(c, (int, float)) or isinstance(c, bool) or c < 0:
            raise MMIPError(f"cost for {q!r} must be a non-negative number, got {c!r}")

    best: list[tuple[str, ...]] = []
    best_cost = float("inf")
    for size in range(len(unique) + 1):
        for subset in combinations(unique, size):
            total = sum(costs[q] for q in subset)
            if total > best_cost:
                continue
            if not is_decision_determining(worlds, subset):
                continue
            if total < best_cost:
                best_cost, best = total, [subset]
            elif subset not in best:
                best.append(subset)
    return best


def greedy_query_set(
    worlds: Sequence[Mapping[str, Any]],
    candidate_queries: Sequence[str],
    costs: Mapping[str, float] | None = None,
) -> tuple[str, ...]:
    """Approximate solver for instances too large to enumerate.

    Standard greedy set-cover over the safe/unsafe pairs (cost-effectiveness =
    newly separated pairs per unit cost). Returns a MINIMAL, not necessarily
    MINIMUM, set — the classical `O(log n)` guarantee, which is essentially the
    best possible unless P=NP. Callers reporting an "information efficiency"
    endpoint must state which solver produced the number.

    Returns `()` when nothing is required, and raises if the case is unresolvable.
    """
    validate_worlds(worlds)
    unique = tuple(dict.fromkeys(candidate_queries))
    pairs = _undecided_pairs(worlds)
    if not pairs:
        return tuple()
    if not resolvable(worlds, unique):
        raise MMIPError("no candidate query set can resolve this case; widen the question set")

    remaining = list(pairs)
    chosen: list[str] = []
    while remaining:
        best_q, best_score = None, 0.0
        for q in unique:
            if q in chosen:
                continue
            gain = sum(1 for l, r in remaining if distinguishes(l, r, q))
            if gain == 0:
                continue
            cost = float(costs[q]) if costs else 1.0
            score = gain / cost if cost > 0 else float("inf")
            if score > best_score:
                best_q, best_score = q, score
        if best_q is None:          # unreachable given the resolvable() guard
            raise MMIPError("no remaining query separates the outstanding pairs")
        chosen.append(best_q)
        remaining = [(l, r) for l, r in remaining if not distinguishes(l, r, best_q)]
    return tuple(chosen)


def information_efficiency(minimum_needed: int, actually_requested: int) -> float | None:
    """Evaluation endpoint: how close a system came to asking only what was needed.

    1.0 = asked exactly the determining questions. <1.0 = question-dumping.
    None when nothing was requested (undefined, not 0 — do not silently score it).
    """
    if actually_requested <= 0:
        return None
    return round(minimum_needed / actually_requested, 4)
