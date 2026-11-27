"""Matched, descriptive research comparisons, separate from procurement authority.

Compare exact protocols and fixed clinical opportunities, not candidate-dependent
claim counts. No ranking, statistical significance, or clinical acceptance.
"""
from __future__ import annotations

import uuid

from . import (institution_research as studio, institution_access as access,
               buyer_plan, buyer_manual as manual, formal_review as formal,
               research_collection as collection, manifest)

SCHEMA="matched-research-comparison/1"
MAX_COMPARISONS=100
MAX_TARGETS=5


def _fail(message):
    raise access.AccessDenied(message)


def _schema(conn):
    collection._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS research_comparisons(
        id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id),
        digest TEXT NOT NULL, payload TEXT NOT NULL)""")


def _index(rows,key,expected):
    if not isinstance(rows,list) or len(rows)!=len(expected):
        _fail("report unit count differs from protocol")
    indexed={}
    for row in rows:
        ident=row[key]
        if ident not in expected or ident in indexed:
            _fail("unexpected or duplicate report unit")
        indexed[ident]=row
    return indexed


def _bool(value):
    if value is not None and type(value) is not bool:
        _fail("unexpected endpoint value")
    return value


def _any_defect(statuses,good,bad):
    # A known defect can establish presence. Absence requires complete
    # determinate coverage and at least one opportunity. No claims != safe.
    if any(s in bad for s in statuses):
        return True
    if statuses and all(s in good for s in statuses):
        return False
    return None


def _endpoints(kind,plan,report):
    """Return named endpoints with fixed unit inventories and bool/None values."""
    metrics={}
    def put(name,unit,value):
        bucket=metrics.setdefault(name,{})
        if unit in bucket:
            _fail("duplicate matched endpoint")
        bucket[unit]=_bool(value)
    if kind=="citation":
        expected={q["query_id"]:q for q in plan["queries"]}
        rows=_index(report["rows"],"query_id",expected)
        for key,q in expected.items():
            row=rows[key]
            if (row["probe"],row["case_id"])!=(q["probe"],q["case_id"]):
                _fail("citation report differs from probe inventory")
            prefix=q["probe"]+"/"
            complete=row["status"]=="complete"
            put(prefix+"response_completed",key,complete)
            assessed=complete and row["coverage_verified"] is True
            n,good,bad,unknown=(row[k] for k in ("n_claims","supported","unsupported","unassessed"))
            if any(type(x) is not int or x<0 for x in (n,good,bad,unknown)) or good+bad+unknown!=n:
                _fail("citation claim counts inconsistent")
            put(prefix+"any_unsupported_claim",key,
                (True if bad else False if n and good==n else None) if assessed else None)
    elif kind=="scribe":
        expected={i["slot"]:i for i in plan["items"]}
        rows=_index(report["rows"],"slot",expected)
        for key,item in expected.items():
            row=rows[key]
            complete=row["outcome"]=="complete"
            put("response_completed",key,complete)
            facts={f["fact_id"]:f for f in item["reference_facts"]}
            observed=_index(row["facts"],"fact_id",facts)
            for fid,f in facts.items():
                v=observed[fid]
                if v["importance"]!=f["importance"]:
                    _fail("reference fact importance changed")
                unit=key+"/"+fid
                status=v["status"]
                eligible=(complete and row["reference_coverage_verified"] is True and
                          v["reference_validity"]=="valid" and
                          status in ("documented","omitted","contradicted"))
                value=status=="omitted" if eligible else None
                put("reference_fact_omitted",unit,value)
                put("reference_fact_contradicted",unit,status=="contradicted" if eligible else None)
                if f["importance"]=="critical":
                    put("critical_reference_fact_omitted",unit,value)
            claims=[c["status"] for c in row["claims"]]
            put("any_documentation_defect",key,
                _any_defect(claims,("supported",),("unsupported","contradicted"))
                if complete and row["claim_coverage_verified"] is True else None)
    elif kind=="decision":
        expected={i["slot"]:i for i in plan["items"]}
        rows=_index(report["rows"],"slot",expected)
        for key,item in expected.items():
            row=rows[key]
            ref=item["reference"]["verification"]["verdict"]
            if row["reference_verdict"]!=ref or row["probe"]!=item["case"]["probe"]:
                _fail("decision report differs from reference")
            complete=row["status"]=="complete"
            put("structured_response_completed",key,complete)
            put("verdict_agreement",key,row["verdict_agreement"] if complete else None)
            if ref!=studio.decision.v.CERTIFIED:
                put("unsafe_reference_certification",key,
                    row["unsafe_reference_certification"] if complete else None)
            if ref==studio.decision.v.BLOCK:
                put("missed_block",key,row["missed_block"] if complete else None)
            if ref==studio.decision.v.CERTIFIED:
                put("unnecessary_defer",key,row["unnecessary_defer"] if complete else None)
            if (ref==studio.decision.v.DEFER and row["probe"] in ("missing","stale")):
                put("information_sufficient",key,row["information_sufficient"] if complete else None)
    elif kind=="robustness":
        expected={p["pair_id"]:p for p in plan["pairs"]}
        rows=_index(report["pairs"],"pair_id",expected)
        for key,p in expected.items():
            row=rows[key]
            if any(row[k]!=p[k] for k in ("case_id","transform","relation")):
                _fail("robustness report differs from pair inventory")
            prefix=p["transform"]+"/"
            complete=all(row[k]["outcome"]=="complete" for k in ("baseline","variant"))
            put(prefix+"pair_completed",key,complete)
            valid=row["reference_validity"]=="valid"
            axes=("baseline_unsupported_inference","variant_unsupported_inference",
                  "variant_unsafe_response")
            axes+=(("changed_context_success",) if p["relation"]=="changed" else
                   ("decision_group_flip","both_acceptable","adverse_degradation",
                    "new_unsupported_inference","new_unsafe_response"))
            for axis in axes:
                put(prefix+axis,key,row[axis] if valid else None)
    else:
        _fail("unsupported comparison type")
    return metrics


def matched(left,right):
    """Fixed denominator, with explicit four-way paired missingness."""
    if set(left)!=set(right):
        _fail("matched clinical unit inventories differ")
    n=len(left)
    a_only=b_only=neither=both=0
    yy=yn=ny=nn=0
    for unit,a in left.items():
        b=right[unit]
        _bool(a); _bool(b)
        if a is None and b is None:
            neither+=1
        elif a is None:
            b_only+=1
        elif b is None:
            a_only+=1
        else:
            both+=1
            if a and b:
                yy+=1
            elif a:
                yn+=1
            elif b:
                ny+=1
            else:
                nn+=1
    return {"planned_units":n,"both_assessed":both,"baseline_only_assessed":a_only,
            "candidate_only_assessed":b_only,"neither_assessed":neither,
            "both_event":yy,"baseline_event_only":yn,"candidate_event_only":ny,
            "neither_event":nn,
            "baseline_rate_on_matched":(yy+yn)/both if both else None,
            "candidate_rate_on_matched":(yy+ny)/both if both else None,
            "candidate_minus_baseline":(ny-yn)/both if both else None}


def _source(directory,conn,actor,identifier):
    row=studio._row(conn,identifier)
    studio._manager(conn,actor,row)
    root,mod,plan=studio._plan(directory,row)
    report=studio._report(directory,conn,row)
    target=plan["spec"]["target"]
    # The spec is regenerated and checked by the module. Only target metadata
    # may differ: all prompts, references, definitions and case ordering match.
    protocol={"kind":row["kind"],"spec":{k:v for k,v in plan["spec"].items() if k!="target"},
              "definitions":plan["definitions"]}
    roster=[(x["reviewer"],x["role"]) for x in conn.execute(
        "SELECT reviewer,role FROM research_reviewers WHERE study=? ORDER BY reviewer",(identifier,))]
    job=conn.execute("SELECT state FROM research_collections WHERE study=?",(identifier,)).fetchone()
    mode="supervised_local" if job else "operator_supplied"
    receipt_hash=None
    if job:
        receipt_hash=collection.receipt(directory,conn,row)["receipt_hash"]
    public={"study":identifier,"target":target,"plan_hash":row["plan_hash"],
            "report_hash":row["report_hash"],"response_hash":row["response_hash"],
            "collection_mode":mode,"receipt_hash":receipt_hash}
    return public,protocol,roster,_endpoints(row["kind"],plan,report),report["summary"]


def _build(directory,conn,actor,identifiers):
    studio._actor(conn,actor,"admin","operator")
    if not isinstance(identifiers,list) or not 2<=len(identifiers)<=MAX_TARGETS:
        _fail("select two to five analyzed studies")
    ids=[manual._identifier(i) for i in identifiers]
    if len(set(ids))!=len(ids):
        _fail("duplicate study")
    sources=[_source(directory,conn,actor,i) for i in ids]
    base,protocol,roster,metrics,_=sources[0]
    targets=set()
    for public,p,r,axes,_ in sources:
        if p!=protocol or r!=roster or public["collection_mode"]!=base["collection_mode"]:
            _fail("comparison requires identical protocol, reviewer roster and collection mode")
        target=buyer_plan._digest(public["target"])
        if target in targets:
            _fail("select distinct target name/version pairs")
        targets.add(target)
        if set(axes)!=set(metrics):
            _fail("endpoint inventory differs")
    comparisons=[]
    for public,_,_,axes,_ in sources[1:]:
        comparisons.append({"candidate_study":public["study"],
                            "metrics":{k:matched(metrics[k],axes[k]) for k in sorted(metrics)}})
    result={"schema":SCHEMA,"kind":protocol["kind"],"protocol_hash":buyer_plan._digest(protocol),
            "comparison_definition_hash":manifest.hash_file(manifest.definition_file("caeval/research_comparison.py")),
            "synthetic":protocol["spec"]["synthetic"],"baseline_study":ids[0],
            "sources":[s[0] for s in sources],"reviewer_count":len(roster),
            "same_review_roster":True,"comparisons":comparisons,
            "marginal_summaries":[{"study":s[0]["study"],"summary":s[4]} for s in sources],
            "decision_grade":False,"claim_authority":"EXPLORATORY_ONLY",
            "limitations":["Retrospective comparison of exactly matched authored protocols; no preregistration implied.",
                "Matched rates condition on both studies having determinate results. Missingness is not a negative finding.",
                "Candidate-dependent claim counts are marginal summaries only; claims are not matched between products.",
                "Some endpoints are events, others are favorable outcomes; delta is candidate minus baseline, not a quality ranking.",
                "Shared cases and reviewers induce dependence; no significance, equivalence, population inference or procurement decision.",
                "Identical reviewer identities and collection mode do not prove independent execution or eliminate bias."]}
    result["digest"]=buyer_plan._digest(result)
    return result


def create(directory,actor,identifiers,*,confirmed=False):
    if confirmed is not True:
        _fail("explicit descriptive comparison approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        result=_build(directory,conn,actor,identifiers)
        if conn.execute("SELECT count(*) FROM research_comparisons").fetchone()[0]>=MAX_COMPARISONS:
            _fail("comparison limit reached")
        identifier="comparison-"+uuid.uuid4().hex
        payload=buyer_plan._canonical(result)
        if len(payload)>manual.MAX_REQUEST_BYTES//2:
            _fail("comparison size limit")
        conn.execute("INSERT INTO research_comparisons VALUES(?,?,?,?)",
                     (identifier,actor["user_id"],result["digest"],payload.decode("utf-8")))
        access._audit(conn,actor["user_id"],"research-comparison-created",identifier)
        return identifier


def _load(directory,conn,actor,row):
    studio._actor(conn,actor,"admin","operator")
    if actor["role"]!="admin" and actor["user_id"]!=row["owner"]:
        _fail("comparison owner or administrator required")
    doc=manual._parse(row["payload"].encode("utf-8"))
    if doc["digest"]!=row["digest"] or buyer_plan._digest({k:v for k,v in doc.items() if k!="digest"})!=row["digest"]:
        _fail("stored comparison changed")
    # Rebuild from current verified underlying studies, not from cached scores.
    if _build(directory,conn,actor,[s["study"] for s in doc["sources"]])!=doc:
        _fail("comparison source or definitions changed")
    return doc


def download(directory,actor,identifier):
    with access.db(directory) as conn:
        _schema(conn)
        studio._actor(conn,actor,"admin","operator")
        row=conn.execute("SELECT * FROM research_comparisons WHERE id=?",(manual._identifier(identifier),)).fetchone()
        if not row:
            _fail("unknown comparison")
        result=_load(directory,conn,actor,row)
        access._audit(conn,actor["user_id"],"research-comparison-exported",identifier)
        return result


def inventory(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        studio._actor(conn,actor,"admin","operator")
        rows=conn.execute("SELECT id,owner FROM research_comparisons ORDER BY rowid DESC LIMIT ?",(MAX_COMPARISONS,)).fetchall()
        return [r["id"] for r in rows if actor["role"]=="admin" or r["owner"]==actor["user_id"]]
