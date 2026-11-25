"""Matched-cohort longitudinal research across separately frozen studies.

Same vendor, different version, exact cohort and reference protocol. No
inferential claim: reused historical cohorts may be selected, leaked or
dependent, and independent reviewer behavior can change between runs.
"""
from fractions import Fraction
from . import diagnostic_imaging as d, rag_grounding as g, rag_paired_analysis as rag

ENDPOINTS={
    "imaging":("resolved_accuracy","sensitivity","specificity","score_availability"),
    "rag_grounding":rag.ENDPOINTS,
}
LIMITATIONS=[
    "Historical matched-cohort descriptive comparison only; no population inference or clinical acceptance.",
    "Exact corpus, reference metadata and declared cohort equality do not establish external reference truth, execution or independence.",
    "Baseline results may already be known; repeated evaluation may introduce selection, learning, contamination and temporal confounding.",
    "Equal-cluster weighting; unknown outcomes contribute worst/best binary envelopes, not confidence intervals.",
    "The same reviewer identities do not guarantee identical or independent ratings across studies.",
    "Product version is declared, not independently attested; no procurement pass or deployment authorization.",
]


def _verify(kind,evidence):
    if kind=="imaging":
        d.verify(evidence)
        d._require(evidence["report"]["roster_complete"],"complete imaging roster required")
    elif kind=="rag_grounding":
        g.verify(evidence)
    else:
        raise ValueError("unknown longitudinal modality")


def _cohort(kind,plan):
    if kind=="imaging":
        d._verify_plan(plan)
    elif kind=="rag_grounding":
        g.verify_plan(plan)
    else:
        raise ValueError("unknown longitudinal modality")
    # Retain every frozen reference, cluster, corpus, question, task,
    # modality, stratum and attestation; only study and roster differ.
    return {k:v for k,v in plan["spec"].items() if k not in ("study_id","candidates")}


def _candidate(kind,plan,vid):
    return d._find_candidate(plan,vid) if kind=="imaging" else g._candidate(plan,vid)


def protocol(kind,baseline,successor_plan,spec):
    _verify(kind,baseline)
    d._keys(spec,"vendor_id endpoint margin_bp max_unknown_bp")
    d._require(d._token(spec["vendor_id"]) and spec["endpoint"] in ENDPOINTS[kind],"invalid longitudinal target")
    for k,low,high in (("margin_bp",-10000,10000),("max_unknown_bp",0,10000)):
        d._require(type(spec[k]) is int and low<=spec[k]<=high,"invalid research threshold")
    old=baseline["plan"]
    cohort=_cohort(kind,old)
    d._require(cohort==_cohort(kind,successor_plan),"exact matched cohort and reference protocol required")
    d._require(old["spec"]["study_id"]!=successor_plan["spec"]["study_id"]
               and old["private_nonce"]!=successor_plan["private_nonce"],"distinct studies and opaque exchanges required")
    a,b=(_candidate(kind,p,spec["vendor_id"]) for p in (old,successor_plan))
    d._require(a["version"]!=b["version"],"distinct declared product versions required")
    d._require([x for x in old["spec"]["candidates"] if x["id"]!=spec["vendor_id"]]==
               [x for x in successor_plan["spec"]["candidates"] if x["id"]!=spec["vendor_id"]],
               "other candidate identities and versions must remain unchanged")
    if kind=="imaging":
        d._require(a["threshold_bp"]==b["threshold_bp"],"changed decision threshold requires separately scoped design")
    return d._sealed({"schema":"matched-version-regression-plan/1","kind":kind,
                      "baseline_evidence_digest":baseline["digest"],
                      "successor_plan_digest":successor_plan["digest"],"cohort_digest":d._digest(cohort),
                      "baseline_study":old["spec"]["study_id"],"successor_study":successor_plan["spec"]["study_id"],
                      "baseline":a,"successor":b,"parameters":dict(spec),
                      "decision_grade":False,"limitations":LIMITATIONS[:]})


def _imaging(evidence,vid):
    plan=evidence["plan"]
    candidate=d._find_candidate(plan,vid)
    doc=next(r for r in evidence["submissions"] if r["candidate"]["id"]==vid)
    rows=d._predictions(plan,doc)
    out={}
    for slot,case in d._slots(plan).items():
        row=rows[slot]
        value=int(row["status"]=="scored")
        result={"score_availability":value}
        if case["reference"]!="unresolved":
            correct=d._correct(case,row,candidate["threshold_bp"])
            result["resolved_accuracy"]=correct
            result["sensitivity" if case["reference"]=="positive" else "specificity"]=correct
        out[case["case_id"]]=result
    return out


def _rag(evidence,vid):
    plan=evidence["plan"]
    ids=plan["spec"]["reviewers"]
    packet=g.review_packet(plan,evidence["traces"],ids[0])
    reviewers=[g._review(packet,evidence["reviews"][uid]) for uid in ids]
    return rag._values(evidence,vid,reviewers)


