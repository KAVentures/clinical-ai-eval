"""Hospital-owned gate review and referral policy; never authorizes deployment.

The policy is registered before programme source association, not necessarily
before native evaluation exposure. Human attestations are not verified external
evidence. This deliberately does not implement automated clinical acceptance.
"""
from . import diagnostic_imaging as d

MANDATORY=("clinical_safety","clinical_effectiveness","privacy_security",
           "regulatory_quality","workflow_human_factors","monitoring_incident_response")
DISPOSITIONS=("stop","hold","refer_external_assessment")
VERDICTS=("attested_met","not_met","unresolved")
MAX_GATES=16
LIMITATIONS=[
    "Administrative referral policy, not clinical acceptance or deployment authorization.",
    "Native source availability and internal-regression claims do not establish clinical effectiveness or safety.",
    "Gate reviewers attest external evidence; artifact digests, rationale, credentials and independence are not externally verified.",
    "Policy registration precedes programme source association, not necessarily native evaluation or candidate exposure.",
    "Unplanned canonical requirements, missing or changed sources, weak canonical claims, and unresolved human gates block referral.",
]


def _text(x,n):
    return isinstance(x,str) and bool(x.strip()) and len(x.encode("utf-8"))<=n


def policy(programme,spec):
    d._keys(spec,"policy_id programme_id vendor_id intended_use gates")
    d._require(d._token(spec["policy_id"]) and spec["programme_id"]==programme["programme_id"]
               and d._token(spec["vendor_id"]) and _text(spec["intended_use"],2000),
               "invalid policy scope")
    plans=[p for p in programme["plans"] if p["vendor_id"]==spec["vendor_id"]]
    d._require(bool(plans),"registered vendor scope required")
    pids={p["plan_id"] for p in plans}
    reqs=[r for r in programme["requirements"] if r["plan_id"] in pids]
    d._require(bool(reqs),"nonempty vendor requirements")
    gates=spec["gates"]
    d._require(isinstance(gates,list) and len(MANDATORY)<=len(gates)<=MAX_GATES,
               "mandatory gate inventory required")
    seen=set()
    reviewers=set()
    for r in gates:
        d._keys(r,"id reviewer criterion")
        d._require(d._token(r["id"]) and r["id"] not in seen and d._token(r["reviewer"])
                   and _text(r["criterion"],1500),"invalid gate or reviewer")
        seen.add(r["id"])
        reviewers.add(r["reviewer"])
    d._require(set(MANDATORY)<=seen and len(reviewers)>=2,"all mandatory gates and two distinct reviewers required")
    return d._sealed({"schema":"hospital-referral-policy/1","programme_digest":programme["digest"],
                      "spec":{**spec,"gates":sorted([dict(r) for r in gates],key=lambda r:r["id"])},
                      "target":dict(plans[0]["target"]),"plan_ids":sorted(pids),
                      "requirements":reqs,"canonical_gaps":[r for r in programme["unplanned_canonical_suites"] if r["plan_id"] in pids],
                      "decision_grade":False,"limitations":LIMITATIONS[:]})


def snapshot(p,status):
    doc=status["programme"]
    d._require(policy(doc,p["spec"])==p,"policy/programme scope changed")
    rows=[r for r in status["requirements"] if r["requirement"]["plan_id"] in p["plan_ids"]]
    d._require([r["requirement"] for r in rows]==p["requirements"],"policy requirement inventory mismatch")
    conditions=[]
    for r in rows:
        req=r["requirement"]
        source=r["locked_source"]
        available=r["state"]=="source_available" and source is not None and status["state"]=="current"
        # Native text/conversation jobs currently cap authority at internal
        # regression. Rehearsals never count as clinical-family evidence.
        canonical=req["family"] is not None
        adequate=available and (not canonical or (
            req["kind"] in ("text","conversation") and source["mode"]=="internal_regression"
            and source["claim"]=="internal_regression" and source["scope"]=="exact_buyer_plan"))
        if req["kind"]=="imaging" and available:
            adequate=adequate and source.get("complete_roster") is True
        conditions.append({"requirement_id":req["id"],"kind":req["kind"],"family":req["family"],
                           "state":r["state"],"source":source,"administrative_condition_met":bool(adequate)})
    return d._sealed({"schema":"hospital-referral-review-snapshot/1","policy_digest":p["digest"],
                      "programme_state":status["state"],"conditions":conditions,
                      "canonical_gaps":p["canonical_gaps"],
                      "evidence_conditions_met":status["state"]=="current" and not p["canonical_gaps"] and
                          all(r["administrative_condition_met"] for r in conditions),
                      "canonical_requirements_satisfied":False,"decision_grade":False})


