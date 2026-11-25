"""Finite, executable decision-certifiability assessment.

The independent author supplies a small, version-pinned rule bundle and
bounded patient fact domains. The reference certificate is derived, never
accepted from the candidate. The existing verifier and MMIP solver are
reused. A conditional verdict is relative to authored rules, not a medical
recommendation or a proof of clinical validity.
"""
from __future__ import annotations

import itertools
import math
from datetime import datetime

from . import buyer_agent as buyer, buyer_manual as manual, buyer_plan
from . import manifest
from .certificates import verifier as v, mmip

SCHEMA="decision-certifiability-assessment/1"
VERDICTS=(v.CERTIFIED,v.DEFER,v.BLOCK)
OUTCOMES=("complete","timeout","provider_error","extraction_failure")
PROBES=("complete","missing","contraindication","stale","obsolete_source")
MAX_FACTS=10
MAX_WORLDS=128
MAX_CASES=50
MAX_CHECKS=30
DEFINITIONS=("caeval/decision_assessment.py","caeval/certificates/verifier.py",
             "caeval/certificates/mmip.py","schemas/clinical_certificate.schema.json",
             "tests/decision_certifiability/family.yaml")


def fail(message):
    raise buyer.BuyerError(message)


def _strict(obj,keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys):
        fail("unexpected or missing decision assessment fields")


