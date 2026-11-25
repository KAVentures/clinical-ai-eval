"""Exploratory corpus-bound retrieval and independent citation review.

External products retrieve from a case-specific allowed corpus. Their reported
trace is self-attested, not a proof of actual execution. References, reviews
and outcomes are kept distinct. No clinical verdict or canonical promotion.
"""
import secrets
from . import diagnostic_imaging as d

SCHEMA="rag-grounding-study/1"
MAX_DOCS=80
MAX_CASES=40
MAX_CANDIDATES=6
MAX_TEXT=16000
MAX_CLAIMS=20
MAX_TOTAL=1000000
MAX_TRACE_BYTES=800000
MAX_EXCHANGE_BYTES=4000000
MAX_TOTAL_CLAIMS_PER_CANDIDATE=300
CONDITIONS=("supported","support_removed")
LABELS=("supported","unsupported","uncertain")
LIMITATIONS=[
    "Exploratory corpus-bound research; clinician reference quality and independence require external verification.",
    "Product-declared trace, corpus use, retrieval, version and actual execution are not independently attested.",
    "Candidate prompt and corpus may reveal the condition; external isolation and no cross-case memory are operator responsibilities.",
    "Citation support requires two independent agreeing reviews; disagreement and uncertainty remain unresolved.",
    "Claims are candidate-declared spans, not an independent exhaustive extraction; answer coverage is separately reviewed.",
    "Descriptive case/claim-weighted estimates, no confidence intervals, clinical safety pass or deployment authorization.",
]


def _text(value,limit=MAX_TEXT):
    return isinstance(value,str) and bool(value.strip()) and len(value.encode("utf-8"))<=limit


def _ids(values, allowed, *, minimum=0):
    return (isinstance(values,list) and minimum<=len(values)<=len(allowed)
            and all(d._token(v) and v in allowed for v in values)
            and len(set(values))==len(values))


def _doc_hash(doc):
    return d._digest(doc)


def freeze(spec,*,nonce=None,confirmed=False):
    d._require(confirmed is True,"explicit prospective study approval required")
    d._keys(spec,"study_id corpus_id corpus_version documents questions candidates reviewers attestations")
    import json
    d._require(len(json.dumps(spec,ensure_ascii=True,allow_nan=False))<=800000,"study specification byte limit")
    d._require(all(d._token(spec[k]) for k in ("study_id","corpus_id","corpus_version")),"invalid study identity")
    att=spec["attestations"]
    d._keys(att,"authorized_corpus independently_reviewed_references no_patient_identifiers prospective isolated_case_execution")
    d._require(all(v is True for v in att.values()),"all study attestations required")
    docs=spec["documents"]
    d._require(isinstance(docs,list) and 1<=len(docs)<=MAX_DOCS,"bounded corpus required")
    by={}
    total=0
    for doc in docs:
        d._keys(doc,"id version title text state")
        d._require(d._token(doc["id"]) and doc["id"] not in by and d._token(doc["version"])
                   and _text(doc["title"],300) and _text(doc["text"]) and doc["state"] in ("current","withdrawn"),
                   "invalid or duplicate corpus document")
        total+=len(doc["title"].encode("utf-8"))+len(doc["text"].encode("utf-8"))
        by[doc["id"]]=doc
    d._require(total<=MAX_TOTAL,"corpus size limit")
    qids=set()
    exchange_bytes=0
    questions=spec["questions"]
    d._require(isinstance(questions,list) and 1<=len(questions)<=MAX_CASES,"bounded question cohort required")
    for q in questions:
        d._keys(q,"id cluster_id query reference_ids distractor_ids")
        d._require(d._token(q["id"]) and q["id"] not in qids and d._token(q["cluster_id"])
                   and _text(q["query"],4000),"invalid or duplicate question")
        qids.add(q["id"])
        d._require(_ids(q["reference_ids"],by,minimum=1) and _ids(q["distractor_ids"],by,minimum=1)
                   and not set(q["reference_ids"])&set(q["distractor_ids"])
                   and all(by[x]["state"]=="current" for x in q["reference_ids"]),
                   "current reference and disjoint nonempty distractor corpus required")
        exchange_bytes+=2*len(q["query"].encode("utf-8"))+sum(len(json.dumps(by[x],ensure_ascii=True)) + 128 for x in q["reference_ids"])+2*sum(len(json.dumps(by[x],ensure_ascii=True))+128 for x in q["distractor_ids"])+512
        d._require(exchange_bytes<=MAX_EXCHANGE_BYTES,"repeated candidate corpus exchange exceeds budget")
    candidates=spec["candidates"]
    d._require(isinstance(candidates,list) and 1<=len(candidates)<=MAX_CANDIDATES,"bounded candidate roster")
    ids=set()
    for c in candidates:
        d._keys(c,"id version")
        d._require(d._token(c["id"]) and d._token(c["version"]) and c["id"] not in ids,"unique candidate identity required")
        ids.add(c["id"])
    d._require(d._unique_tokens(spec["reviewers"],2,2),"two distinct reference review accounts required")
    nonce=secrets.token_hex(32) if nonce is None else nonce
    d._require(d._hex(nonce),"invalid study nonce")
    normalized={**spec,"documents":sorted(docs,key=lambda r:r["id"]),
                "questions":[{**q,"reference_ids":sorted(q["reference_ids"]),"distractor_ids":sorted(q["distractor_ids"])}
                             for q in sorted(questions,key=lambda r:r["id"])],
                "candidates":sorted(candidates,key=lambda r:r["id"]),"reviewers":sorted(spec["reviewers"])}
    return d._sealed({"schema":SCHEMA,"spec":normalized,"private_nonce":nonce,
                      "decision_grade":False,"limitations":LIMITATIONS[:]})