def _mean(xs):
    return sum(xs,Fraction(0))/len(xs)


def _endpoint(cases,old,new,name,params):
    groups={}
    for ident,cluster in cases:
        # Endpoint applicability must agree, not be silently intersected.
        d._require((name in old[ident])==(name in new[ident]),"endpoint applicability mismatch")
        if name in old[ident]:
            groups.setdefault(cluster,[]).append((old[ident][name],new[ident][name]))
    if not groups:
        return {"endpoint":name,"n_cases":0,"n_clusters":0,"n_known_pairs":0,
                "n_baseline_unknown":0,"n_successor_unknown":0,
                "observed_difference_bounds":None,"complete_pair_cluster_mean_difference":None,
                "unknown_pair_cluster_fraction":None,"unknown_pair_limit_met":False,"margin_relation":"empty"}
    lows=[];highs=[];unknown=[];complete=[]
    pairs=[x for group in groups.values() for x in group]
    for rows in groups.values():
        lows.append(_mean([(0 if b is None else b)-(1 if a is None else a) for a,b in rows]))
        highs.append(_mean([(1 if b is None else b)-(0 if a is None else a) for a,b in rows]))
        unknown.append(_mean([int(a is None or b is None) for a,b in rows]))
        known=[b-a for a,b in rows if a is not None and b is not None]
        if known:
            complete.append(_mean(known))
    lower,upper=_mean(lows),_mean(highs)
    margin=Fraction(params["margin_bp"],10000)
    relation="below" if upper<margin else "above" if lower>margin else "overlap"
    return {"endpoint":name,"n_cases":len(pairs),"n_clusters":len(groups),
            "n_known_pairs":sum(a is not None and b is not None for a,b in pairs),
            "n_baseline_unknown":sum(a is None for a,b in pairs),
            "n_successor_unknown":sum(b is None for a,b in pairs),
            "observed_difference_bounds":[float(lower),float(upper)],
            "margin_relation":relation,
            "complete_pair_cluster_mean_difference":float(_mean(complete)) if complete else None,
            "unknown_pair_cluster_fraction":float(_mean(unknown)),
            "unknown_pair_limit_met":_mean(unknown)<=Fraction(params["max_unknown_bp"],10000)}


def analyse(baseline,successor,analysis_plan):
    kind=analysis_plan["kind"]
    _verify(kind,successor)
    params=analysis_plan["parameters"]
    d._require(protocol(kind,baseline,successor["plan"],params)==analysis_plan,"regression plan replay mismatch")
    vid=params["vendor_id"]
    if kind=="imaging":
        cases=[(c["case_id"],c["cluster_id"]) for c in baseline["plan"]["spec"]["cases"]]
        a,b=_imaging(baseline,vid),_imaging(successor,vid)
    else:
        cases=[(q["id"],q["cluster_id"]) for q in baseline["plan"]["spec"]["questions"]]
        a,b=_rag(baseline,vid),_rag(successor,vid)
    d._require(set(a)==set(b)=={x for x,_ in cases},"matched case inventory mismatch")
    endpoints=[_endpoint(cases,a,b,name,params) for name in ENDPOINTS[kind]]
    primary=next(r for r in endpoints if r["endpoint"]==params["endpoint"])
    bounds=primary["observed_difference_bounds"]
    if bounds is None:
        signal="withheld_empty_endpoint"
    elif not primary["unknown_pair_limit_met"]:
        signal="withheld_unknown_pair_limit"
    elif primary["margin_relation"]=="below":
        signal="observed_upper_bound_below_margin"
    elif primary["margin_relation"]=="above":
        signal="observed_lower_bound_above_margin"
    else:
        signal="observed_bounds_overlap_margin"
    return d._sealed({"schema":"matched-version-regression/1","analysis_plan":analysis_plan,
                      "successor_evidence_digest":successor["digest"],"endpoints":endpoints,
                      "descriptive_research_signal":signal,"inference_performed":False,
                      "decision_grade":False,"deployment_authorized":False,"limitations":LIMITATIONS[:]})


def verify(baseline,successor,result,expected_digest=None):
    rebuilt=analyse(baseline,successor,result["analysis_plan"])
    d._require(rebuilt==result and (expected_digest is None or expected_digest==result["digest"]),
               "longitudinal evidence replay mismatch")
    return {"status":"REPLAY_VERIFIED","digest":result["digest"],"expected_digest_matched":expected_digest is not None,
            "prospective_timing_independently_verified":False,"decision_grade":False}


def configure(parser):
    parser.add_argument("baseline")
    parser.add_argument("successor")
    parser.add_argument("comparison")
    parser.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    import json
    try:
        print(json.dumps(verify(d._read(args.baseline),d._read(args.successor),d._read(args.comparison),
                                args.expected_digest),allow_nan=False))
    except (ValueError,RuntimeError,KeyError,TypeError,OSError,AttributeError,RecursionError):
        raise SystemExit("Matched longitudinal regression verification refused.")
