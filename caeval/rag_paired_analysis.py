"""Prospectively specified, paired cluster-weighted exploratory RAG contrasts.

One ordered candidate pair in one frozen study. Independent clusters are an
assumption, not a property established by identifiers. Hoeffding simultaneous
bounds require independent bounded cluster observations; no normal approximation,
bootstrap degeneracy, or independence assumption for questions within clusters.
"""
from fractions import Fraction
import math
from . import diagnostic_imaging as d, rag_grounding as g

ENDPOINTS=("reference_hit","reviewed_grounded_answer","declared_abstention","paired_availability")
MIN_CLUSTERS=10
LIMITATIONS=[
    "Within-study comparison of exact declared candidate identities only; no cross-study or automatic version equivalence.",
    "All endpoints are exploratory proxies, not clinical correctness or clinical safety.",
    "Equal-cluster weighting: each predeclared cluster contributes equally, regardless of question count.",
    "Simultaneous Hoeffding bounds assume independent cluster observations and target the mean of their expectations; no sampling representativeness is established.",
    "Missing or unresolved endpoint values receive worst/best binary bounds; complete-pair estimates are descriptive only.",
    "An endpoint threshold is a research signal, not procurement acceptance, regulatory approval or deployment authorization.",
]


def protocol(plan,spec):
    g.verify_plan(plan)
    d._keys(spec,"baseline candidate primary_endpoint alpha_bp margin_bp max_unknown_bp min_clusters independent_clusters_attested")
    d._require(d._token(spec["baseline"]) and d._token(spec["candidate"]) and spec["baseline"]!=spec["candidate"],
               "one distinct ordered pair required")
    base=g._candidate(plan,spec["baseline"])
    cand=g._candidate(plan,spec["candidate"])
    d._require(spec["primary_endpoint"] in ENDPOINTS,"unknown primary endpoint")
    for key,low,high in (("alpha_bp",100,1000),("margin_bp",-10000,10000),
                         ("max_unknown_bp",0,10000),("min_clusters",MIN_CLUSTERS,g.MAX_CASES)):
        d._require(type(spec[key]) is int and low<=spec[key]<=high,"invalid analysis parameter")
    d._require(type(spec["independent_clusters_attested"]) is bool,"explicit cluster assumption required")
    return d._sealed({"schema":"rag-paired-analysis-plan/1","study_digest":plan["digest"],
                      "baseline":base,"candidate":cand,"parameters":dict(spec),"endpoints":list(ENDPOINTS),
                      "method":"equal_cluster_paired_hoeffding_bonferroni/1",
                      "decision_grade":False,"limitations":LIMITATIONS[:]})


def _verify_protocol(plan,analysis_plan):
    d._require(protocol(plan,analysis_plan["parameters"])==analysis_plan,"analysis plan differs from frozen protocol")


def _grounded(plan,q,row,reviewers):
    if row["status"]=="unavailable":
        return None
    if row["status"]!="answered":
        return 0
    hits={r["id"] for r in row["retrieved"]}
    if not set(q["reference_ids"])<=hits or not row["claims"]:
        return 0
    corpus={doc["id"]:doc for doc in plan["spec"]["documents"]}
    # Any deterministic citation defect is failure even if review is uncertain.
    for claim in row["claims"]:
        cited=set(claim["citations"])
        if not cited or not cited<=hits or any(corpus[x]["state"]!="current" for x in cited):
            return 0
    unit=d._digest([plan["private_nonce"],"review",row["slot"]])
    a,b=reviewers[0][unit],reviewers[1][unit]
    coverage=g._agree(a[0],b[0])
    labels=[g._agree(a[1][cid],b[1][cid]) for cid in
            [d._digest([plan["private_nonce"],row["slot"],i]) for i in range(len(row["claims"]))]]
    if coverage=="incomplete" or "unsupported" in labels:
        return 0
    if coverage!="complete" or "uncertain" in labels:
        return None
    return 1


def _values(evidence,cid,reviewers):
    plan=evidence["plan"]
    doc=next(x for x in evidence["traces"] if x["candidate"]["id"]==cid)
    cases=g._cases(plan,cid)
    by={}
    for row in doc["traces"]:
        q,condition=cases[row["slot"]]
        by.setdefault(q["id"],{})[condition]=row
    out={}
    for q in plan["spec"]["questions"]:
        pos=by[q["id"]]["supported"]
        neg=by[q["id"]]["support_removed"]
        out[q["id"]]={
            "reference_hit":None if pos["status"]=="unavailable" else int(bool(
                set(q["reference_ids"]) & {h["id"] for h in pos["retrieved"]})),
            "reviewed_grounded_answer":_grounded(plan,q,pos,reviewers),
            "declared_abstention":None if neg["status"]=="unavailable" else int(neg["status"]=="abstained"),
            "paired_availability":int(pos["status"]!="unavailable" and neg["status"]!="unavailable"),
        }
    return out


def _mean(values):
    return sum(values,Fraction(0))/len(values)


def _number(x):
    return None if x is None else float(x)