def rating(p,snap,reviewer,doc):
    d._keys(doc,"snapshot_digest gate_id verdict artifact_digest rationale")
    matches=[r for r in p["spec"]["gates"] if r["id"]==doc["gate_id"] and r["reviewer"]==reviewer]
    d._require(len(matches)==1 and doc["snapshot_digest"]==snap["digest"] and doc["verdict"] in VERDICTS
               and _text(doc["rationale"],3000),"assigned exact gate review required")
    d._require(doc["artifact_digest"] is None or d._hex(doc["artifact_digest"]),"invalid external artifact digest")
    d._require(doc["verdict"]!="attested_met" or doc["artifact_digest"] is not None,
               "met gate requires external evidence digest")
    return d._sealed({"schema":"hospital-referral-gate-review/1","policy_digest":p["digest"],
                      "reviewer":reviewer,"rating":dict(doc),"external_artifact_verified":False})


def evaluate(p,snap,ratings):
    d._require(snap["policy_digest"]==p["digest"],"snapshot policy mismatch")
    by={}
    for r in ratings:
        d._require(rating(p,snap,r["reviewer"],r["rating"])==r,"gate rating differs from replay")
        gid=r["rating"]["gate_id"]
        d._require(gid not in by,"duplicate gate review")
        by[gid]=r
    rows=[{"gate_id":gate["id"],"state":by[gate["id"]]["rating"]["verdict"] if gate["id"] in by else "not_reviewed",
           "review_digest":by[gate["id"]]["digest"] if gate["id"] in by else None}
          for gate in p["spec"]["gates"]]
    return d._sealed({"schema":"hospital-referral-evaluation/1","policy_digest":p["digest"],
                      "snapshot_digest":snap["digest"],"gates":rows,
                      "evidence_conditions_met":snap["evidence_conditions_met"],
                      "referral_conditions_met":snap["evidence_conditions_met"] and all(r["state"]=="attested_met" for r in rows),
                      "decision_grade":False,"deployment_authorized":False})


def disposition(p,snap,ratings,kind,rationale):
    d._require(kind in DISPOSITIONS and _text(rationale,4000),"explicit disposition and rationale required")
    result=evaluate(p,snap,ratings)
    d._require(kind!="refer_external_assessment" or result["referral_conditions_met"],
               "mandatory referral conditions not met")
    return d._sealed({"schema":"hospital-referral-disposition/1","evaluation":result,
                      "disposition":kind,"rationale":rationale.strip(),
                      "clinical_acceptance":False,"deployment_authorized":False,"decision_grade":False})


def package(programme,status,p,snap,ratings,decision):
    d._require(status["programme"]==programme and policy(programme,p["spec"])==p
               and snapshot(p,status)==snap,"exact reviewed programme snapshot required")
    d._require(disposition(p,snap,ratings,decision["disposition"],decision["rationale"])==decision,
               "disposition differs from replay")
    return d._sealed({"schema":"hospital-referral-evidence/1","programme_coverage":status,
                      "policy":p,"snapshot":snap,"ratings":sorted(ratings,key=lambda r:r["rating"]["gate_id"]),
                      "disposition":decision,"decision_grade":False})


def verify(doc,expected_digest=None):
    # Verify structure and arithmetic; private native source files and
    # external reviewer artifacts cannot be independently checked here.
    from . import unified_assessment as u
    coverage=doc["programme_coverage"]
    u.verify_export(u._package(coverage))
    rebuilt=package(coverage["programme"],coverage,doc["policy"],doc["snapshot"],doc["ratings"],doc["disposition"])
    d._require(rebuilt==doc and (expected_digest is None or expected_digest==doc["digest"]),"referral replay mismatch")
    return {"status":"STRUCTURE_REPLAY_VERIFIED","digest":doc["digest"],
            "expected_digest_matched":expected_digest is not None,"source_records_reverified":False,
            "external_artifacts_verified":False,"decision_grade":False,"deployment_authorized":False}


def configure(parser):
    parser.add_argument("evidence")
    parser.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    import json
    try:
        print(json.dumps(verify(d._read(args.evidence),args.expected_digest),allow_nan=False))
    except (ValueError,RuntimeError,KeyError,TypeError,OSError,AttributeError,RecursionError):
        raise SystemExit("Hospital referral evidence verification refused.")