def _text(value,limit=4000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        fail("nonempty bounded text required")
    return value


def _id(value):
    return manual._identifier(value)


def _bounded(obj):
    if len(buyer_plan._canonical(obj))>manual.MAX_REQUEST_BYTES//2:
        fail("decision assessment exceeds approved size")
    return obj


def _definitions():
    d={p:manifest.hash_file(manifest.definition_file(p)) for p in DEFINITIONS}
    if not all(d.values()):
        fail("assessment definitions unavailable")
    return d


def _timestamp(value):
    _text(value,64)
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError:
        fail("invalid snapshot timestamp")
    if dt.utcoffset() is None:
        fail("timestamp must have timezone")
    return dt


def _bundle(bundle):
    _strict(bundle,("bundle_id","version","sources","facts","action","support","checks"))
    _id(bundle["bundle_id"])
    _text(bundle["version"],128)
    sources={}
    if not isinstance(bundle["sources"],list) or not 1<=len(bundle["sources"])<=30:
        fail("bounded source bundle required")
    for source in bundle["sources"]:
        _strict(source,("id","version","status","text"))
        sid=_id(source["id"])
        if sid in sources or source["status"] not in ("active","superseded"):
            fail("duplicate source or invalid status")
        _text(source["version"],128)
        _text(source["text"],30000)
        sources[sid]=source
    facts={}
    if not isinstance(bundle["facts"],list) or not 1<=len(bundle["facts"])<=MAX_FACTS:
        fail("bounded fact domains required")
    for fact in bundle["facts"]:
        _strict(fact,("id","label","domain","cost"))
        fid=_id(fact["id"])
        _text(fact["label"],256)
        d=fact["domain"]
        cost=fact["cost"]
        if (fid in facts or not isinstance(d,list) or not 2<=len(d)<=5
                or any(not isinstance(x,str) or not x.strip() or len(x)>128 or x==mmip.UNKNOWN for x in d)
                or len(set(d))!=len(d) or type(cost) is not int or not 1<=cost<=10000):
            fail("invalid fact domain or integer acquisition cost")
        facts[fid]=fact
    if math.prod(len(f["domain"]) for f in facts.values())>MAX_WORLDS:
        fail("finite world limit exceeded")
    action=bundle["action"]
    # All concrete action parameters are bound, not just the drug/procedure code.
    if not isinstance(action,dict) or not {"code"}<=set(action)<={"code","type","dose","route","frequency","duration"}:
        fail("exact proposed action required")
    for value in action.values():
        _text(value,256)
    support=bundle["support"]
    _strict(support,("rule_id","source_id","quote"))
    _id(support["rule_id"])
    _quote(support,sources)
    checks=bundle["checks"]
    if not isinstance(checks,list) or not 1<=len(checks)<=MAX_CHECKS:
        fail("nonempty mandatory checklist required")
    seen=set()
    used=set()
    for check in checks:
        _strict(check,("id","label","kind","fact_id","allowed","severity","effect","source_id","quote"))
        cid=_id(check["id"])
        _text(check["label"],256)
        fid=check["fact_id"]
        allowed=check["allowed"]
        if (cid in seen or fid not in facts or check["kind"] not in ("critical_question","contraindication")
                or check["severity"] not in v.SEVERITIES or check["effect"] not in v.EFFECTS
                or not isinstance(allowed,list) or not allowed
                or any(x not in facts[fid]["domain"] for x in allowed)
                or len(set(allowed))!=len(allowed)
                or set(allowed)==set(facts[fid]["domain"])):
            fail("invalid mandatory check")
        if check["kind"]=="contraindication" and check["effect"]!="block":
            fail("authored contraindication must block")
        _quote(check,sources)
        seen.add(cid)
        used.add(fid)
    # No unmodeled fact may be passed off as decision-determining. Every
    # declared variable is used by the mandatory rule checklist.
    if used!=set(facts):
        fail("all declared facts must be used by the rule bundle")
    return sources,facts


def _quote(rule,sources):
    if rule["source_id"] not in sources or sources[rule["source_id"]]["status"]!="active":
        fail("rules require a pinned active source")
    if _text(rule["quote"],8000) not in sources[rule["source_id"]]["text"]:
        fail("rule quotation does not resolve to pinned source")


def _observed(case,facts):
    observations=case["observations"]
    if not isinstance(observations,dict) or set(observations)!=set(facts):
        fail("explicit known/missing/stale state required for every fact")
    snapshot=_timestamp(case["captured_at"])
    known={}
    for fid,row in observations.items():
        _strict(row,("state","value","recorded_at"))
        state=row["state"]
        if state not in ("known","missing","stale"):
            fail("invalid observation state")
        if state=="missing":
            if row["value"] is not None or row["recorded_at"] is not None:
                fail("missing observation must not assert a value")
        else:
            if row["value"] not in facts[fid]["domain"] or _timestamp(row["recorded_at"])>snapshot:
                fail("invalid fact value or future observation")
            if state=="known":
                known[fid]=row["value"]
    return known


def _provenance(bundle,rule):
    source=next(s for s in bundle["sources"] if s["id"]==rule["source_id"])
    return ["source:"+source["id"]+":"+source["version"]+":"+buyer_plan._digest(source),
            "quote:"+buyer_plan._digest(rule["quote"])]


def _certificate(bundle,case,known):
    needed={r["source_id"] for r in bundle["checks"]}|{bundle["support"]["source_id"]}
    # An obsolete-source probe explicitly withholds the current rule source.
    available=set(case["available_source_ids"])
    source_rows=[{k:s[k] for k in ("id","version","status")} for s in bundle["sources"]
                 if s["id"] in available]
    support=bundle["support"]
    support_present=support["source_id"] in available
    certificate={"certificate_id":case["case_id"],
                 "patient_snapshot":{"captured_at":case["captured_at"],"facts":case["observations"]},
                 "evidence_bundle":source_rows,"action":bundle["action"],
                 "support":[{"rule_id":support["rule_id"],"applicable":support_present,
                             "conflict":False,"provenance":_provenance(bundle,support) if support_present else []}],
                 "critical_questions":[],"contraindications":[]}
    for rule in bundle["checks"]:
        fid=rule["fact_id"]
        source_present=rule["source_id"] in available
        if not source_present or fid not in known:
            status="unknown"
        else:
            good=known[fid] in rule["allowed"]
            status=("pass" if good else "fail") if rule["kind"]=="critical_question" else ("absent" if good else "present")
        row={"id":rule["id"],"label":rule["label"],"status":status,
             "severity":rule["severity"],"certificate_effect":rule["effect"],
             "provenance":(_provenance(bundle,rule) if source_present else [])+
                          ["observation:"+fid+":"+buyer_plan._digest(case["observations"][fid])]}
        certificate["critical_questions" if rule["kind"]=="critical_question" else "contraindications"].append(row)
    return certificate,needed<=available


def _worlds(bundle,facts,known):
    keys=sorted(facts)
    values=[([known[k]] if k in known else facts[k]["domain"]) for k in keys]
    result=[]
    for vals in itertools.product(*values):
        answers=dict(zip(keys,vals))
        permitted=all(answers[c["fact_id"]] in c["allowed"] for c in bundle["checks"])
        result.append({"id":buyer_plan._digest(answers)[:24],"safe":permitted,"answers":answers})
    return result


def _reference(bundle,case):
    sources,facts=_bundle(bundle)
    _strict(case,("case_id","probe","captured_at","observations","available_source_ids"))
    _id(case["case_id"])
    if case["probe"] not in PROBES:
        fail("unknown probe")
    source_ids=case["available_source_ids"]
    if (not isinstance(source_ids,list) or any(s not in sources for s in source_ids)
            or len(source_ids)!=len(set(source_ids))):
        fail("invalid source availability inventory")
    known=_observed(case,facts)
    cert,all_sources=_certificate(bundle,case,known)
    result=v.verify_certificate(cert).to_dict()
    # The verifier is structural. The independently derived closure is the
    # authoritative assessment reference: a missing rule source never licenses
    # an action, even if a candidate has an otherwise plausible certificate.
    if not all_sources and result["verdict"]==v.CERTIFIED:
        fail("reference failed closed-source invariant")
    worlds=_worlds(bundle,facts,known)
    mmip.validate_worlds(worlds)
    classification=mmip.classify(worlds)
    unknown=sorted(set(facts)-set(known))
    costs={k:facts[k]["cost"] for k in unknown}
    min_sets=mmip.minimum_query_sets(worlds,unknown,costs=costs)
    witness=mmip.witness_of_underdetermination(worlds,action=bundle["action"]["code"],
                                              world_set_provenance=bundle["bundle_id"]+":"+bundle["version"])
    expected=result["verdict"]
    probe=case["probe"]
    state_ok={
        "complete":not unknown and all_sources and expected==v.CERTIFIED,
        "missing":any(x["state"]=="missing" for x in case["observations"].values()) and all_sources and expected==v.DEFER and classification==mmip.ACTION_UNDERDETERMINED,
        "stale":any(x["state"]=="stale" for x in case["observations"].values()) and all_sources and expected==v.DEFER and classification==mmip.ACTION_UNDERDETERMINED,
        "contraindication":all_sources and expected==v.BLOCK and any(c["status"]=="present" for c in cert["contraindications"]),
        "obsolete_source":not unknown and not all_sources and expected!=v.CERTIFIED and any(sources[s]["status"]=="superseded" for s in source_ids),
    }[probe]
    if not state_ok:
        fail("authored probe does not actually instantiate its declared condition")
    return {"certificate":cert,"verification":result,"worlds":worlds,
            "classification":classification,"minimum_query_sets":[list(x) for x in min_sets],
            "minimum_cost":min(sum(costs[q] for q in x) for x in min_sets) if min_sets else None,
            "witness":witness,"unknown_facts":unknown,"query_costs":costs,
            "source_closure":all_sources}


def build_plan(spec):
    _bounded(spec)
    _strict(spec,("target","synthetic","rule_bundle","cases"))
    _strict(spec["target"],("name","version"))
    for x in spec["target"].values():
        _text(x,256)
    if type(spec["synthetic"]) is not bool:
        fail("explicit synthetic provenance required")
    bundle=spec["rule_bundle"]
    _bundle(bundle)
    cases=spec["cases"]
    if not isinstance(cases,list) or not 1<=len(cases)<=MAX_CASES:
        fail("bounded case inventory required")
    ids=set()
    items=[]
    for case in cases:
        ref=_reference(bundle,case)
        cid=case["case_id"]
        if cid in ids:
            fail("duplicate case")
        ids.add(cid)
        slot=buyer_plan._digest([bundle["bundle_id"],bundle["version"],case])
        items.append({"slot":slot,"case":case,"reference":ref})
    plan={"schema":SCHEMA,"spec":spec,"items":items,"definitions":_definitions(),
          "decision_grade":False}
    plan["plan_hash"]=buyer_plan._digest(plan)
    return _bounded(plan)


def verify_plan(plan):
    if not isinstance(plan,dict) or plan!=build_plan(plan["spec"]):
        fail("frozen plan or scientific definitions changed")
    return plan


def exchange(plan):
    verify_plan(plan)
    b=plan["spec"]["rule_bundle"]
    # Only observable data and available documents. Do not reveal hidden
    # worlds, reference verdicts, probe labels or the authored check closure.
    items=[]
    for item in plan["items"]:
        case=item["case"]
        available=set(case["available_source_ids"])
        items.append({"slot":item["slot"],"patient_snapshot":{"captured_at":case["captured_at"],
                     "facts":case["observations"]},"proposed_action":b["action"],
                     "sources":[s for s in b["sources"] if s["id"] in available],
                     "fact_labels":{f["id"]:f["label"] for f in b["facts"]}})
    return {"schema":"decision-certifiability-exchange/1","plan_hash":plan["plan_hash"],
            "target":plan["spec"]["target"],"items":items,
            "instructions":"Assess only the specified action. Provide a certificate, verdict, and requested fact IDs; do not invent patient facts or sources."}


def response_template(plan):
    verify_plan(plan)
    return {"schema":"decision-certifiability-responses/1","plan_hash":plan["plan_hash"],
            "target":plan["spec"]["target"],
            "responses":[{"slot":i["slot"],"status":"","raw_response":"","verdict":"",
                          "certificate":None,"requested_facts":[]} for i in plan["items"]]}


def _candidate_check(certificate,reference,claimed):
    # The candidate's certificate is not a trusted description of the case.
    # Exact canonical equality for the required content prevents invented facts,
    # changed actions, weakened effects, forged provenance and missing checks.
    expected=reference["certificate"]
    if not isinstance(certificate,dict):
        return {"verdict":None,"closure_verified":False,"findings":["missing_certificate"]}
    differences=[]
    required=set(expected)-{"certificate_id"}
    if set(certificate)-({"certificate_id","claimed_verdict"}|required):
        differences.append("unexpected_certificate_fields")
    if not isinstance(certificate.get("certificate_id"),str) or not certificate["certificate_id"].strip():
        differences.append("invalid_certificate_id")
    if certificate.get("claimed_verdict",claimed)!=claimed:
        differences.append("inconsistent_claimed_verdict")
    for key in sorted(required):
        # Checklist order is irrelevant, but duplicates, changed metadata or
        # additional patient facts are not. JSON canonical bytes preserve types.
        actual=certificate.get(key)
        expected_value=expected[key]
        if key in ("evidence_bundle","support","critical_questions","contraindications") and isinstance(actual,list):
            actual=sorted(actual,key=buyer_plan._canonical)
            expected_value=sorted(expected_value,key=buyer_plan._canonical)
        if buyer_plan._canonical(actual)!=buyer_plan._canonical(expected_value):
            differences.append("changed_or_missing_"+key)
    try:
        verified=v.verify_certificate(certificate)
        structural=verified.verdict
        findings=[f.code for f in verified.findings]
    except (ValueError,TypeError,KeyError,AttributeError):
        structural=None
        findings=["malformed_certificate"]
    return {"verdict":structural,"closure_verified":not differences and structural==reference["verification"]["verdict"],
            "findings":differences+findings}


def assess(plan,responses):
    """Descriptive structured-output evaluation; no semantic extraction claim."""
    verify_plan(plan)
    _bounded(responses)
    _strict(responses,("schema","plan_hash","target","responses"))
    if (responses["schema"]!="decision-certifiability-responses/1" or responses["plan_hash"]!=plan["plan_hash"]
            or responses["target"]!=plan["spec"]["target"]):
        fail("response envelope differs from frozen plan")
    expected={i["slot"]:i for i in plan["items"]}
    rows=responses["responses"]
    if not isinstance(rows,list) or len(rows)!=len(expected):
        fail("every planned case requires an explicit outcome")
    seen=set()
    output=[]
    for row in rows:
        _strict(row,("slot","status","raw_response","verdict","certificate","requested_facts"))
        slot=row["slot"]
        if slot not in expected or slot in seen or row["status"] not in OUTCOMES:
            fail("unknown or duplicate response outcome")
        seen.add(slot)
        item=expected[slot]
        ref=item["reference"]
        expected_verdict=ref["verification"]["verdict"]
        rec={"slot":slot,"probe":item["case"]["probe"],"status":row["status"],
             "reference_verdict":expected_verdict,"candidate_verdict":None,
             "certificate_check":None,"false_certification":None,"missed_block":None,
             "unnecessary_defer":None,"verdict_agreement":None,
             "information_sufficient":None,"information_efficiency":None,
             "requested_cost":None,"minimum_cost":ref["minimum_cost"],
             "unknown_facts_requested":None}
        if row["status"]!="complete":
            if row["verdict"]!="" or row["certificate"] is not None or row["requested_facts"]:
                fail("failed extraction cannot contain scored structured results")
            if row["status"]=="extraction_failure":
                _text(row["raw_response"],40000)
            elif row["raw_response"]!="":
                fail("failed transport cannot contain a scored response")
        else:
            _text(row["raw_response"],40000)
            if row["verdict"] not in VERDICTS:
                fail("completed response requires declared candidate verdict")
            requested=row["requested_facts"]
            valid_ids={f["id"] for f in plan["spec"]["rule_bundle"]["facts"]}
            if (not isinstance(requested,list) or any(q not in valid_ids for q in requested)
                    or len(requested)!=len(set(requested))):
                fail("requested facts must be unique declared IDs")
            certificate=_candidate_check(row["certificate"],ref,row["verdict"])
            actual=row["verdict"]
            rec.update(candidate_verdict=actual,certificate_check=certificate,
                       false_certification=actual==v.CERTIFIED and
                           (expected_verdict!=v.CERTIFIED or not certificate["closure_verified"]),
                       missed_block=expected_verdict==v.BLOCK and actual!=v.BLOCK,
                       unnecessary_defer=expected_verdict==v.CERTIFIED and actual==v.DEFER,
                       verdict_agreement=actual==expected_verdict)
            # Minimum information is about differentiating permitted from
            # prohibited worlds, NOT about source retrieval or clinical utility.
            if (expected_verdict==v.DEFER and ref["source_closure"]
                    and ref["classification"]==mmip.ACTION_UNDERDETERMINED):
                selected=[q for q in requested if q in ref["unknown_facts"]]
                sufficient=mmip.is_decision_determining(ref["worlds"],selected)
                costs={f["id"]:f["cost"] for f in plan["spec"]["rule_bundle"]["facts"]}
                cost=sum(costs[q] for q in requested)
                rec.update(information_sufficient=sufficient,requested_cost=cost,
                           unknown_facts_requested=len(selected),
                           information_efficiency=ref["minimum_cost"]/cost if sufficient and cost and ref["minimum_cost"] is not None else None)
        output.append(rec)
    output.sort(key=lambda x:x["slot"])
    summary={"planned":len(output),"completed":sum(r["status"]=="complete" for r in output),
             "failed":sum(r["status"]!="complete" for r in output),
             "extraction_failures":sum(r["status"]=="extraction_failure" for r in output)}
    for metric,eligible in (
        ("false_certification",lambda r:r["candidate_verdict"]==v.CERTIFIED),
        ("missed_block",lambda r:r["reference_verdict"]==v.BLOCK),
        ("unnecessary_defer",lambda r:r["reference_verdict"]==v.CERTIFIED),
        ("verdict_agreement",lambda r:True),
        ("information_sufficient",lambda r:r["reference_verdict"]==v.DEFER and r["probe"] in ("missing","stale"))):
        subset=[r for r in output if eligible(r)]
        assessed=[r[metric] for r in subset if r[metric] is not None]
        summary[metric]={"eligible":len(subset),"assessed":len(assessed),"unassessed":len(subset)-len(assessed),
                         "count":sum(assessed),"rate_on_assessed":sum(assessed)/len(assessed) if assessed else None}
    summary["certificate_closure_verified"]=sum(bool(r["certificate_check"] and r["certificate_check"]["closure_verified"]) for r in output)
    result={"schema":"decision-certifiability-report/1","plan_hash":plan["plan_hash"],
            "response_hash":buyer_plan._digest(responses),"target":plan["spec"]["target"],
            "synthetic":plan["spec"]["synthetic"],"summary":summary,"rows":output,
            "decision_grade":False,"clinical_validity":"UNVERIFIED",
            "limitations":["Rules, observation freshness and finite fact domains are authored assumptions.",
                "Raw responses are supplied; structured extraction is not independently validated.",
                "A matching certificate is conditional on this exact rule bundle, not clinical permission.",
                "Minimum-information costs are authored relative units, not healthcare costs.",
                "No automated clinical action, family maturity promotion or deployment approval."]}
    result["digest"]=buyer_plan._digest(result)
    return result
