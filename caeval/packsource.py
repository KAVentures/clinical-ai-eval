"""Resolving the case pack a PROJECT declares.

Until v0.16 `run --project` ran `demo_target.base_cases()` with a TODO comment.
A user could describe their product and intended use, and the evidence package
would then describe an assessment of built-in demo vignettes. For a patient or
RAG project the demo cases are not merely unrepresentative — they are the wrong
SHAPE, so the run would either crash or silently mis-measure.

A project must now declare its pack. The pack is content-addressed, its kind is
checked against the family's executor, and a mismatch stops the run.

The one permitted default is explicit: a project may set
`case_pack.source: builtin:public_smoke` (patient) or `builtin:demo_clinician`,
which loads the shipped fixtures AND marks the run `demonstration` so no result
from it can be mistaken for an assessment of the user's own cases.
"""
from __future__ import annotations

import json
from pathlib import Path

BUILTIN_PREFIX = "builtin:"
BUILTIN_PACKS = {
    "builtin:demo_clinician": ("clinician_vignette",
                               "shipped demo vignettes — demonstration only"),
    "builtin:public_smoke": ("patient_worlds",
                             "synthetic patient smoke fixtures — demonstration only"),
    "builtin:demo_rag_corpus": ("rag_corpus_bound",
                                "synthetic corpus-bound queries — demonstration only"),
}


class PackSourceError(RuntimeError):
    pass


def _load_patient_dir(path: Path):
    """A patient pack directory: cases.json describing worlds."""
    f = path / "cases.json"
    if not f.exists():
        raise PackSourceError(
            f"patient pack {path} has no cases.json. A patient pack is a set of worlds, "
            f"not vignettes: see casepacks/patient/public_dev/ for the shape.")
    from .patient.stress import Trajectory, TrajectoryEvent
    from .patient.world import ClinicalWorld, Fact, PatientCase
    raw = json.loads(f.read_text())
    cases = []
    for c in raw:
        worlds = []
        for w in c["worlds"]:
            facts = [Fact(key=x["key"], value=x["value"],
                          disclosure=x.get("disclosure", "when_asked"),
                          load_bearing=bool(x.get("load_bearing", False)),
                          asked_by=tuple(x.get("asked_by", ())))
                     for x in w.get("facts", [])]
            worlds.append(ClinicalWorld(
                world_id=w["world_id"], required_disposition=w["required_disposition"],
                facts=facts, red_flags=list(w.get("red_flags", [])),
                forbidden_advice=list(w.get("forbidden_advice", [])),
                required_safety_net=list(w.get("required_safety_net", []))))
        traj = None
        if c.get("trajectory"):
            traj = Trajectory([
                TrajectoryEvent(
                    after_system_turn=int(e["after_system_turn"]),
                    reveal=tuple(Fact(key=r["key"], value=r["value"],
                                      disclosure=r.get("disclosure", "when_asked"),
                                      load_bearing=bool(r.get("load_bearing", False)),
                                      asked_by=tuple(r.get("asked_by", ())))
                                 for r in e.get("reveal", [])),
                    required_disposition_after=e.get("required_disposition_after", ""),
                    red_flags_added=tuple(e.get("red_flags_added", ())),
                    reason=e.get("reason", ""))
                for e in c["trajectory"]])
        cases.append(PatientCase(
            case_id=c["case_id"], opening_message=c["opening_message"], worlds=worlds,
            specialty=c.get("specialty", "general"), population=c.get("population", "adult"),
            profile=c.get("profile", {}), provenance=c.get("provenance", ""),
            trajectory=traj))
    return cases


def _load_clinician_dir(path: Path):
    f = path / "cases.jsonl"
    if not f.exists():
        f = path / "cases.json"
    if not f.exists():
        raise PackSourceError(
            f"clinician pack {path} has no cases.jsonl or cases.json")
    if f.suffix == ".jsonl":
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return json.loads(f.read_text())


def resolve(spec: dict, expected_kind: str):
    """Return (cases, descriptor). Fails closed on anything unresolvable.

    One project can select families needing DIFFERENT pack kinds — a clinician-RAG
    product is probed both by one-shot vignettes and by corpus-bound queries. A
    project may therefore key its packs by kind:

        case_pack:
          clinician_vignette: {source: ./packs/vignettes}
          rag_corpus_bound:   {source: ./packs/corpus}

    A bare `source` is still accepted and applies to whatever kind is asked for,
    which is what a single-family project wants.
    """
    spec = spec or {}
    if expected_kind in spec and isinstance(spec[expected_kind], dict):
        spec = spec[expected_kind]
    if not spec.get("source"):
        raise PackSourceError(
            "this project declares no case_pack. Add:\n"
            "  case_pack:\n"
            "    source: ./packs/my-pack        # or builtin:public_smoke\n"
            "    pack_id: my-pack\n"
            "    version: '1.0'\n"
            "A run against unspecified cases cannot describe the user's product.")
    source = str(spec["source"])

    if source.startswith(BUILTIN_PREFIX):
        if source not in BUILTIN_PACKS:
            raise PackSourceError(f"unknown builtin pack {source!r}; "
                                  f"known: {sorted(BUILTIN_PACKS)}")
        kind, note = BUILTIN_PACKS[source]
        cases = _load_builtin(source)
        descriptor = {"pack_id": source, "version": "builtin", "kind": kind,
                      "visibility": "public_dev", "clinician_reviewed": False,
                      "is_builtin": True, "note": note,
                      "demonstration_only": True}
    else:
        path = Path(source)
        if not path.is_dir():
            raise PackSourceError(f"case_pack.source {source!r} is not a directory")
        kind = spec.get("expected_kind") or expected_kind
        cases = (_load_patient_dir(path) if kind == "patient_worlds"
                 else _load_clinician_dir(path))
        meta_file = path / "pack.json"
        meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
        descriptor = {
            "pack_id": spec.get("pack_id") or meta.get("pack_id") or path.name,
            "version": str(spec.get("version") or meta.get("version") or "unversioned"),
            "kind": kind,
            "visibility": meta.get("visibility", "private_qualification"),
            # NEVER inferred: a pack is reviewed only if a signature says so.
            "clinician_reviewed": bool(meta.get("clinician_reviewed", False)),
            "is_builtin": False,
            "source": str(path),
            "demonstration_only": False,
        }

    if descriptor["kind"] != expected_kind:
        raise PackSourceError(
            f"pack {descriptor['pack_id']!r} is a {descriptor['kind']!r} pack, but the "
            f"selected family needs {expected_kind!r}. Running it would produce numbers "
            f"that do not describe the product.")
    descriptor["content_hash"] = content_hash(cases, descriptor["kind"])
    return cases, descriptor


def _load_builtin(source: str):
    if source == "builtin:demo_clinician":
        from targets import demo_target
        return demo_target.base_cases()
    if source == "builtin:demo_rag_corpus":
        from .rag.execute import demo_queries
        return demo_queries()
    import sys
    from .util import repo_root
    d = str(repo_root() / "casepacks" / "patient" / "public_dev")
    if d not in sys.path:
        sys.path.insert(0, d)
    from smoke_worlds import SMOKE_CASES
    return list(SMOKE_CASES)


def content_hash(cases, kind: str) -> str:
    from .casepack import PackMeta, pack_hash
    meta = PackMeta("resolve", "0", kind, "private_qualification")
    return pack_hash(meta, cases)
