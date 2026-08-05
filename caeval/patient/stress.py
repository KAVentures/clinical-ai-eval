"""Stress conditions as WORLD TRANSFORMATIONS, not prose edits.

The v0.14 defect this module exists to prevent: P1 suppressed a fact from the
rendered opening while `DisclosurePolicy` still marked it disclosed at turn 0. The
target never saw it, the measurement system believed it had been provided, asking
for it could not reveal it (already "disclosed"), and `missed_red_flag` could not
fire. That is precisely the subject/measurement mismatch this repository exists to
catch, committed inside the harness itself.

The rule that prevents the class of bug: **a stress condition that changes what the
subject can know must change the WORLD, not the rendering.** The simulator then
behaves consistently because there is only one source of truth about what the
patient knows and will say.

  P1  moves a spontaneous fact to `when_asked` — hidden until asked, then obtainable
  P5  swaps load-bearing facts for a sibling world's values; disposition MUST change
  P7  attaches timed trajectory events that add facts and can change the disposition

P3 and P4 are genuinely rendering-level (same world, different phrasing/pressure)
and stay in the simulator. P2 and P6 are properties of the fixture and the analysis,
not transformations.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace

from .world import DISPOSITIONS, ClinicalWorld, Fact, PatientCase, disposition_rank


class StressSpecError(ValueError):
    """A stress condition that cannot be applied as specified. Always raise: a
    silently skipped transformation produces a labelled episode that measures
    nothing, which is worse than an error."""


# --------------------------------------------------------------------------
# P1 — information removal
# --------------------------------------------------------------------------

def apply_information_removal(world: ClinicalWorld, fact_key: str) -> ClinicalWorld:
    """Move a spontaneously-disclosed fact behind a question.

    The fact stays OBTAINABLE: the point is that a competent history still reaches
    it. Making it `unavailable` would test something else entirely (an unwinnable
    world), and would let a target look bad for a failure the fixture caused.
    """
    f = world.fact(fact_key)
    if f is None:
        raise StressSpecError(
            f"P1 on world {world.world_id!r}: no fact {fact_key!r}. Removing a fact that "
            f"was never there removes nothing.")
    if f.disclosure != "spontaneous":
        raise StressSpecError(
            f"P1 on world {world.world_id!r}: fact {fact_key!r} has disclosure "
            f"{f.disclosure!r}, so it is already withheld from the opening. Applying P1 "
            f"would produce an episode identical to its own control.")
    facts = [replace(x, disclosure="when_asked") if x.key == fact_key else x
             for x in world.facts]
    return replace(world, world_id=f"{world.world_id}#p1-{fact_key}", facts=facts)


def removable_facts(world: ClinicalWorld) -> list:
    """Spontaneous load-bearing facts — the only ones P1 can meaningfully remove."""
    return sorted(f.key for f in world.facts if f.is_spontaneous() and f.load_bearing)


# --------------------------------------------------------------------------
# P5 — state substitution
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Substitution:
    """A declared transition between two sibling worlds of the same case."""
    source_world: str
    target_world: str
    changed_facts: tuple = ()

    def describe(self) -> dict:
        return {"source_world": self.source_world, "target_world": self.target_world,
                "changed_facts": list(self.changed_facts)}


def derive_substitution(case: PatientCase, source_world: str, target_world: str) -> Substitution:
    """Build the substitution and REFUSE if it does not change the answer.

    P5's whole claim is causal: change one load-bearing fact, the correct
    disposition changes. A substitution between worlds that agree is a relabelled
    control, and a difference measured across it is noise.
    """
    src, tgt = case.world(source_world), case.world(target_world)
    if src.required_disposition == tgt.required_disposition:
        raise StressSpecError(
            f"P5 {source_world!r}->{target_world!r}: both worlds require "
            f"{src.required_disposition!r}. A substitution that does not change the correct "
            f"disposition tests nothing.")
    changed = tuple(sorted(
        f.key for f in src.facts
        if tgt.fact(f.key) is None or tgt.fact(f.key).value != f.value))
    if not changed:
        raise StressSpecError(
            f"P5 {source_world!r}->{target_world!r}: no fact differs between the worlds.")
    return Substitution(source_world, target_world, changed)


def apply_state_substitution(case: PatientCase, sub: Substitution) -> ClinicalWorld:
    """The substituted world: the source world with the target's values for the
    changed facts, carrying the target's required disposition and hazards.

    Built by transformation rather than by simply running the sibling world, so the
    episode records WHAT was substituted — otherwise P5 is indistinguishable from
    'we also ran the other world', and the changed facts are not recoverable from
    the trace.
    """
    src, tgt = case.world(sub.source_world), case.world(sub.target_world)
    facts = []
    for f in src.facts:
        t = tgt.fact(f.key)
        facts.append(replace(f, value=t.value) if (t is not None and f.key in sub.changed_facts)
                     else f)
    for t in tgt.facts:                     # facts present only in the target world
        if src.fact(t.key) is None and t.key in sub.changed_facts:
            facts.append(t)
    return replace(
        src,
        world_id=f"{src.world_id}#p5->{tgt.world_id}",
        required_disposition=tgt.required_disposition,
        facts=facts,
        red_flags=list(tgt.red_flags),
        forbidden_advice=list(tgt.forbidden_advice),
        required_safety_net=list(tgt.required_safety_net))


# --------------------------------------------------------------------------
# P7 — longitudinal trajectory
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TrajectoryEvent:
    """A clinical state change at a specific point in the conversation."""
    after_system_turn: int
    reveal: tuple = ()                       # tuple[Fact, ...]
    required_disposition_after: str = ""
    red_flags_added: tuple = ()
    reason: str = ""

    def __post_init__(self):
        if self.after_system_turn < 1:
            raise StressSpecError("a trajectory event must follow at least one system turn")
        if self.required_disposition_after and \
                self.required_disposition_after not in DISPOSITIONS:
            raise StressSpecError(
                f"required_disposition_after {self.required_disposition_after!r} "
                f"not in {DISPOSITIONS}")

    def describe(self) -> dict:
        return {"after_system_turn": self.after_system_turn,
                "reveals": [f.key for f in self.reveal],
                "required_disposition_after": self.required_disposition_after,
                "red_flags_added": list(self.red_flags_added),
                "reason": self.reason}


@dataclass
class Trajectory:
    """Timed events for a P7 episode, declared on the case."""
    events: list = field(default_factory=list)

    def __post_init__(self):
        turns = [e.after_system_turn for e in self.events]
        if len(turns) != len(set(turns)):
            raise StressSpecError("two trajectory events on the same turn")
        self.events = sorted(self.events, key=lambda e: e.after_system_turn)

    def due(self, system_turn: int) -> list:
        return [e for e in self.events if e.after_system_turn == system_turn]

    def escalates(self, base_disposition: str) -> bool:
        """Does this trajectory ever require MORE urgency than it started with?"""
        return any(e.required_disposition_after and
                   disposition_rank(e.required_disposition_after) > disposition_rank(base_disposition)
                   for e in self.events)


def validate_trajectory(world: ClinicalWorld, traj: Trajectory) -> None:
    """A P7 trajectory that never changes the clinical picture is prose, not a
    stress test — the v0.14 defect, where P7 appended 'it's getting worse' and
    changed nothing measurable."""
    if not traj.events:
        raise StressSpecError(f"P7 on {world.world_id!r}: no trajectory events declared")
    if not any(e.reveal for e in traj.events):
        raise StressSpecError(
            f"P7 on {world.world_id!r}: no event reveals a fact. Without a new fact the "
            f"target is being tested against an ungrounded phrase, and nothing enters the "
            f"ledger to check its reasoning against.")
    for e in traj.events:
        for f in e.reveal:
            if world.fact(f.key) is not None:
                raise StressSpecError(
                    f"P7 on {world.world_id!r}: event reveals {f.key!r}, which the world "
                    f"already contains. A trajectory must add new information.")


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

WORLD_TRANSFORMING = {"P1_INFORMATION_REMOVAL", "P5_STATE_SUBSTITUTION", "P7_LONGITUDINAL"}
RENDERING_ONLY = {"P3_COMMUNICATION_STYLE", "P4_REASSURANCE_PRESSURE"}
ANALYSIS_ONLY = {"P2_INFORMATION_NECESSITY", "P6_REASONING_FIDELITY"}


def prepare(case: PatientCase, world_id: str, stress_test: str, spec: dict | None = None):
    """Return (world, trajectory, applied_spec) for an episode.

    `spec` carries the declaration the condition needs (which fact P1 removes, which
    sibling world P5 substitutes toward, the P7 trajectory). A world-transforming
    condition with no usable spec RAISES rather than silently degrading to baseline.
    """
    spec = spec or {}
    world = case.world(world_id)
    if stress_test == "P1_INFORMATION_REMOVAL":
        key = spec.get("fact_key") or (removable_facts(world) or [None])[0]
        if key is None:
            raise StressSpecError(
                f"P1 on {world_id!r}: the world declares no spontaneous load-bearing fact, so "
                f"there is nothing to withhold. Running it anyway would produce an episode "
                f"identical to BASELINE while claiming to be an information-removal probe.")
        return apply_information_removal(world, key), None, {"removed_fact": key}
    if stress_test == "P5_STATE_SUBSTITUTION":
        target = spec.get("target_world")
        if target is None:
            sibs = [w.world_id for w in case.worlds
                    if w.world_id != world_id
                    and w.required_disposition != world.required_disposition]
            if not sibs:
                raise StressSpecError(
                    f"P5 on {world_id!r}: no sibling world requires a different disposition, "
                    f"so no substitution can change the correct answer.")
            target = sorted(sibs)[0]
        sub = derive_substitution(case, world_id, target)
        return apply_state_substitution(case, sub), None, {"substitution": sub.describe()}
    if stress_test == "P7_LONGITUDINAL":
        traj = spec.get("trajectory") or getattr(case, "trajectory", None)
        if traj is None:
            raise StressSpecError(
                f"P7 on {world_id!r}: no trajectory declared. Appending a phrase like "
                f"'it's getting worse' without a structured state change tests the product "
                f"against prose, not against a clinical trajectory.")
        validate_trajectory(world, traj)
        # DEEP COPY: firing a trajectory appends facts and rewrites the required
        # disposition. Handing back the case's own world object would let one P7
        # episode permanently alter the fixture for every later episode — the
        # second run would then see the fact "already present" and be skipped, and
        # every BASELINE afterwards would be scored against a mutated world.
        return copy.deepcopy(world), traj, {"trajectory": [e.describe() for e in traj.events]}
    return world, None, {}


def applicable(case: PatientCase, world_id: str, stress_test: str, spec: dict | None = None) -> tuple:
    """(ok, reason) — used to SKIP LOUDLY rather than run a hollow episode."""
    try:
        prepare(case, world_id, stress_test, spec)
        return True, ""
    except StressSpecError as e:
        return False, str(e)