def verify_plan(plan):
    d._keys(plan,"schema spec private_nonce decision_grade limitations digest")
    d._require(freeze(plan["spec"],nonce=plan["private_nonce"],confirmed=True)==plan,"study or reference digest mismatch")


def _candidate(plan,cid):
    matches=[c for c in plan["spec"]["candidates"] if c["id"]==cid]
    d._require(len(matches)==1,"unknown candidate")
    return matches[0]


def _cases(plan,cid):
    return {d._digest([plan["private_nonce"],cid,q["id"],condition]):(q,condition)
            for q in plan["spec"]["questions"] for condition in CONDITIONS}


def _allowed(q,condition):
    return sorted(q["distractor_ids"]+(q["reference_ids"] if condition=="supported" else []))


def exchange(plan,cid):
    verify_plan(plan)
    candidate=_candidate(plan,cid)
    docs={doc["id"]:doc for doc in plan["spec"]["documents"]}
    # All supplied content has an explicit digest. Labels, question IDs,
    # cluster IDs, condition labels and reviewer IDs are omitted.
    rows=[]
    for slot,(q,condition) in sorted(_cases(plan,cid).items()):
        rows.append({"slot":slot,"query":q["query"],
                     "corpus":[{**docs[x],"digest":_doc_hash(docs[x])} for x in _allowed(q,condition)]})
    return {"schema":"rag-grounding-exchange/1","plan_digest":plan["digest"],
            "candidate":candidate,"cases":rows}


def template(plan,cid):
    ex=exchange(plan,cid)
    return {"schema":"rag-grounding-trace/1","plan_digest":ex["plan_digest"],"candidate":ex["candidate"],
            "traces":[{"slot":r["slot"],"status":"unavailable","retrieved":[],"answer":"","claims":[]} for r in ex["cases"]]}


