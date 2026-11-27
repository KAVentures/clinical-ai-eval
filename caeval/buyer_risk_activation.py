"""Explicit activation of buyer risk criteria for canonical single-turn results.

Only existing top-level binary metrics are selectable. No interpretation of
free-text endpoints, automatic model calls or change to clinical maturity.
"""
import json
import hashlib
import tempfile
from pathlib import Path

from . import (buyer_agent as buyer, buyer_plan, buyer_manual as manual,
               buyer_assessment as assessment, buyer_risk_protocol as risk,
               institution_access as access, institution_setup as setup,
               procurement_workflow as pw, formal_review as formal, manifest)
from .util import stable_hash_text
from .report import recorded_judges

METRICS=("panel_any_unsafe","panel_majority_unsafe","potentially_harmful_treatment")
SCHEMA="buyer-risk-conditions/1"
DENOMINATOR="all_generated_cells"
LIMITATIONS=[
    "Only canonical generic paired-text results with explicit top-level binary metrics are eligible.",
    "All generated cells, including original and variant cells, form the denominator; invalid or incomplete cells remain missing.",
    "Unmapped hazards and blocked suites are not measured by this study.",
    "No guarantee of clinical validity, prior non-exposure, purchasing or deployment authorization.",
]


def _fail(message):
    raise access.AccessDenied(message)


def _compile(directory,conn,plan_id,pack_id,panel_id,mappings):
    risk._schema(conn)
    row=conn.execute("SELECT * FROM buyer_risk_protocols WHERE plan_id=?",(manual._identifier(plan_id,digest=True),)).fetchone()
    if not row:
        _fail("freeze a buyer risk protocol first")
    saved=risk._read(conn,row,directory)
    if not saved["scope_current"]:
        _fail("risk scope is stale")
    p=saved["protocol"]
    exchange=manual._build(directory,plan_id,pack_id)
    raw,panel=assessment._panel(directory,panel_id)
    runnable=set(exchange["family_ids"])
    hazards={h["hazard_id"]:h for h in p["hazards"]}
    if not isinstance(mappings,dict) or not mappings or set(mappings)-set(hazards):
        _fail("explicit mappings to known frozen hazards required")
    criteria=[]
    for hid,metric in sorted(mappings.items()):
        h=hazards[hid]
        if metric not in METRICS or h["suite"] not in runnable:
            _fail("unsupported canonical family or metric")
        criteria.append({"hazard_id":hid,"family":h["suite"],"description":h["description"],
                         "severity":h["severity"],"tests":["ALL"],
                         "acceptance_criterion":{"metric":metric,"operator":h["operator"],"threshold":h["threshold"]}})
    families=sorted({h["family"] for h in criteria})
    # Every selected family must have a mapped criterion. Otherwise the legacy
    # comparator's fallback could apply an unrelated family's hazards.
    if set(families)!=runnable:
        _fail("map at least one hazard for every runnable family")
    material={"schema":SCHEMA,"risk_protocol_id":saved["id"],"plan_id":plan_id,
        "vendor_id":p["vendor_id"],"target":p["target"],"pack_id":pack_id,
        "pack_content_hash":exchange["pack_content_hash"],"panel_id":panel_id,
        "panel_file_sha256":hashlib.sha256(raw).hexdigest(),
        "judge_panel_hash":stable_hash_text(json.dumps(recorded_judges(panel["judges"]),sort_keys=True)),
        "judge_prompt_hash":manifest.hash_file(manifest.definition_file("prompts/judge_prompt.txt")),
        "selection_rules_hash":p["selection_rules_hash"],
        "family_definition_hashes":{f:p["family_definition_hashes"][f] for f in families},
        "cell_inventory":{f:sorted(k for k,v in exchange["slots"].items() if f in v["families"]) for f in families},
        "families":families,"hazards":criteria,"mappings":mappings,
        "unmapped_hazards":sorted(set(hazards)-set(mappings)),
        "required_but_not_run":sorted(s["suite"] for s in p["coverage"] if not s["canonical_runnable"]),
        "denominator":DENOMINATOR,"decision_grade":False,"limitations":LIMITATIONS[:]}
    return {**material,"digest":buyer_plan._digest(material)}


def preview(directory,actor,plan_id,pack_id,panel_id,mappings):
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        return _compile(directory,conn,plan_id,pack_id,panel_id,mappings)


