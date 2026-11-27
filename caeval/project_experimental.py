"""Project-bound execution of the four experimental assessment protocols.

This is an explicit secondary execution lane. It does not route a family
through an incompatible canonical scorer, change selection, or satisfy a
canonical procurement requirement. All operations are offline.
"""
from pathlib import Path

from . import (buyer_agent as buyer, buyer_plan, experimental_evidence as evidence,
               formal_review as formal, project, selection, claim, manifest,
               institution_research as studio)
from .util import repo_root

PLAN_SCHEMA = "project-experimental-plan/1"
RESULT_SCHEMA = "project-experimental-result/1"
SUITES = {"citation": ("citation_verification",),
          "scribe": ("omission", "fabrication"),
          "decision": ("decision_certifiability",),
          "robustness": ("unsupported_inference", "rewording_robustness")}
LIMITS = ["Experimental execution is separate from canonical family execution.",
          "No canonical requirement is satisfied by this result.",
          "Candidate identity, reference validity and clinician credentials are not independently established.",
          "A matching plan or package digest is not proof of prospective registration.",
          "No clinical finding, purchasing recommendation or deployment approval."]


def fail(message):
    raise buyer.BuyerError(message)


def _digest(doc):
    return buyer_plan._digest(doc)


def _sealed(doc):
    return {**doc, "digest": _digest(doc)}


def _check_digest(doc):
    if not isinstance(doc,dict) or doc.get("digest") != _digest({k:v for k,v in doc.items() if k!="digest"}):
        fail("project experimental record digest mismatch")


def _scope(project_path):
    proj = project.load(project_path)
    proj.require_valid()
    audience = claim._audience_for(proj)
    target = {k:proj.target_meta[k] for k in ("name","version")}
    if any(not isinstance(x,str) or not x.strip() for x in target.values()):
        fail("explicit string product and version required")
    # Only the digest of the full configuration is exported; no subject URLs,
    # headers, panel configuration or local paths are included.
    selected = selection.select_suites(proj.profiles)
    return {"project_config_digest":_digest(proj.data),
            "selection_rules_digest":manifest.hash_file(repo_root()/"selection_rules.yaml"),
            "mode":proj.mode,"target":target,"audience":audience,
            "profiles":sorted(proj.profiles),"subject_kind":proj.subject["kind"],
            "selection":selected}


def _related(scope, kind):
    required = {x["suite"] for x in scope["selection"]["required_suites"]}
    return sorted(required.intersection(SUITES[kind]))


def build_plan(project_path, specs):
    """Freeze exact scientific plans under the existing canonical project intake."""
    scope = _scope(project_path)
    if not isinstance(specs,dict) or not specs or set(specs)-set(SUITES):
        fail("provide one or more recognized experimental module specifications")
    if scope["audience"]!="clinician":
        fail("these experimental modules do not measure the patient-facing audience")
    modules = {}
    for kind,spec in sorted(specs.items()):
        related = _related(scope,kind)
        if not related:
            fail("module is not selected by the project's declared profiles")
        plan = evidence.MODULES[kind].build_plan(spec)
        if plan["spec"]["target"] != scope["target"]:
            fail("experimental specification target/version differs from project")
        if scope["subject_kind"]=="mock" and plan["spec"]["synthetic"] is not True:
            fail("mock project cannot carry non-synthetic experimental inputs")
        modules[kind] = {"related_suites":related,"plan":plan}
    omitted = [kind for kind in sorted(SUITES) if _related(scope,kind) and kind not in modules]
    result = _sealed({"schema":PLAN_SCHEMA,"scope":scope,"modules":modules,
                      "unplanned_related_modules":omitted,"decision_grade":False,
                      "limitations":LIMITS[:]})
    evidence.encode(result)
    return result


def verify_plan(project_path, frozen, approved_digest):
    _check_digest(frozen)
    if frozen["digest"] != approved_digest or frozen.get("schema")!=PLAN_SCHEMA:
        fail("exact approved project plan digest required")
    if not isinstance(frozen.get("modules"),dict):
        fail("invalid module inventory")
    regenerated = build_plan(project_path,{k:v["plan"]["spec"] for k,v in frozen["modules"].items()})
    if regenerated != frozen:
        fail("project scope, scientific definition or module inventory changed")
    return frozen


def module_plan(project_path, frozen, approved_digest, kind):
    plan = verify_plan(project_path,frozen,approved_digest)
    if kind not in plan["modules"]:
        fail("module is not in the approved project plan")
    return plan["modules"][kind]["plan"]


def exchange(project_path, frozen, approved_digest, kind):
    plan = module_plan(project_path,frozen,approved_digest,kind)
    mod = evidence.MODULES[kind]
    return mod.prompts(plan) if kind=="citation" else mod.exchange(plan)