def _validate(plan,doc):
    d._keys(doc,"schema plan_digest candidate traces")
    c=_candidate(plan,doc["candidate"]["id"])
    d._require(doc["schema"]=="rag-grounding-trace/1" and doc["plan_digest"]==plan["digest"] and doc["candidate"]==c,
               "candidate trace binding mismatch")
    cases=_cases(plan,c["id"])
    docs={x["id"]:x for x in plan["spec"]["documents"]}
    seen=set()
    rows=doc["traces"]
    d._require(isinstance(rows,list) and len(rows)==len(cases),"complete candidate trace inventory required")
    for row in rows:
        d._keys(row,"slot status retrieved answer claims")
        slot=row["slot"]
        d._require(d._hex(slot) and slot in cases and slot not in seen,"duplicate or unknown trace slot")
        seen.add(slot)
        q,condition=cases[slot]
        allowed=set(_allowed(q,condition))
        status=row["status"]
        d._require(status in ("answered","abstained","unavailable"),"invalid candidate status")
        d._require(isinstance(row["retrieved"],list) and len(row["retrieved"])<=len(allowed),"invalid retrieval inventory")
        used=set()
        for hit in row["retrieved"]:
            d._keys(hit,"id digest")
            ident=hit["id"]
            d._require(d._token(ident) and ident in allowed and ident not in used and hit["digest"]==_doc_hash(docs[ident]),
                       "retrieved document outside exact allowed corpus")
            used.add(ident)
        if status=="unavailable":
            d._require(row["retrieved"]==[] and row["answer"]=="" and row["claims"]==[],"unavailable trace must be empty")
            continue
        d._require(_text(row["answer"]) and isinstance(row["claims"],list) and len(row["claims"])<=MAX_CLAIMS,"invalid answer or claim inventory")
        # Claim spans are nonoverlapping offsets into the exact answer.
        # Citations may reference unknown/nonretrieved documents, explicitly
        # retained as defects rather than removed to make a trace look safe.
        end=0
        for claim in row["claims"]:
            d._keys(claim,"start end citations")
            a,b=claim["start"],claim["end"]
            d._require(type(a) is int and type(b) is int and end<=a<b<=len(row["answer"])
                       and bool(row["answer"][a:b].strip()),"invalid or overlapping claim span")
            end=b
            d._require(d._unique_tokens(claim["citations"],0,MAX_DOCS),"invalid citation inventory")
    d._require(sum(len(r["claims"]) for r in rows)<=MAX_TOTAL_CLAIMS_PER_CANDIDATE,"candidate claim inventory limit")
    import json
    d._require(len(json.dumps(doc,ensure_ascii=True).encode("ascii"))<=MAX_TRACE_BYTES,"candidate trace byte limit")
    return c["id"]


def locked_traces(plan,documents):
    verify_plan(plan)
    d._require(isinstance(documents,list) and len(documents)<=len(plan["spec"]["candidates"]),"invalid trace roster")
    by={}
    for doc in documents:
        cid=_validate(plan,doc)
        d._require(cid not in by,"duplicate candidate trace")
        by[cid]={**doc,"traces":sorted(doc["traces"],key=lambda r:r["slot"])}
    return [by[cid] for cid in sorted(by)]


def review_packet(plan,documents,reviewer):
    verify_plan(plan)
    d._require(reviewer in plan["spec"]["reviewers"],"assigned reviewer required")
    docs=locked_traces(plan,documents)
    d._require(len(docs)==len(plan["spec"]["candidates"]),"complete candidate roster required before review")
    corpus={x["id"]:x for x in plan["spec"]["documents"]}
    units=[]
    included=set()
    for doc in docs:
        cid=doc["candidate"]["id"]
        for row in doc["traces"]:
            if row["status"]=="unavailable":
                continue
            q,condition=_cases(plan,cid)[row["slot"]]
            allowed=set(_allowed(q,condition))
            # Reviewer sees query, supplied corpus and answer, not declared
            # candidate identity, condition, reference IDs, or peer ratings.
            claim_units=[]
            included.update(x["id"] for x in row["retrieved"])
            for i,claim in enumerate(row["claims"]):
                included.update(x for x in claim["citations"] if x in allowed)
                claim_units.append({"claim_id":d._digest([plan["private_nonce"],row["slot"],i]),
                                    "text":row["answer"][claim["start"]:claim["end"]],
                                    "citations":[{"id":x,"available_in_case":x in allowed}
                                                 for x in claim["citations"]]})
            units.append({"unit_id":d._digest([plan["private_nonce"],"review",row["slot"]]),
                          "query":q["query"],"answer":row["answer"],"claims":claim_units,
                          "retrieved":[x["id"] for x in row["retrieved"]]})
    return {"schema":"rag-grounding-review-packet/1","plan_digest":plan["digest"],
            "trace_inventory_digest":d._digest(docs),"documents":[corpus[x] for x in sorted(included)],
            "units":sorted(units,key=lambda r:r["unit_id"])}