def activate(directory,actor,plan_id,pack_id,panel_id,mappings,approved_digest,*,confirmed=False):
    if confirmed is not True:
        _fail("explicit endpoint and denominator approval required")
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        access.require(actor,"admin")
        risk._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        binding=_compile(directory,conn,plan_id,pack_id,panel_id,mappings)
        if approved_digest!=binding["digest"]:
            _fail("approve the exact compiled conditions digest")
        root=buyer._root(directory)
        dest=root/"assessment"
        if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
            _fail("invalid assessment directory")
        dest.mkdir(mode=0o700,exist_ok=True)
        if (dest/pw.STATE_FILE).exists() or (dest/pw.STATE_FILE).is_symlink():
            _fail("existing procurement study cannot be overwritten")
        # Generate canonical state in scratch, then publish exclusively.
        with tempfile.TemporaryDirectory(prefix=".risk-activation-",dir=root) as tmp:
            state=pw.init(tmp,"Buyer risk-bound assessment",binding["families"],
                {"pack_id":pack_id,"content_hash":binding["pack_content_hash"]},
                "approved-panel:"+panel_id,binding["hazards"])
            state["conditions"]["risk_activation"]=binding
            state["conditions_hash"]=pw.conditions_hash(state["conditions"])
            pw.save(tmp,state)
            state=pw.add_vendor(tmp,binding["vendor_id"],binding["target"],"Product A")
            formal._publish_once(dest/pw.STATE_FILE,state)
        access._audit(conn,actor["user_id"],"buyer-risk-conditions-activated",binding["digest"])
        return {"conditions_hash":state["conditions_hash"],"binding":binding,"decision_grade":False}


def check(directory,plan_id,pack_hash,panel_id,raw_panel):
    """Validate a requested prepared assessment against the actual frozen study."""
    root=buyer._root(directory)
    dest=root/"assessment"
    if dest.is_symlink() or (dest/pw.STATE_FILE).is_symlink():
        _fail("invalid study path")
    state=pw.load(dest)
    binding=state["conditions"].get("risk_activation")
    if not binding:
        _fail("risk-bound conditions are not activated")
    with access.db(directory) as conn:
        expected=_compile(directory,conn,plan_id,binding["pack_id"],panel_id,binding["mappings"])
    if (binding!=expected or binding["plan_id"]!=plan_id or binding["pack_content_hash"]!=pack_hash
            or binding["panel_file_sha256"]!=hashlib.sha256(raw_panel).hexdigest()):
        _fail("prepared inputs differ from activated conditions")
    _check_conditions(state,binding)
    return state["conditions_hash"]


def _check_conditions(state,b):
    if (b.get("schema")!=SCHEMA or b.get("decision_grade") is not False or
            buyer_plan._digest({k:v for k,v in b.items() if k!="digest"})!=b["digest"] or
            state["conditions"]["families"]!=b["families"] or
            state["conditions"]["hazards"]!=b["hazards"] or
            state["conditions"]["case_pack"]["content_hash"]!=b["pack_content_hash"] or
            state["conditions"]["panel_config"]!="approved-panel:"+b["panel_id"]):
        _fail("risk activation conditions changed")


def check_package(state,vendor_id,family,environment,plan_binding,cells):
    """Extra ingestion gate for risk-bound studies; legacy studies unaffected."""
    b=state["conditions"].get("risk_activation")
    if b is None:
        return cells
    _check_conditions(state,b)
    if (vendor_id!=b["vendor_id"] or family not in b["families"] or
            plan_binding.get("target_name")!=b["target"]["name"] or
            plan_binding.get("target_version")!=b["target"]["version"] or
            plan_binding.get("family_id")!=family or
            plan_binding.get("case_pack_hash")!=b["pack_content_hash"] or
            environment.get("judge_panel_hash")!=b["judge_panel_hash"] or
            environment.get("judge_prompt_hash")!=b["judge_prompt_hash"] or
            environment.get("selection_rules_hash")!=b["selection_rules_hash"] or
            environment.get("family_version")!="sha256:"+b["family_definition_hashes"][family]):
        _fail("verified package differs from frozen risk conditions")
    ids=[c["cell_id"] for c in cells]
    if len(ids)!=len(set(ids)) or sorted(ids)!=b["cell_inventory"][family]:
        _fail("verified result does not cover the exact frozen cell inventory")
    normalized=[]
    for cell in cells:
        row=dict(cell)
        for metric in METRICS:
            value=row.get(metric)
            if value is not None and (type(value) is not int or value not in (0,1)):
                _fail("canonical metric is not binary")
            if (row.get("evaluation_complete") is not True or
                    (row.get("kind")=="variant" and row.get("validity_valid") is not True)):
                row[metric]=None
        normalized.append(row)
    return normalized
