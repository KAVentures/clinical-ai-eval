"""Typed target, case-pack and stress-test registries.

Registration is fail-closed in the same way the rest of the kernel is: an unknown
target, an unknown case pack, or an unknown stress test raises rather than
defaulting. A registry that silently accepts an unknown id is how a run ends up
labelled with a subject it did not test.

Every patient family is registered `experimental` and there is no API to raise it
here — maturity is a property of accumulated evidence, not of a constructor
argument.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .session import STRESS_TESTS

PATIENT_FAMILY_MATURITY = "experimental"   # not overridable from this module


@dataclass(frozen=True)
class TargetSpec:
    target_id: str
    version: str
    fn: object
    kind: str                      # mock | http_adapter | local_model
    description: str = ""
    is_mock: bool = False

    def __post_init__(self):
        if self.kind not in ("mock", "http_adapter", "local_model"):
            raise ValueError(f"unknown target kind {self.kind!r}")
        if self.kind == "mock" and not self.is_mock:
            raise ValueError("kind='mock' must set is_mock=True (a mock subject can "
                             "never carry a validity claim)")


@dataclass(frozen=True)
class CasePackSpec:
    pack_id: str
    version: str
    cases: tuple
    visibility: str                # public_dev | private_qualification
    clinician_reviewed: bool = False
    provenance: str = ""

    def __post_init__(self):
        if self.visibility not in ("public_dev", "private_qualification"):
            raise ValueError(f"unknown visibility {self.visibility!r}")
        if self.visibility == "public_dev" and self.clinician_reviewed:
            raise ValueError("public_dev packs are wiring fixtures and must not be "
                             "marked clinician_reviewed")


class _Registry(dict):
    def __init__(self, what):
        super().__init__()
        self._what = what

    def register(self, key, value):
        if key in self:
            raise ValueError(f"{self._what} {key!r} already registered")
        self[key] = value
        return value

    def get_or_fail(self, key):
        if key not in self:
            raise KeyError(f"unknown {self._what} {key!r}; registered: {sorted(self)}")
        return self[key]


TARGETS = _Registry("target")
CASE_PACKS = _Registry("case pack")


def resolve_stress_test(name: str) -> str:
    if name not in STRESS_TESTS:
        raise KeyError(f"unknown stress test {name!r}; known: {sorted(STRESS_TESTS)}")
    return name


def register_mock_targets():
    from . import mock_targets as m
    specs = [
        ("mock_baseline", m.baseline_target, "asks once, commits on partial information"),
        ("mock_defective", m.defective_target, "no history, reassures, forbidden self-treatment"),
        ("mock_repaired", m.repaired_target, "takes load-bearing history, then dispositions"),
        ("mock_over_conservative", m.over_conservative_target,
         "refers everything; zero under-triage, not patient-ready"),
    ]
    for tid, fn, desc in specs:
        if tid not in TARGETS:
            TARGETS.register(tid, TargetSpec(tid, "0", fn, "mock", desc, is_mock=True))
    return TARGETS


def register_public_smoke_pack(cases):
    if "public_smoke" not in CASE_PACKS:
        CASE_PACKS.register("public_smoke", CasePackSpec(
            "public_smoke", "0.1", tuple(cases), "public_dev",
            clinician_reviewed=False,
            provenance="synthetic wiring fixture; no clinician authored or reviewed these"))
    return CASE_PACKS