def review_template(packet):
    return {"schema":"rag-grounding-review/1","packet_digest":d._digest(packet),
            "units":[{"unit_id":r["unit_id"],"claim_coverage":"uncertain",
                      "claims":[{"claim_id":c["claim_id"],"label":"uncertain"} for c in r["claims"]]}
                     for r in packet["units"]]}


def _review(packet,doc):
    d._keys(doc,"schema packet_digest units")
    d._require(doc["schema"]=="rag-grounding-review/1" and doc["packet_digest"]==d._digest(packet),"review packet mismatch")
    expected={u["unit_id"]:{c["claim_id"]:c for c in u["claims"]} for u in packet["units"]}
    seen={}
    d._require(isinstance(doc["units"],list) and len(doc["units"])==len(expected),"complete review inventory required")
    for row in doc["units"]:
        d._keys(row,"unit_id claim_coverage claims")
        uid=row["unit_id"]
        d._require(d._hex(uid) and uid in expected and uid not in seen
                   and row["claim_coverage"] in ("complete","incomplete","uncertain"),"invalid review unit")
        claims={}
        d._require(isinstance(row["claims"],list) and len(row["claims"])==len(expected[uid]),"complete claim reviews required")
        for r in row["claims"]:
            d._keys(r,"claim_id label")
            cid=r["claim_id"]
            d._require(d._hex(cid) and cid in expected[uid] and cid not in claims and r["label"] in LABELS,"invalid claim review")
            if r["label"]=="supported":
                d._require(any(c["available_in_case"] for c in expected[uid][cid]["citations"]),
                           "support requires at least one available cited document")
            claims[cid]=r["label"]
        seen[uid]=(row["claim_coverage"],claims)
    return seen


def _agree(a,b):
    return a if a==b else "uncertain"


def _ratio(n,den):
    return n/den if den else None