def response_template(project_path, frozen, approved_digest, kind):
    plan = module_plan(project_path,frozen,approved_digest,kind)
    mod = evidence.MODULES[kind]
    return studio._citation_template(plan) if kind=="citation" else mod.response_template(plan)


def prepare_review(project_path, frozen, approved_digest, kind, responses, destination,
                   reviewers, validity_reviewers, operator, *, confirmed=False):
    plan = module_plan(project_path,frozen,approved_digest,kind)
    if kind=="decision":
        fail("native decision assessment has no human review workspace")
    mod = evidence.MODULES[kind]
    if kind=="robustness":
        return mod.prepare(plan,responses,destination,validity_reviewers,reviewers,operator,confirmed=confirmed)
    if validity_reviewers:
        fail("separate validity roster is only supported for paired robustness")
    return mod.prepare(plan,responses,destination,reviewers,operator,confirmed=confirmed)


def complete_module(project_path, frozen, approved_digest, kind, source, *, confirmed=False):
    """Run native decision scoring or finalize exact completed independent reviews."""
    if confirmed is not True:
        fail("explicit reference and collection provenance approval required")
    plan = module_plan(project_path,frozen,approved_digest,kind)
    mod = evidence.MODULES[kind]
    if kind=="decision":
        responses = evidence.load(source)
        return evidence.build(kind,plan,responses,None,{},mod.assess(plan,responses))
    root = Path(source)
    loaded = mod._load(root)
    if kind=="robustness":
        _,stored,binding,_,issued = loaded
        responses = formal._json(root/"responses.json")
    else:
        _,stored,responses,binding,_,issued = loaded
    if stored != plan:
        fail("review workspace does not belong to frozen project module")
    report = mod.analyze(root)
    records = {rid:formal._json(root/"submissions"/(rid+".json")) for rid in issued}
    return evidence.build(kind,plan,responses,binding,records,report)


def _assemble(frozen, packages):
    if not isinstance(packages,dict) or not packages or set(packages)-set(frozen["modules"]):
        fail("provide exact planned module packages")
    receipts = {}
    for kind, package in sorted(packages.items()):
        expected = frozen["modules"][kind]["plan"]
        if package.get("kind")!=kind or package.get("plan")!=expected:
            fail("package differs from exact planned module")
        receipt = evidence.verify(package)
        receipts[kind] = {"package_digest":receipt["package_digest"],
                          "report_digest":receipt["report_digest"],
                          "plan_hash":receipt["plan_hash"],
                          "related_suites":frozen["modules"][kind]["related_suites"],
                          "synthetic":expected["spec"]["synthetic"],
                          "summary":package["report"]["summary"],
                          "decision_grade":False}
    coverage = []
    for suite in frozen["scope"]["selection"]["required_suites"]:
        sid = suite["suite"]
        related = [k for k in sorted(frozen["modules"]) if sid in frozen["modules"][k]["related_suites"]]
        coverage.append({"suite":sid,"canonical_runnable":suite["implemented"],
                         "canonical_blocked_reason":suite["blocked_reason"],
                         "canonical_execution_verified":False,
                         "canonical_requirement_satisfied":False,
                         "planned_experimental_modules":related,
                         "replayed_experimental_modules":[k for k in related if k in receipts]})
    payload = {"schema":RESULT_SCHEMA,"project_plan_digest":frozen["digest"],
               "target":frozen["scope"]["target"],"module_receipts":receipts,
               "packages":packages,"coverage":coverage,
               "uncompleted_planned_modules":sorted(set(frozen["modules"])-set(packages)),
               "unplanned_related_modules":frozen["unplanned_related_modules"],
               "all_planned_modules_replayed":set(packages)==set(frozen["modules"]),
               "decision_grade":False,"limitations":LIMITS[:]}
    result = _sealed(payload)
    evidence.encode(result)
    return result


def assemble(project_path, frozen, approved_digest, packages):
    return _assemble(verify_plan(project_path,frozen,approved_digest),packages)


def verify_result(project_path, frozen, approved_digest, result, *, expected_digest=None):
    _check_digest(result)
    if expected_digest is not None and result["digest"]!=expected_digest:
        fail("result differs from independently supplied digest")
    if result.get("schema")!=RESULT_SCHEMA:
        fail("unsupported experimental project result schema")
    actual = assemble(project_path,frozen,approved_digest,result["packages"])
    if result!=actual:
        fail("project result differs from independently replayed evidence")
    return {"status":"REPLAY_VERIFIED","digest":result["digest"],
            "project_plan_digest":approved_digest,
            "expected_digest_matched":expected_digest is not None,
            "all_planned_modules_replayed":actual["all_planned_modules_replayed"],
            "uncompleted_planned_modules":actual["uncompleted_planned_modules"],
            "unplanned_related_modules":actual["unplanned_related_modules"],
            "coverage":actual["coverage"],"decision_grade":False}
