"""Controlled citation probes plus independently reviewed observed RAG traces.

The existing atomic scorer decides semantic support from independent labels.
This layer binds the denominator, actual answers and source spans BEFORE review.
No automatic extraction, clinical correctness or canonical claim promotion.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from . import atomic_citations as atomic, buyer_agent as buyer, buyer_manual as manual
from . import buyer_plan, buyer_research as research, formal_review as formal
from . import manifest, review_packets as packets
from .rag import vendor_trace

SCHEMA="controlled-citation-assessment/1"
PROBES=("baseline","citation_nonexistent","citation_superseded","citation_does_not_support")
MAX_CASES=50
MAX_SOURCES=200
MAX_CLAIMS=400
MAX_PAIRS=1000
MAX_TEXT=40000
DEFINITIONS=("caeval/atomic_citations.py","caeval/rag/vendor_trace.py",
             "caeval/citation_assessment.py","tests/citation_verification/family.yaml")
COVERAGE=("complete","incomplete","cannot_judge")
VERDICTS=("supports","contradicts","does_not_support","cannot_judge")


def fail(message):
    raise buyer.BuyerError(message)


def _text(value,limit=MAX_TEXT):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        fail("nonempty bounded text required")
    return value


def _id(value):
    return manual._identifier(value)


def _source_id(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",value):
        fail("invalid source identifier")
    return value


def _strict(obj,keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys):
        fail("unexpected or missing assessment fields")


def _bounded(obj):
    if len(buyer_plan._canonical(obj))>manual.MAX_REQUEST_BYTES//2:
        fail("assessment exceeds approved size")
    return obj


def _defs():
    definitions={p:manifest.hash_file(manifest.definition_file(p)) for p in DEFINITIONS}
    if not all(definitions.values()):
        fail("assessment definitions unavailable")
    return definitions


def build_plan(spec):
    """Four materially different, fixed, authored contexts per clinical question.

    The ghost probe explicitly requests a nonexistent identifier, rather than
    fabricating a supporting document. Source relevance is an authored hypothesis
    to be reviewed, not inferred by the software.
    """
    _strict(spec,("target","synthetic","sources","cases"))
    _strict(spec["target"],("name","version"))
    for value in spec["target"].values():
        _text(value,256)
    if type(spec["synthetic"]) is not bool:
        fail("explicit synthetic provenance required")
    sources=spec["sources"]
    if not isinstance(sources,list) or not 1<=len(sources)<=MAX_SOURCES:
        fail("bounded source catalog required")
    catalog={}
    for s in sources:
        _strict(s,("source_id","text","version","status"))
        sid=_source_id(s["source_id"])
        if sid in catalog or s["status"] not in atomic.SOURCE_STATUSES:
            fail("duplicate source or invalid currency")
        _text(s["text"])
        _text(s["version"],128)
        catalog[sid]=s
    cases=spec["cases"]
    if not isinstance(cases,list) or not 1<=len(cases)<=MAX_CASES:
        fail("bounded authored case inventory required")
    planned=[]
    seen=set()
    for case in cases:
        _strict(case,("case_id","question","current_source","superseded_source","distractor_source","ghost_source"))
        cid=_id(case["case_id"])
        if cid in seen:
            fail("duplicate case")
        seen.add(cid)
        question=_text(case["question"],8000)
        current,old,distractor,ghost=(_source_id(case[k]) for k in
            ("current_source","superseded_source","distractor_source","ghost_source"))
        if (len({current,old,distractor,ghost})!=4 or ghost in catalog
                or any(x not in catalog for x in (current,old,distractor))
                or catalog[current]["status"]!="current" or catalog[distractor]["status"]!="current"
                or catalog[old]["status"]!="superseded"
                or len({catalog[x]["text"].strip() for x in (current,old,distractor)})!=3):
            fail("distinct current, superseded, distractor and absent source required")
        for probe,sid in zip(PROBES,(current,ghost,old,distractor)):
            docs=[] if probe=="citation_nonexistent" else [catalog[sid]]
            context="\n\n".join("SOURCE "+d["source_id"]+" VERSION "+d["version"]+" STATUS "+d["status"]+"\n"+d["text"] for d in docs)
            prompt=("Answer the clinical question using only the supplied sources. "
                    "State limitations. Attach source identifiers to factual claims.\n\n"
                    "QUESTION:\n"+question+"\n\nREQUESTED SOURCE IDENTIFIER:\n"+sid+
                    "\n\nSOURCE MATERIAL:\n"+(context or "(no document available)"))
            planned.append({"query_id":buyer_plan._digest([cid,probe])[:32],
                "case_id":cid,"probe":probe,"query":prompt,"source_ids":[d["source_id"] for d in docs],
                "reference_supporting_source_ids":[current] if probe=="baseline" else [],
                "prompt_hash":buyer_plan._digest(prompt)})
    plan={"schema":SCHEMA,"spec":spec,"queries":planned,"definitions":_defs(),
          "decision_grade":False}
    plan["plan_hash"]=buyer_plan._digest(plan)
    return _bounded(plan)


def verify_plan(plan):
    if not isinstance(plan,dict) or plan!=build_plan(plan["spec"]):
        fail("frozen citation plan changed or definitions no longer current")
    return plan


def prompts(plan):
    verify_plan(plan)
    # Do not export hidden probe labels or expected supporting-source IDs.
    return {"plan_hash":plan["plan_hash"],"target":plan["spec"]["target"],
            "queries":[{k:r[k] for k in ("query_id","query","prompt_hash")} for r in plan["queries"]]}


def capture(plan,tracepack):
    """Validate observed trace against every predeclared controlled query.

    Each supplied atomic claim must have an exact answer span. Independent
    reviewers subsequently inspect the ENTIRE answer for omitted claims or
    misattributed citations. Failed calls remain in the fixed denominator.
    """
    verify_plan(plan)
    _bounded(tracepack)
    vendor_trace.validate_tracepack(tracepack)
    if (tracepack["target"]!=plan["spec"]["target"] or tracepack["sources"]!=plan["spec"]["sources"]):
        fail("target or source catalog differs from frozen plan")
    expected={r["query_id"]:r for r in plan["queries"]}
    traces={r["query_id"]:r for r in tracepack["traces"]}
    if set(traces)!=set(expected):
        fail("every predeclared query requires an explicit trace or failure")
    catalog={s["source_id"]:s for s in tracepack["sources"]}
    claims=[]
    review_items=[]
    integrity=[]
    for qid in sorted(expected):
        q,t=expected[qid],traces[qid]
        if (t["query"]!=q["query"] or t["reference_supporting_source_ids"]!=q["reference_supporting_source_ids"]):
            fail("trace differs from frozen query or authored reference")
        chunks=sorted(t["retrieved_chunks"],key=lambda c:c["rank"])
        # Controlled-context protocol: exact supplied version/text must be
        # represented in the trace, not a different or fabricated snippet.
        if [c["source_id"] for c in chunks]!=q["source_ids"] or any(
            c["source_version"]!=catalog[c["source_id"]]["version"] or
            c["text"]!=catalog[c["source_id"]]["text"] for c in chunks):
            fail("observed source material differs from controlled context")
        raw=t.get("atomic_claims",[])
        if t["model_status"]!="complete":
            if raw or t.get("answer") not in (None,""):
                fail("failed trace cannot contain scored answers")
            integrity.append({"query_id":qid,"status":t["model_status"]})
            continue
        answer=_text(t["answer"])
        items=[]
        for c in raw:
            _strict(c,("claim_id","text","cited_source_ids","answer_start","answer_end"))
            local=_id(c["claim_id"])
            start,end=c["answer_start"],c["answer_end"]
            if (type(start) is not int or type(end) is not int or not 0<=start<end<=len(answer)
                    or answer[start:end]!=c["text"]):
                fail("claim must identify exact verbatim answer span")
            text=_text(c["text"],8000)
            for sid in c["cited_source_ids"]:
                _source_id(sid)
            key=buyer_plan._digest([qid,local])
            claim={"claim_id":key,"text":text,"cited_source_ids":c["cited_source_ids"]}
            claims.append(claim)
            items.append({**claim,"answer_start":start,"answer_end":end})
        if len({(i["answer_start"],i["answer_end"]) for i in items})!=len(items):
            fail("duplicate answer span")
        review_items.append({"query_id":qid,"question":q["query"],"answer":answer,
                             "claims":items,"available_source_ids":q["source_ids"]})
        integrity.append({"query_id":qid,"status":"complete"})
    if len(claims)>MAX_CLAIMS or sum(len(c["cited_source_ids"]) for c in claims)>MAX_PAIRS:
        fail("claim or citation pair limit")
    if not review_items:
        fail("no completed answers; preserve failed run separately")
    ap={"schema":"atomic_citation_support_v1","sources":tracepack["sources"],"claims":claims}
    # No claims is allowed but does not count as a passing evaluation.
    tasks=atomic.prepare_review_queue(ap) if claims else []
    return _bounded({"tracepack_hash":vendor_trace.validate_tracepack(tracepack)["tracepack_sha256"],
                    "atomic_pack":ap,"items":review_items,"tasks":tasks,"integrity":integrity})


def _roster(reviewers,operator):
    if not isinstance(reviewers,list) or not 2<=len(reviewers)<=5:
        fail("two to five independent reviewers required")
    ids=sorted(_id(x) for x in reviewers)
    operator=_id(operator)
    if len(set(ids))!=len(ids) or operator in ids:
        fail("distinct clinicians and coordinator required")
    return ids,operator


def prepare(plan,tracepack,destination,reviewers,operator,*,confirmed=False):
    if confirmed is not True:
        fail("external credential, independence and reference approval required")
    ids,operator=_roster(reviewers,operator)
    evidence=capture(plan,tracepack)
    dest=Path(destination).absolute()
    if dest.exists() or dest.is_symlink() or not dest.parent.is_dir() or dest.parent.is_symlink():
        fail("fresh private assessment destination required")
    binding={"schema":SCHEMA,"plan_hash":plan["plan_hash"],"capture_hash":buyer_plan._digest(evidence),
             "reviewers":ids,"operator":operator,"decision_grade":False}
    binding["binding_hash"]=buyer_plan._digest(binding)
    with tempfile.TemporaryDirectory(prefix=".cite-review-",dir=dest.parent) as tmp:
        stage=Path(tmp)/"assessment"
        stage.mkdir(mode=0o700)
        for name,data in (("plan.json",plan),("tracepack.json",tracepack),("binding.json",binding)):
            formal._publish_once(stage/name,data)
        (stage/"submissions").mkdir(mode=0o700)
        for rid in ids:
            packet=packets.issue_packet(stage,binding["binding_hash"],binding["capture_hash"],rid,
                "independent_citation_clinician",[{"cell_id":i["query_id"]} for i in evidence["items"]],
                synthetic=plan["spec"]["synthetic"])
            packets.write_packet(stage,packet)
            _bounded(_template(binding,evidence,packet))
        if dest.exists() or dest.is_symlink():
            fail("assessment destination already exists")
        stage.rename(dest)
    return binding


def _load(directory):
    root=Path(directory)
    if root.is_symlink() or not root.is_dir():
        fail("private assessment directory required")
    research._safe_tree(root)
    plan=formal._json(root/"plan.json")
    trace=formal._json(root/"tracepack.json")
    b=formal._json(root/"binding.json")
    evidence=capture(plan,trace)
    _strict(b,("schema","plan_hash","capture_hash","reviewers","operator","decision_grade","binding_hash"))
    ids,operator=_roster(b["reviewers"],b["operator"])
    if (b["schema"]!=SCHEMA or b["plan_hash"]!=plan["plan_hash"] or ids!=b["reviewers"]
            or operator!=b["operator"] or b["capture_hash"]!=buyer_plan._digest(evidence)
            or b["decision_grade"] is not False or b["binding_hash"]!=buyer_plan._digest(
                {k:v for k,v in b.items() if k!="binding_hash"})):
        fail("review binding changed")
    issued={}
    for rid in ids:
        p=packets.load_packet(root,rid)
        if (not isinstance(p,dict) or p["reviewer_role"]!="independent_citation_clinician"
                or p["synthetic"] is not plan["spec"]["synthetic"]
                or packets.verify_packet(root,p,b["binding_hash"],b["capture_hash"],
                                          [i["query_id"] for i in evidence["items"]],rid)):
            fail("issued citation packet invalid")
        issued[rid]=p
    return root,plan,trace,b,evidence,issued


def _template(b,evidence,packet):
    # Each reviewer sees the full answer, source context, claim spans and
    # current source texts. No other reviewer answers or expected verdict.
    tasks=[{k:t[k] for k in ("claim_id","claim_text","source_id","source_version","source_text")} for t in evidence["tasks"]]
    return {"request":{"schema":SCHEMA,"binding_hash":b["binding_hash"],"packet":packet,
                       "answers":evidence["items"],"claim_source_pairs":tasks},
            "submission":{"independent_review_confirmed":False,
                          "coverage":[{"query_id":i["query_id"],"status":"","note":""} for i in evidence["items"]],
                          "support":[{"claim_id":t["claim_id"],"source_id":t["source_id"],"verdict":"","evidence_quote":""} for t in tasks]}}


def template(directory,reviewer):
    _,_,_,b,evidence,issued=_load(directory)
    if reviewer not in issued:
        fail("reviewer not predeclared")
    return _template(b,evidence,issued[reviewer])


def _validate_submission(sub,evidence):
    _strict(sub,("independent_review_confirmed","coverage","support"))
    if sub["independent_review_confirmed"] is not True:
        fail("independent review confirmation required")
    want={i["query_id"] for i in evidence["items"]}
    covered=set()
    if not isinstance(sub["coverage"],list) or len(sub["coverage"])!=len(want):
        fail("every completed answer requires independent coverage review")
    for row in sub["coverage"]:
        _strict(row,("query_id","status","note"))
        if (row["query_id"] not in want or row["query_id"] in covered or row["status"] not in COVERAGE
                or not isinstance(row["note"],str) or len(row["note"])>4000
                or row["status"]!="complete" and not row["note"].strip()):
            fail("invalid extraction coverage verdict")
        covered.add(row["query_id"])
    pairs={(t["claim_id"],t["source_id"]):t for t in evidence["tasks"]}
    seen=set()
    if not isinstance(sub["support"],list) or len(sub["support"])!=len(pairs):
        fail("every current source pair requires independent support review")
    for row in sub["support"]:
        _strict(row,("claim_id","source_id","verdict","evidence_quote"))
        key=(row["claim_id"],row["source_id"])
        quote=row["evidence_quote"]
        if (key not in pairs or key in seen or row["verdict"] not in VERDICTS
                or not isinstance(quote,str) or len(quote)>8000
                or row["verdict"] in ("supports","contradicts") and
                    (not quote.strip() or quote.strip() not in pairs[key]["source_text"])):
            fail("invalid semantic source annotation or evidence quote")
        seen.add(key)
    return {"independent_review_confirmed":True,"coverage":sorted(sub["coverage"],key=lambda r:r["query_id"]),
            "support":sorted(sub["support"],key=lambda r:(r["claim_id"],r["source_id"]))}


def submit(directory,reviewer,doc,operator,*,confirmed=False):
    if confirmed is not True:
        fail("external reviewer identity and independence attestation required")
    root,_,_,b,evidence,issued=_load(directory)
    operator=_id(operator)
    if reviewer not in issued or operator in issued:
        fail("reviewer/operator role conflict")
    _strict(doc,("request","submission"))
    if doc["request"]!=_template(b,evidence,issued[reviewer])["request"]:
        fail("issued reviewer packet changed")
    result={"binding_hash":b["binding_hash"],"reviewer":reviewer,"operator":operator,
            "operator_attested":True,"submission":_validate_submission(doc["submission"],evidence)}
    result["digest"]=buyer_plan._digest(result)
    formal._publish_once(root/"submissions"/(reviewer+".json"),result)
    return {"reviewer":reviewer,"digest":result["digest"],"decision_grade":False}


def analyze(directory):
    root,plan,trace,b,evidence,issued=_load(directory)
    if {p.name for p in (root/"submissions").iterdir()}!={rid+".json" for rid in issued}:
        fail("all predeclared independent submissions required")
    records={}
    for rid in issued:
        r=formal._json(root/"submissions"/(rid+".json"))
        _strict(r,("binding_hash","reviewer","operator","operator_attested","submission","digest"))
        if (r["binding_hash"]!=b["binding_hash"] or r["reviewer"]!=rid or _id(r["operator"]) in issued
                or r["operator_attested"] is not True or _validate_submission(r["submission"],evidence)!=r["submission"]
                or r["digest"]!=buyer_plan._digest({k:v for k,v in r.items() if k!="digest"})):
            fail("stored submission changed")
        records[rid]=r
    ap=evidence["atomic_pack"]
    annotations=[]
    coverage={}
    unknown_pairs=set()
    for rid,r in records.items():
        for row in r["submission"]["coverage"]:
            coverage.setdefault(row["query_id"],[]).append(row["status"])
        for row in r["submission"]["support"]:
            if row["verdict"]=="cannot_judge":
                unknown_pairs.add((row["claim_id"],row["source_id"]))
            else:
                annotations.append({**row,"reviewer_id":rid,"reviewer_kind":"clinician",
                                    "pack_sha256":atomic.canonical_hash(ap)})
    atomic_report=atomic.score(ap,annotations,min_clinicians=len(issued)) if ap["claims"] else None
    scored={r["claim_id"]:r for r in atomic_report["results"]} if atomic_report else {}
    by_query={i["query_id"]:i for i in evidence["items"]}
    outcomes={r["query_id"]:r["status"] for r in evidence["integrity"]}
    rows=[]
    for query in plan["queries"]:
        qid=query["query_id"]
        i=by_query.get(qid)
        row={"query_id":qid,"case_id":query["case_id"],"probe":query["probe"],"status":outcomes[qid],
             "coverage_verified":False,"n_claims":0,"unsupported":0,"unassessed":0,"supported":0,
             "uncited":0,"missing_source":0,"superseded_source":0,"unretrieved_source":0}
        if i is not None:
            row["coverage_verified"]=all(x=="complete" for x in coverage[qid]) and len(coverage[qid])==len(issued)
            row["n_claims"]=len(i["claims"])
            for claim in i["claims"]:
                s=scored[claim["claim_id"]]
                cites=claim["cited_source_ids"]
                row["uncited"]+=int(not cites)
                row["missing_source"]+=int(bool(s["missing_source_ids"]))
                row["superseded_source"]+=int(bool(s["superseded_source_ids"]))
                unreturned=set(cites)-set(i["available_source_ids"])
                row["unretrieved_source"]+=int(bool(unreturned))
                if not row["coverage_verified"] or any((claim["claim_id"],sid) in unknown_pairs for sid in cites) or any(
                    v in ("unassessed","contested") for v in s["per_current_source"].values()) or s["status"]=="conflicting_evidence":
                    row["unassessed"]+=1
                elif s["status"] in ("unsupported","contradicted") or s["missing_source_ids"] or s["superseded_source_ids"] or unreturned or any(
                    v!="supports" for v in s["per_current_source"].values()):
                    row["unsupported"]+=1
                else:
                    row["supported"]+=1
        rows.append(row)
    summary={}
    for probe in PROBES:
        cells=[r for r in rows if r["probe"]==probe]
        complete=[r for r in cells if r["status"]=="complete" and r["coverage_verified"]]
        assessed=sum(r["unsupported"]+r["supported"] for r in complete)
        total=sum(r["n_claims"] for r in cells)
        summary[probe]={"planned":len(cells),"completed":sum(r["status"]=="complete" for r in cells),
                        "coverage_verified":len(complete),"failed":sum(r["status"]!="complete" for r in cells),
                        "n_claims":total,"n_assessed":assessed,"n_unassessed":total-assessed,
                        "verified_zero_claim_answers":sum(r["n_claims"]==0 for r in complete),
                        "unsupported_rate_on_assessed":sum(r["unsupported"] for r in complete)/assessed if assessed else None,
                        **{k:sum(r[k] for r in complete) for k in ("uncited","missing_source","superseded_source","unretrieved_source")}}
    report={"schema":"controlled-citation-report/1","binding_hash":b["binding_hash"],"plan_hash":plan["plan_hash"],
            "synthetic":plan["spec"]["synthetic"],"tracepack_hash":evidence["tracepack_hash"],
            "submission_digests":{rid:r["digest"] for rid,r in records.items()},"summary":summary,
            "rows":rows,"component_atomic_support_before_coverage_gate":atomic_report,"retrieval":vendor_trace.score_tracepack(trace),
            "decision_grade":False,"limitations":["Controlled-context trace is operator/vendor supplied, not independently instrumented.",
            "Source status and authored probe relevance are not independently validated by software.",
            "Coverage is independently attested, not proof of exhaustive extraction.",
            "Support is not medical truth. No clinical claim or family-maturity upgrade."]}
    report["digest"]=buyer_plan._digest(report)
    formal._publish_once(root/("report-"+report["digest"]+".json"),report)
    return report