def _summary(plan,doc,reviews):
    cases=_cases(plan,doc["candidate"]["id"])
    corpus={x["id"]:x for x in plan["spec"]["documents"]}
    results=[]
    for condition in CONDITIONS:
        stats={"n_cases":0,"n_available":0,"n_answered":0,"n_abstained":0,
               "n_support_hit":0,"n_all_reference_retrieved":0,"n_withdrawn_retrieved":0,
               "n_claims":0,"n_uncited_claims":0,"n_outside_corpus_citation_claims":0,
               "n_nonretrieved_citation_claims":0,"n_withdrawn_citation_claims":0,
               "n_review_supported":0,"n_review_unsupported":0,"n_review_unresolved":0,
               "n_review_disagreements":0,"n_answer_claim_inventory_complete":0}
        for row in doc["traces"]:
            q,cond=cases[row["slot"]]
            if cond!=condition:
                continue
            stats["n_cases"]+=1
            if row["status"]=="unavailable":
                continue
            stats["n_available"]+=1
            stats["n_"+row["status"]]+=1
            retrieved={r["id"] for r in row["retrieved"]}
            refs=set(q["reference_ids"])
            if condition=="supported":
                stats["n_support_hit"]+=bool(refs&retrieved)
                stats["n_all_reference_retrieved"]+=refs<=retrieved
            stats["n_withdrawn_retrieved"]+=any(corpus[x]["state"]=="withdrawn" for x in retrieved)
            unit=d._digest([plan["private_nonce"],"review",row["slot"]])
            a,b=reviews[0][unit],reviews[1][unit]
            stats["n_answer_claim_inventory_complete"]+=_agree(a[0],b[0])=="complete"
            allowed=set(_allowed(q,cond))
            for i,claim in enumerate(row["claims"]):
                stats["n_claims"]+=1
                cited=set(claim["citations"])
                stats["n_uncited_claims"]+=not cited
                stats["n_outside_corpus_citation_claims"]+=bool(cited-allowed)
                stats["n_nonretrieved_citation_claims"]+=bool(cited-retrieved)
                stats["n_withdrawn_citation_claims"]+=any(corpus[x]["state"]=="withdrawn" for x in cited&allowed)
                cid=d._digest([plan["private_nonce"],row["slot"],i])
                v1,v2=a[1][cid],b[1][cid]
                agreed=_agree(v1,v2)
                stats["n_review_disagreements"]+=v1!=v2
                stats["n_review_"+("unresolved" if agreed=="uncertain" else agreed)]+=1
        n=stats["n_cases"]
        available=stats["n_available"]
        claims=stats["n_claims"]
        resolved=stats["n_review_supported"]+stats["n_review_unsupported"]
        missing=n-available
        results.append({"condition":condition,"counts":stats,"availability":_ratio(available,n),
                        "reported_reference_hit_rate":_ratio(stats["n_support_hit"],available) if condition=="supported" else None,
                        "reference_hit_bounds":[_ratio(stats["n_support_hit"],n),_ratio(stats["n_support_hit"]+missing,n)] if condition=="supported" else None,
                        "declared_abstention_rate":_ratio(stats["n_abstained"],available),
                        "reviewed_declared_claim_support_rate":_ratio(stats["n_review_supported"],resolved),
                        "declared_claim_support_bounds":[_ratio(stats["n_review_supported"],claims),_ratio(stats["n_review_supported"]+stats["n_review_unresolved"],claims)],
                        "answer_claim_inventory_agreement_rate":_ratio(stats["n_answer_claim_inventory_complete"],available)})
    return {"candidate":doc["candidate"],"conditions":results}


def package(plan,documents,reviews):
    docs=locked_traces(plan,documents)
    ids=plan["spec"]["reviewers"]
    d._require(isinstance(reviews,dict) and set(reviews)==set(ids),"both independent review documents required")
    packet=review_packet(plan,docs,ids[0])
    parsed=[_review(packet,reviews[uid]) for uid in ids]
    return d._sealed({"schema":"rag-grounding-evidence/1","plan":plan,"traces":docs,
                      "reviews":reviews,"results":[_summary(plan,doc,parsed) for doc in docs],
                      "n_clusters":len({q["cluster_id"] for q in plan["spec"]["questions"]}),
                      "decision_grade":False,"limitations":LIMITATIONS[:]})


def verify(evidence,expected_digest=None):
    d._keys(evidence,"schema plan traces reviews results n_clusters decision_grade limitations digest")
    rebuilt=package(evidence["plan"],evidence["traces"],evidence["reviews"])
    d._require(rebuilt==evidence and (expected_digest is None or expected_digest==rebuilt["digest"]),
               "RAG grounding evidence replay mismatch")
    return {"status":"REPLAY_VERIFIED","digest":rebuilt["digest"],"expected_digest_matched":expected_digest is not None,
            "decision_grade":False}


def configure(parser):
    sub=parser.add_subparsers(dest="grounding_action",required=True)
    v=sub.add_parser("verify")
    v.add_argument("evidence")
    v.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    import json
    try:
        print(json.dumps(verify(d._read(args.evidence),args.expected_digest),allow_nan=False))
    except (ValueError,RuntimeError,KeyError,TypeError,OSError,AttributeError,RecursionError):
        raise SystemExit("RAG grounding evidence verification refused.")
