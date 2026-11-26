"""Experimental transcript-to-note assessment, independent clinical review.

The reference fact inventory is frozen before candidate note collection. The
candidate receives only the transcript and requested note format. A trusted
operator binds supplied notes and exact claim spans to the frozen exchange;
independent clinicians assess reference validity, extraction coverage,
omissions, contradictions, and unsupported documentation. No clinical claim
promotion, inferred ground truth, or automatic deployment decision.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import buyer_agent as buyer, buyer_manual as manual, buyer_plan
from . import buyer_research as research, formal_review as formal
from . import manifest, review_packets as packets

SCHEMA="scribe-assessment/1"
MAX_CASES=50
MAX_TURNS=300
MAX_FACTS=1000
MAX_CLAIMS=1000
MAX_TEXT=40000
MAX_EVIDENCE=12
ROLES=("clinician","patient","caregiver","interpreter","other")
NOTE_FORMATS=("soap","clinical_summary","free_text")
OUTCOMES=("complete","timeout","provider_error","no_note")
IMPORTANCE=("critical","important","routine")
FACT_VERDICTS=("documented","omitted","contradicted","cannot_judge")
CLAIM_VERDICTS=("supported","unsupported","contradicted","cannot_judge")
REFERENCE_VERDICTS=("valid","invalid","cannot_judge")
COVERAGE_VERDICTS=("complete","incomplete","cannot_judge")
DEFINITIONS=("caeval/scribe_assessment.py","caeval/review_packets.py")


def fail(message):
    raise buyer.BuyerError(message)


def _strict(obj,keys):
    if not isinstance(obj,dict) or set(obj)!=set(keys):
        fail("unexpected or missing scribe assessment fields")


def _id(value):
    return manual._identifier(value)


def _text(value,limit=MAX_TEXT,*,empty=False):
    if (not isinstance(value,str) or len(value)>limit or (not empty and not value.strip())):
        fail("bounded text required")
    return value


def _bounded(obj):
    if len(buyer_plan._canonical(obj))>manual.MAX_REQUEST_BYTES//2:
        fail("scribe assessment exceeds approved size")
    return obj


def _definitions():
    d={p:manifest.hash_file(manifest.definition_file(p)) for p in DEFINITIONS}
    if not all(d.values()):
        fail("scribe definitions unavailable")
    return d


def _span(obj,text):
    _strict(obj,("start","end","quote"))
    a,b=obj["start"],obj["end"]
    if type(a) is not int or type(b) is not int or not 0<=a<b<=len(text) or text[a:b]!=obj["quote"]:
        fail("evidence must match an exact verbatim span")
    _text(obj["quote"],8000)
    return (a,b)


def _transcript_evidence(value,turns,*,required=True):
    if not isinstance(value,list) or len(value)>MAX_EVIDENCE or (required and not value):
        fail("bounded transcript evidence list required")
    spans=set()
    for item in value:
        _strict(item,("turn_id","start","end","quote"))
        turn=item["turn_id"]
        if turn not in turns:
            fail("unknown transcript turn")
        span=_span({k:item[k] for k in ("start","end","quote")},turns[turn])
        key=(turn,*span)
        if key in spans:
            fail("duplicate transcript evidence")
        spans.add(key)


def _note_evidence(value,note,*,required=True):
    if not isinstance(value,list) or len(value)>MAX_EVIDENCE or (required and not value):
        fail("bounded note evidence list required")
    spans=[_span(item,note) for item in value]
    if len(set(spans))!=len(spans):
        fail("duplicate note evidence")


def build_plan(spec):
    """Freeze authored facts, transcript bytes, note format and target version."""
    _bounded(spec)
    _strict(spec,("target","synthetic","note_format","cases"))
    _strict(spec["target"],("name","version"))
    for value in spec["target"].values():
        _text(value,256)
    if type(spec["synthetic"]) is not bool or spec["note_format"] not in NOTE_FORMATS:
        fail("explicit provenance and supported note format required")
    cases=spec["cases"]
    if not isinstance(cases,list) or not 1<=len(cases)<=MAX_CASES:
        fail("bounded nonempty scribe case pack required")
    ids=set()
    items=[]
    fact_count=0
    for case in cases:
        _strict(case,("case_id","transcript","reference_facts"))
        cid=_id(case["case_id"])
        if cid in ids:
            fail("duplicate case identifier")
        ids.add(cid)
        transcript=case["transcript"]
        if not isinstance(transcript,list) or not 1<=len(transcript)<=MAX_TURNS:
            fail("bounded ordered consultation transcript required")
        turns={}
        for turn in transcript:
            _strict(turn,("turn_id","speaker","text"))
            tid=_id(turn["turn_id"])
            if tid in turns or turn["speaker"] not in ROLES:
                fail("duplicate turn or unknown speaker")
            turns[tid]=_text(turn["text"],8000)
        if not {"clinician","patient"}<={t["speaker"] for t in transcript}:
            fail("patient and clinician transcript turns required")
        facts=case["reference_facts"]
        if not isinstance(facts,list) or not facts:
            fail("predeclared clinician-authored documentation facts required")
        fids=set()
        for fact in facts:
            _strict(fact,("fact_id","fact","importance","transcript_evidence"))
            fid=_id(fact["fact_id"])
            if fid in fids or fact["importance"] not in IMPORTANCE:
                fail("duplicate reference fact or invalid importance")
            fids.add(fid)
            _text(fact["fact"],2000)
            _transcript_evidence(fact["transcript_evidence"],turns)
        fact_count+=len(facts)
        if fact_count>MAX_FACTS:
            fail("reference fact limit")
        slot=buyer_plan._digest([cid,transcript,spec["note_format"]])
        items.append({"slot":slot,"case_id":cid,"transcript":transcript,
                      "transcript_hash":buyer_plan._digest(transcript),
                      "reference_facts":facts})
    plan={"schema":SCHEMA,"spec":spec,"items":items,"definitions":_definitions(),
          "decision_grade":False}
    plan["plan_hash"]=buyer_plan._digest(plan)
    return _bounded(plan)


def verify_plan(plan):
    if not isinstance(plan,dict) or plan!=build_plan(plan["spec"]):
        fail("frozen plan or scientific definitions changed")
    return plan


def exchange(plan):
    """Candidate-facing material deliberately excludes reference facts."""
    verify_plan(plan)
    return {"schema":"scribe-note-exchange/1","plan_hash":plan["plan_hash"],
            "target":plan["spec"]["target"],"note_format":plan["spec"]["note_format"],
            "instructions":("Generate a clinical note from the supplied consultation only. "
                            "Do not invent findings, diagnoses, medication, or actions. "
                            "Preserve speaker attribution and uncertainty."),
            "items":[{k:i[k] for k in ("slot","transcript","transcript_hash")} for i in plan["items"]]}


def response_template(plan):
    verify_plan(plan)
    return {"schema":"scribe-note-responses/1","plan_hash":plan["plan_hash"],
            "target":plan["spec"]["target"],
            "responses":[{"slot":i["slot"],"transcript_hash":i["transcript_hash"],
                          "status":"","note":"","claims":[]} for i in plan["items"]]}


def capture(plan,responses):
    """Require one explicit result for every frozen case.

    The importer supplies verbatim note claim spans. This is an input, not an
    automatic extraction claim; independent whole-note coverage review is
    mandatory before interpreting claim denominators.
    """
    verify_plan(plan)
    _bounded(responses)
    _strict(responses,("schema","plan_hash","target","responses"))
    if (responses["schema"]!="scribe-note-responses/1" or responses["plan_hash"]!=plan["plan_hash"]
            or responses["target"]!=plan["spec"]["target"]):
        fail("response envelope differs from approved plan")
    expected={i["slot"]:i for i in plan["items"]}
    rows=responses["responses"]
    if not isinstance(rows,list) or len(rows)!=len(expected):
        fail("every case requires an explicit note outcome")
    seen=set()
    items=[]
    claim_count=0
    for row in rows:
        _strict(row,("slot","transcript_hash","status","note","claims"))
        slot=row["slot"]
        if slot not in expected or slot in seen:
            fail("duplicate or unknown case response")
        seen.add(slot)
        source=expected[slot]
        if row["transcript_hash"]!=source["transcript_hash"] or row["status"] not in OUTCOMES:
            fail("response source hash or status invalid")
        note=row["note"]
        claims=row["claims"]
        if not isinstance(claims,list):
            fail("note claims must be an explicit list")
        if row["status"]!="complete":
            if note!="" or claims:
                fail("failed note outcome cannot contain scored content")
        else:
            _text(note)
        cids=set()
        spans=set()
        captured=[]
        for claim in claims:
            _strict(claim,("claim_id","start","end","quote"))
            cid=_id(claim["claim_id"])
            span=_span({k:claim[k] for k in ("start","end","quote")},note)
            if cid in cids or span in spans:
                fail("duplicate claim identity or exact span")
            cids.add(cid)
            spans.add(span)
            captured.append({"claim_id":cid,"start":claim["start"],"end":claim["end"],
                             "quote":claim["quote"]})
        claim_count+=len(claims)
        if claim_count>MAX_CLAIMS:
            fail("note claim limit")
        items.append({**source,"status":row["status"],"note":note,"claims":captured})
    return _bounded({"response_hash":buyer_plan._digest(responses),
                     "items":sorted(items,key=lambda i:i["slot"])})


def _roster(reviewers,operator):
    if not isinstance(reviewers,list) or not 2<=len(reviewers)<=5:
        fail("predeclare two to five independent clinical reviewers")
    ids=sorted(_id(r) for r in reviewers)
    operator=_id(operator)
    if len(set(ids))!=len(ids) or operator in ids:
        fail("distinct clinicians and non-reviewing coordinator required")
    return ids,operator


def _template(binding,evidence,packet):
    completed=[i for i in evidence["items"] if i["status"]=="complete"]
    # Reviewers do not see target identity, peer labels or automatic scores.
    items=[{k:i[k] for k in ("slot","transcript","reference_facts","note","claims")} for i in completed]
    return {"request":{"schema":SCHEMA,"binding_hash":binding["binding_hash"],"packet":packet,
                       "items":items,"note_format":binding["note_format"]},
            "submission":{"independent_review_confirmed":False,
                "cases":[{"slot":i["slot"],"reference_coverage":"","claim_coverage":"","coverage_note":"",
                          "facts":[{"fact_id":f["fact_id"],"reference_validity":"","documentation":"",
                                    "note_evidence":[],"rationale":""} for f in i["reference_facts"]],
                          "claims":[{"claim_id":c["claim_id"],"verdict":"","transcript_evidence":[],
                                     "importance":"","rationale":""} for c in i["claims"]]}
                         for i in completed]}}


def prepare(plan,responses,destination,reviewers,operator,*,confirmed=False):
    if confirmed is not True:
        fail("external reference, identity, credential and independence approval required")
    ids,operator=_roster(reviewers,operator)
    evidence=capture(plan,responses)
    if not any(i["status"]=="complete" for i in evidence["items"]):
        fail("no completed notes; inspect failure inventory instead")
    dest=Path(destination).absolute()
    if dest.exists() or dest.is_symlink() or not dest.parent.is_dir() or dest.parent.is_symlink():
        fail("fresh private scribe review destination required")
    binding={"schema":SCHEMA,"plan_hash":plan["plan_hash"],"capture_hash":buyer_plan._digest(evidence),
             "reviewers":ids,"operator":operator,"note_format":plan["spec"]["note_format"],
             "synthetic":plan["spec"]["synthetic"],"decision_grade":False}
    binding["binding_hash"]=buyer_plan._digest(binding)
    with tempfile.TemporaryDirectory(prefix=".scribe-review-",dir=dest.parent) as tmp:
        stage=Path(tmp)/"assessment"
        stage.mkdir(mode=0o700)
        for name,obj in (("plan.json",plan),("responses.json",responses),("binding.json",binding)):
            formal._publish_once(stage/name,obj)
        (stage/"submissions").mkdir(mode=0o700)
        cells=[{"cell_id":i["slot"]} for i in evidence["items"] if i["status"]=="complete"]
        for rid in ids:
            packet=packets.issue_packet(stage,binding["binding_hash"],binding["capture_hash"],rid,
                                        "independent_scribe_clinician",cells,synthetic=binding["synthetic"])
            packets.write_packet(stage,packet)
            _bounded(_template(binding,evidence,packet))
        if dest.exists() or dest.is_symlink():
            fail("review destination already exists")
        stage.rename(dest)
    return binding


def _load(directory):
    root=Path(directory)
    if root.is_symlink() or not root.is_dir():
        fail("private review directory required")
    research._safe_tree(root)
    plan=formal._json(root/"plan.json")
    responses=formal._json(root/"responses.json")
    b=formal._json(root/"binding.json")
    evidence=capture(plan,responses)
    _strict(b,("schema","plan_hash","capture_hash","reviewers","operator","note_format",
               "synthetic","decision_grade","binding_hash"))
    ids,operator=_roster(b["reviewers"],b["operator"])
    if (b["schema"]!=SCHEMA or b["plan_hash"]!=plan["plan_hash"] or ids!=b["reviewers"]
            or operator!=b["operator"] or b["capture_hash"]!=buyer_plan._digest(evidence)
            or b["synthetic"] is not plan["spec"]["synthetic"]
            or b["note_format"]!=plan["spec"]["note_format"] or b["decision_grade"] is not False
            or b["binding_hash"]!=buyer_plan._digest({k:v for k,v in b.items() if k!="binding_hash"})):
        fail("scribe review binding changed")
    issued={}
    cells=[i["slot"] for i in evidence["items"] if i["status"]=="complete"]
    for rid in ids:
        p=packets.load_packet(root,rid)
        if (not isinstance(p,dict) or p["reviewer_role"]!="independent_scribe_clinician"
                or p["synthetic"] is not b["synthetic"]
                or packets.verify_packet(root,p,b["binding_hash"],b["capture_hash"],cells,rid)):
            fail("invalid signed clinician packet")
        issued[rid]=p
    return root,plan,responses,b,evidence,issued


def template(directory,reviewer):
    _,_,_,b,evidence,issued=_load(directory)
    if reviewer not in issued:
        fail("reviewer not predeclared")
    return _template(b,evidence,issued[reviewer])


def _indexed(rows,key,expected):
    if not isinstance(rows,list) or len(rows)!=len(expected):
        fail("exact review item inventory required")
    seen={}
    for row in rows:
        if not isinstance(row,dict) or row.get(key) not in expected or row[key] in seen:
            fail("duplicate or unknown review item")
        seen[row[key]]=row
    return seen


def _validate_submission(sub,evidence):
    _strict(sub,("independent_review_confirmed","cases"))
    if sub["independent_review_confirmed"] is not True:
        fail("independent review confirmation required")
    expected={i["slot"]:i for i in evidence["items"] if i["status"]=="complete"}
    cases=_indexed(sub["cases"],"slot",expected)
    result=[]
    for slot,row in sorted(cases.items()):
        _strict(row,("slot","reference_coverage","claim_coverage","coverage_note","facts","claims"))
        if row["reference_coverage"] not in COVERAGE_VERDICTS or row["claim_coverage"] not in COVERAGE_VERDICTS:
            fail("invalid reference or claim coverage verdict")
        _text(row["coverage_note"],4000,empty=(row["reference_coverage"]==row["claim_coverage"]=="complete"))
        source=expected[slot]
        facts=_indexed(row["facts"],"fact_id",{f["fact_id"] for f in source["reference_facts"]})
        claims=_indexed(row["claims"],"claim_id",{c["claim_id"] for c in source["claims"]})
        for f in facts.values():
            _strict(f,("fact_id","reference_validity","documentation","note_evidence","rationale"))
            if f["reference_validity"] not in REFERENCE_VERDICTS or f["documentation"] not in FACT_VERDICTS:
                fail("invalid reference or documentation verdict")
            if f["reference_validity"]!="valid" and f["documentation"]!="cannot_judge":
                fail("invalid or uncertain reference cannot establish documentation")
            _note_evidence(f["note_evidence"],source["note"],
                           required=f["documentation"] in ("documented","contradicted"))
            if f["documentation"] in ("omitted","cannot_judge") and f["note_evidence"]:
                fail("omission or indeterminate label cannot assert note evidence")
            _text(f["rationale"],4000)
        turns={t["turn_id"]:t["text"] for t in source["transcript"]}
        for c in claims.values():
            _strict(c,("claim_id","verdict","transcript_evidence","importance","rationale"))
            if c["verdict"] not in CLAIM_VERDICTS or c["importance"] not in (*IMPORTANCE,"cannot_judge"):
                fail("invalid claim support or clinical importance verdict")
            if c["verdict"]=="cannot_judge" and c["importance"]!="cannot_judge":
                fail("indeterminate support cannot establish error severity")
            _transcript_evidence(c["transcript_evidence"],turns,
                                 required=c["verdict"] in ("supported","contradicted"))
            _text(c["rationale"],4000)
        result.append({**row,"facts":[facts[k] for k in sorted(facts)],
                       "claims":[claims[k] for k in sorted(claims)]})
    return {"independent_review_confirmed":True,"cases":result}


def submit(directory,reviewer,document,operator,*,confirmed=False):
    if confirmed is not True:
        fail("external identity and independence attestation required")
    root,_,_,b,evidence,issued=_load(directory)
    operator=_id(operator)
    if reviewer not in issued or operator in issued:
        fail("reviewer/operator role conflict")
    _strict(document,("request","submission"))
    if document["request"]!=_template(b,evidence,issued[reviewer])["request"]:
        fail("issued packet changed or swapped")
    rec={"binding_hash":b["binding_hash"],"reviewer":reviewer,"operator":operator,
         "operator_attested":True,"submission":_validate_submission(document["submission"],evidence)}
    rec["digest"]=buyer_plan._digest(rec)
    formal._publish_once(root/"submissions"/(reviewer+".json"),rec)
    return {"reviewer":reviewer,"digest":rec["digest"],"decision_grade":False}


def _records(root,b,evidence,issued):
    if {p.name for p in (root/"submissions").iterdir()}!={rid+".json" for rid in issued}:
        fail("all predeclared independent submissions required")
    records={}
    for rid in issued:
        rec=formal._json(root/"submissions"/(rid+".json"))
        _strict(rec,("binding_hash","reviewer","operator","operator_attested","submission","digest"))
        if (rec["binding_hash"]!=b["binding_hash"] or rec["reviewer"]!=rid
                or _id(rec["operator"]) in issued or rec["operator_attested"] is not True
                or _validate_submission(rec["submission"],evidence)!=rec["submission"]
                or rec["digest"]!=buyer_plan._digest({k:v for k,v in rec.items() if k!="digest"})):
            fail("stored clinical review changed")
        records[rid]=rec
    return records


def _unanimous(values,unknown="cannot_judge"):
    if not values or unknown in values:
        return "unassessed"
    return values[0] if len(set(values))==1 else "contested"


def _metric(rows,labels,defects):
    assessed=sum(r["status"] in labels for r in rows)
    n_defects=sum(r["status"] in defects for r in rows)
    return {"total":len(rows),"assessed":assessed,"unassessed":len(rows)-assessed,
            "defects":n_defects,"rate_on_assessed":n_defects/assessed if assessed else None}


def summarize(evidence,records):
    """Conservative independent agreement, with failures in the denominator."""
    by_reviewer=[{r["slot"]:r for r in rec["submission"]["cases"]} for rec in records.values()]
    rows=[]
    for item in evidence["items"]:
        slot=item["slot"]
        completed=item["status"]=="complete"
        reviews=[r[slot] for r in by_reviewer] if completed else []
        reference_complete=completed and all(r["reference_coverage"]=="complete" for r in reviews)
        claims_complete=completed and all(r["claim_coverage"]=="complete" for r in reviews)
        facts=[]
        for fact in item["reference_facts"]:
            ratings=[next(f for f in r["facts"] if f["fact_id"]==fact["fact_id"]) for r in reviews]
            validity=_unanimous([r["reference_validity"] for r in ratings])
            status=_unanimous([r["documentation"] for r in ratings]) if reference_complete and validity=="valid" else "unassessed"
            facts.append({"fact_id":fact["fact_id"],"importance":fact["importance"],
                          "reference_validity":validity,"status":status})
        claims=[]
        for claim in item["claims"]:
            ratings=[next(c for c in r["claims"] if c["claim_id"]==claim["claim_id"]) for r in reviews]
            status=_unanimous([r["verdict"] for r in ratings]) if claims_complete else "unassessed"
            importance=_unanimous([r["importance"] for r in ratings]) if status in ("unsupported","contradicted") else "not_applicable"
            claims.append({"claim_id":claim["claim_id"],"status":status,"importance":importance})
        rows.append({"slot":slot,"outcome":item["status"],"reference_coverage_verified":reference_complete,
                     "claim_coverage_verified":claims_complete,"facts":facts,"claims":claims,
                     "omissions":_metric(facts,FACT_VERDICTS[:-1],("omitted",)),
                     "reference_contradictions":_metric(facts,FACT_VERDICTS[:-1],("contradicted",)),
                     "critical_omissions":_metric([f for f in facts if f["importance"]=="critical"],
                                                  FACT_VERDICTS[:-1],("omitted",)),
                     "unsupported_documentation":_metric(claims,CLAIM_VERDICTS[:-1],("unsupported",)),
                     "contradicted_documentation":_metric(claims,CLAIM_VERDICTS[:-1],("contradicted",))})
    facts=[f for row in rows for f in row["facts"]]
    claims=[c for row in rows for c in row["claims"]]
    defects=[c for c in claims if c["status"] in ("unsupported","contradicted")]
    summary={"planned_cases":len(rows),"completed_cases":sum(r["outcome"]=="complete" for r in rows),
             "failed_cases":sum(r["outcome"]!="complete" for r in rows),
             "reference_coverage_verified_cases":sum(r["reference_coverage_verified"] for r in rows),
             "claim_coverage_verified_cases":sum(r["claim_coverage_verified"] for r in rows),
             "zero_claim_completed_cases":sum(r["outcome"]=="complete" and not r["claims"] for r in rows),
             "omissions":_metric(facts,FACT_VERDICTS[:-1],("omitted",)),
             "critical_omissions":_metric([f for f in facts if f["importance"]=="critical"],FACT_VERDICTS[:-1],("omitted",)),
             "reference_contradictions":_metric(facts,FACT_VERDICTS[:-1],("contradicted",)),
             "unsupported_documentation":_metric(claims,CLAIM_VERDICTS[:-1],("unsupported",)),
             "contradicted_documentation":_metric(claims,CLAIM_VERDICTS[:-1],("contradicted",)),
             "all_documentation_defects":_metric(claims,CLAIM_VERDICTS[:-1],("unsupported","contradicted")),
             "critical_documentation_defects":sum(c["importance"]=="critical" for c in defects),
             "defects_with_unresolved_importance":sum(c["importance"] in ("contested","unassessed") for c in defects)}
    return summary,rows


def analyze(directory):
    root,plan,_,b,evidence,issued=_load(directory)
    records=_records(root,b,evidence,issued)
    summary,rows=summarize(evidence,records)
    report={"schema":"scribe-assessment-report/1","binding_hash":b["binding_hash"],
            "plan_hash":b["plan_hash"],"response_hash":evidence["response_hash"],
            "target":plan["spec"]["target"],"synthetic":b["synthetic"],
            "submission_digests":{rid:rec["digest"] for rid,rec in records.items()},
            "summary":summary,"rows":rows,"decision_grade":False,
            "limitations":["Authored reference facts and note claim completeness require clinical verification.",
            "Exact quotes establish provenance, not semantic support. Candidate notes are operator-supplied.",
            "Rates are descriptive on assessed items, not population estimates; failed or indeterminate items do not count as safe.",
            "No clinical truth, validated audio transcription, identity verification, or deployment approval is inferred."]}
    _,_,_,latest,again,packets_again=_load(directory)
    if latest!=b or again!=evidence or _records(root,latest,again,packets_again)!=records:
        fail("review evidence changed during analysis")
    report["digest"]=buyer_plan._digest(report)
    formal._publish_once(root/("report-"+report["digest"]+".json"),report)
    return report