def _endpoint(questions,base,cand,endpoint,params):
    clusters={}
    counts={"n_questions":len(questions),"both_known":0,"baseline_unknown":0,"candidate_unknown":0,
            "candidate_only_success":0,"baseline_only_success":0,"both_success":0,"both_failure":0}
    for q in questions:
        a,b=base[q["id"]][endpoint],cand[q["id"]][endpoint]
        counts["baseline_unknown"]+=a is None
        counts["candidate_unknown"]+=b is None
        if a is not None and b is not None:
            counts["both_known"]+=1
            key={(0,1):"candidate_only_success",(1,0):"baseline_only_success",
                 (1,1):"both_success",(0,0):"both_failure"}[(a,b)]
            counts[key]+=1
        clusters.setdefault(q["cluster_id"],[]).append((a,b))
    lower=[];upper=[];unknown=[];complete=[]
    for rows in clusters.values():
        lower.append(_mean([(0 if b is None else b)-(1 if a is None else a) for a,b in rows]))
        upper.append(_mean([(1 if b is None else b)-(0 if a is None else a) for a,b in rows]))
        unknown.append(_mean([int(a is None or b is None) for a,b in rows]))
        resolved=[b-a for a,b in rows if a is not None and b is not None]
        if resolved:
            complete.append(_mean(resolved))
    n=len(clusters)
    lo,hi,unk=_mean(lower),_mean(upper),_mean(unknown)
    sufficient=n>=params["min_clusters"] and params["independent_clusters_attested"]
    interval=None
    radius=None
    if sufficient:
        # For variables in [-1,1], one-sided Hoeffding radius is
        # sqrt(2 log(1/delta)/n). Union bound over both ends of all four
        # endpoints, including the lower and upper missingness envelopes.
        delta=params["alpha_bp"]/10000/(2*len(ENDPOINTS))
        radius=math.sqrt(2*math.log(1/delta)/n)
        # Outward rounding preserves conservative bounds.
        interval=[max(-1.0,math.nextafter(float(lo)-radius,-math.inf)),
                  min(1.0,math.nextafter(float(hi)+radius,math.inf))]
    return {"endpoint":endpoint,"counts":counts,"n_clusters":n,"n_clusters_with_known_pairs":len(complete),
            "complete_pair_cluster_mean_difference":_number(_mean(complete) if complete else None),
            "observed_difference_bounds":[float(lo),float(hi)],
            "unknown_pair_cluster_fraction":float(unk),
            "unknown_pair_limit_met":unk<=Fraction(params["max_unknown_bp"],10000),
            "simultaneous_confidence_interval":interval,"hoeffding_radius":radius,
            "inference_status":"bounded_under_assumption" if sufficient else "withheld_insufficient_clusters_or_assumption"}


def analyse(evidence,analysis_plan):
    g.verify(evidence)
    plan=evidence["plan"]
    _verify_protocol(plan,analysis_plan)
    params=analysis_plan["parameters"]
    ids=plan["spec"]["reviewers"]
    packet=g.review_packet(plan,evidence["traces"],ids[0])
    reviewers=[g._review(packet,evidence["reviews"][uid]) for uid in ids]
    base=_values(evidence,params["baseline"],reviewers)
    cand=_values(evidence,params["candidate"],reviewers)
    endpoints=[_endpoint(plan["spec"]["questions"],base,cand,name,params) for name in ENDPOINTS]
    primary=next(r for r in endpoints if r["endpoint"]==params["primary_endpoint"])
    ci=primary["simultaneous_confidence_interval"]
    margin=params["margin_bp"]/10000
    if not primary["unknown_pair_limit_met"]:
        signal="withheld_unknown_pair_limit"
    elif ci is None:
        signal="withheld_inference"
    elif ci[0]>margin:
        signal="lower_bound_strictly_above_margin"
    elif ci[1]<margin:
        signal="upper_bound_strictly_below_margin"
    else:
        signal="inconclusive"
    return d._sealed({"schema":"rag-paired-analysis/1","analysis_plan":analysis_plan,
                      "study_evidence_digest":evidence["digest"],"endpoints":endpoints,
                      "primary_research_signal":signal,"decision_grade":False,
                      "deployment_authorized":False,"limitations":LIMITATIONS[:]})


def verify(evidence,result,expected_digest=None):
    rebuilt=analyse(evidence,result["analysis_plan"])
    d._require(rebuilt==result and (expected_digest is None or expected_digest==rebuilt["digest"]),
               "paired analysis replay mismatch")
    return {"status":"REPLAY_VERIFIED","digest":rebuilt["digest"],"expected_digest_matched":expected_digest is not None,
            "prospective_timing_independently_verified":False,"decision_grade":False}


def configure(parser):
    sub=parser.add_subparsers(dest="comparison_action",required=True)
    p=sub.add_parser("verify")
    p.add_argument("study_evidence")
    p.add_argument("analysis")
    p.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    import json
    try:
        print(json.dumps(verify(d._read(args.study_evidence),d._read(args.analysis),args.expected_digest),allow_nan=False))
    except (ValueError,RuntimeError,KeyError,TypeError,OSError,AttributeError,RecursionError):
        raise SystemExit("Paired RAG comparison verification refused.")
