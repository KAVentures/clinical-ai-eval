"""Independent clinician review of the existing patient multi-turn rubric.

A trusted operator supplies a verified canonical patient package. All recorded
episodes (not only machine-flagged ones) are included. This derivative workflow
does not modify source evidence, the canonical scorer, or claim authority.
"""
from __future__ import annotations

import json
import tempfile
from itertools import combinations
from pathlib import Path

from . import buyer_agent as buyer, buyer_manual as manual, buyer_plan
from . import buyer_research as research, formal_review as formal, manifest
from . import review_packets as packets, unit_review
from .patient import judging

FIELDS=tuple(judging.SCHEMA_FIELDS)
LABELS=("yes","no","cannot_judge")
MAX_EPISODES=400
MAX_PACKET_BYTES=manual.MAX_REQUEST_BYTES
SCHEMA="patient-independent-review/1"


def _fail(message):
    raise buyer.BuyerError(message)


def _rows(raw):
    return [manual._parse(line) for line in raw.splitlines() if line.strip()]


def _source(source,approved):
    ws=Path(source).absolute()
    if ws.is_symlink() or not ws.is_dir():
        _fail("regular patient package required")
    meta=research._preflight(ws)
    if (meta["executor"]!="patient_episode" or meta["manifest_hash"]!=approved
            or manifest.verify_manifest(ws)["verdict"]!=manifest.VALID):
        _fail("exact verified patient assessment manifest required")
    cells=_rows(research.verified_bytes(ws,meta,"results"))
    responses=_rows(research.verified_bytes(ws,meta,"responses"))
    analysis=manual._parse(research.verified_bytes(ws,meta,"analysis"))
    locked=manual._parse(research.verified_bytes(ws,meta,"review_manifest"))
    unit_review.verify_review_manifest(locked)
    if (analysis["executor"]!="patient_episode" or analysis["n_cells"]!=len(cells)
            or not 1<=len(cells)<=MAX_EPISODES or len(responses)!=len(cells)):
        _fail("invalid patient review inventory")
    by_response={r["perturbation_id"]:r for r in responses}
    if len(by_response)!=len(responses):
        _fail("duplicate response identity")
    items={}
    ids=set()
    used=set()
    for cell in cells:
        eid=cell["patient"]["episode_id"]
        pid=cell["perturbation_id"]
        r=by_response[pid]
        if (not isinstance(eid,str) or not eid or eid in ids or pid in used
                or cell["judge_contract"]!="patient_multiturn_v1"
                or any(cell[k]!=r[k] for k in ("judge_contract","judge_payload","input_text","response_text"))):
            _fail("response and scored episode identity mismatch")
        ids.add(eid)
        used.add(pid)
        payload=cell["judge_payload"]
        judging.assert_blinded(payload)
        transcript=payload["transcript"]
        facts=payload["facts_available_before_each_system_turn"]
        if (not isinstance(transcript,list) or not transcript
                or any(set(t)!={"turn","speaker","text"} or type(t["turn"]) is not int
                       or t["speaker"] not in ("patient","system")
                       or not isinstance(t["text"],str) for t in transcript)
                or len({t["turn"] for t in transcript})!=len(transcript)
                or [t["turn"] for t in transcript]!=sorted(t["turn"] for t in transcript)
                or not isinstance(facts,list)
                or any(set(f)!={"turn","facts_known"} or not isinstance(f["facts_known"],list)
                       or any(not isinstance(k,str) for k in f["facts_known"]) for f in facts)
                or [f["turn"] for f in facts]!=[t["turn"] for t in transcript if t["speaker"]=="system"]):
            _fail("invalid blinded transcript structure")
        if not any(t["speaker"]=="system" for t in transcript):
            _fail("incomplete conversation cannot be reviewed as observed clinical conduct")
        if cell["input_text"]!=judging.format_judge_user(payload):
            _fail("frozen judge view does not match transcript")
        from .util import stable_hash_text
        trace=stable_hash_text("|".join(t["speaker"]+":"+t["text"] for t in transcript))
        if trace!=cell["patient"]["trace_hash"]:
            _fail("transcript differs from recorded episode trace")
        slot=buyer_plan._digest([approved,eid])
        # Deliberately omit candidate identity, stress labels, ground truth,
        # machine verdicts, inferred actions and mandatory-review strata.
        items[slot]={"slot":slot,"transcript":transcript,"disclosed_facts_by_turn":facts}
    queued=[r["unit_id"] for r in locked["expected_units"]]
    if (len(queued)!=locked["n_expected"] or len(queued)!=len(set(queued))
            or not set(queued)<=ids or analysis["n_review_selected"]!=len(queued)):
        _fail("locked mandatory review units not covered")
    if (research._preflight(ws)!=meta or manifest.verify_manifest(ws)["verdict"]!=manifest.VALID):
        _fail("source changed during capture")
    authority=analysis["claim_authority"]["effective_claim"]
    provenance={"claim":authority,"mock_target":analysis["target"].get("is_mock"),
                "reviewed_pack":analysis["case_pack"].get("clinician_reviewed")}
    return {"source_manifest":approved,"items":[items[k] for k in sorted(items)],
            "source_queue_count":len(queued),"source_provenance":provenance}


def _roster(reviewers,operator):
    if not isinstance(reviewers,list) or not 2<=len(reviewers)<=5:
        _fail("predeclare two to five independent clinicians")
    ids=sorted(manual._identifier(r) for r in reviewers)
    operator=manual._identifier(operator)
    if len(set(ids))!=len(ids) or operator in ids:
        _fail("distinct reviewers and non-reviewing operator required")
    return ids,operator


def prepare(source,approved,destination,reviewers,operator,*,confirmed=False):
    if confirmed is not True:
        _fail("explicit external roster and independence verification required")
    ids,operator=_roster(reviewers,operator)
    dest=Path(destination).absolute()
    if dest.exists() or dest.is_symlink() or not dest.parent.is_dir() or dest.parent.is_symlink():
        _fail("fresh private review destination required")
    src=Path(source).resolve()
    dest=dest.parent.resolve()/dest.name
    if src==dest or src in dest.parents or dest in src.parents:
        _fail("derivative review must be outside the original package")
    capture=_source(source,approved)
    binding={"schema":SCHEMA,"source_manifest":approved,
             "capture_digest":buyer_plan._digest(capture),"reviewers":ids,
             "operator":operator,"n_episodes":len(capture["items"]),
             "rubric_hash":manifest.hash_file(manifest.definition_file("prompts/patient_judge_prompt.txt")),
             "synthetic":capture["source_provenance"]["claim"] in ("none","demonstration")
                or capture["source_provenance"]["mock_target"] is not False,
             "decision_grade":False}
    binding["binding_hash"]=buyer_plan._digest(binding)
    # Stage in private directory; no overwrite. Trusted parent is required.
    with tempfile.TemporaryDirectory(prefix=".patient-review-",dir=dest.parent) as tmp:
        stage=Path(tmp)/"review"
        stage.mkdir(mode=0o700)
        formal._publish_once(stage/"binding.json",binding)
        formal._publish_once(stage/"capture.json",capture)
        (stage/"submissions").mkdir(mode=0o700)
        rows=[{"cell_id":i["slot"]} for i in capture["items"]]
        for rid in ids:
            p=packets.issue_packet(stage,binding["binding_hash"],approved,rid,
                                   "blinded_patient_clinician",rows,synthetic=binding["synthetic"])
            packets.write_packet(stage,p)
            if len(json.dumps(_template(binding,capture,p),ensure_ascii=False,indent=2).encode("utf-8"))+1>MAX_PACKET_BYTES:
                _fail("review packet exceeds approved export size")
        if dest.exists() or dest.is_symlink():
            _fail("review destination already exists")
        stage.rename(dest)
    return binding


def _load(source,dest):
    dest=Path(dest)
    if dest.is_symlink() or not dest.is_dir():
        _fail("regular review workspace required")
    research._safe_tree(dest)
    b=formal._json(dest/"binding.json")
    if (b.get("schema")!=SCHEMA or b.get("binding_hash")!=buyer_plan._digest(
            {k:v for k,v in b.items() if k!="binding_hash"})
            or b.get("decision_grade") is not False):
        _fail("review binding changed")
    ids,operator=_roster(b["reviewers"],b["operator"])
    capture=_source(source,b["source_manifest"])
    if (ids!=b["reviewers"] or operator!=b["operator"]
            or capture!=formal._json(dest/"capture.json")
            or buyer_plan._digest(capture)!=b["capture_digest"]
            or b["n_episodes"]!=len(capture["items"])
            or b["synthetic"]!=(capture["source_provenance"]["claim"] in ("none","demonstration")
                or capture["source_provenance"]["mock_target"] is not False)
            or b["rubric_hash"]!=manifest.hash_file(manifest.definition_file("prompts/patient_judge_prompt.txt"))):
        _fail("patient review source or rubric changed")
    issued={}
    for rid in ids:
        p=packets.load_packet(dest,rid)
        if (not isinstance(p,dict) or p.get("reviewer_role")!="blinded_patient_clinician"
                or p.get("synthetic") is not b["synthetic"]
                or packets.verify_packet(dest,p,b["binding_hash"],b["source_manifest"],
                                         [i["slot"] for i in capture["items"]],rid)):
            _fail("invalid issued reviewer packet")
        issued[rid]=p
    return dest,b,capture,issued


def _template(b,capture,packet):
    return {"request":{"schema":SCHEMA,"binding_hash":b["binding_hash"],
                       "packet":packet,"rubric_fields":list(FIELDS),
                       "items":capture["items"]},
            "submission":{"independent_review_confirmed":False,
                          "answers":[{"slot":i["slot"],**{k:"" for k in FIELDS},
                                      "final_disposition":"","rationale":""} for i in capture["items"]]}}


def template(source,dest,reviewer):
    _,b,capture,issued=_load(source,dest)
    if reviewer not in issued:
        _fail("reviewer not predeclared")
    return _template(b,capture,issued[reviewer])


def _answers(rows,capture):
    want={i["slot"] for i in capture["items"]}
    if not isinstance(rows,list) or len(rows)!=len(want):
        _fail("every issued episode requires an answer")
    seen=set()
    for r in rows:
        if (not isinstance(r,dict) or set(r)!={"slot",*FIELDS,"final_disposition","rationale"}
                or not isinstance(r["slot"],str) or r["slot"] not in want or r["slot"] in seen
                or any(r[k] not in LABELS for k in FIELDS)
                or r["final_disposition"] not in judging.DISPOSITIONS|{"cannot_judge"}
                or not isinstance(r["rationale"],str) or not r["rationale"].strip()
                or len(r["rationale"])>4000):
            _fail("invalid or duplicate patient review answer")
        seen.add(r["slot"])
    return sorted(rows,key=lambda r:r["slot"])


def submit(source,dest,reviewer,document,operator,*,confirmed=False):
    if confirmed is not True:
        _fail("external reviewer identity, credentials and independence attestation required")
    dest,b,capture,issued=_load(source,dest)
    operator=manual._identifier(operator)
    if reviewer not in issued or operator in issued:
        _fail("reviewer/operator role conflict")
    expected=_template(b,capture,issued[reviewer])
    if (not isinstance(document,dict) or set(document)!={"request","submission"}
            or document["request"]!=expected["request"]
            or not isinstance(document["submission"],dict)
            or set(document["submission"])!={"answers","independent_review_confirmed"}
            or document["submission"]["independent_review_confirmed"] is not True):
        _fail("changed packet or missing independent confirmation")
    obj={"binding_hash":b["binding_hash"],"reviewer":reviewer,"operator":operator,
         "operator_attested":True,"answers":_answers(document["submission"]["answers"],capture)}
    obj["digest"]=buyer_plan._digest(obj)
    formal._publish_once(dest/"submissions"/(reviewer+".json"),obj)
    return {"reviewer":reviewer,"digest":obj["digest"],"decision_grade":False}


def _records(dest,b,capture,issued):
    records={}
    for rid in issued:
        p=dest/"submissions"/(rid+".json")
        if not p.exists() and not p.is_symlink():
            continue
        r=formal._json(p)
        if (set(r)!={"binding_hash","reviewer","operator","operator_attested","answers","digest"}
                or r["binding_hash"]!=b["binding_hash"] or r["reviewer"]!=rid
                or r["operator_attested"] is not True or manual._identifier(r["operator"]) in issued
                or r["digest"]!=buyer_plan._digest({k:v for k,v in r.items() if k!="digest"})
                or _answers(r["answers"],capture)!=r["answers"]):
            _fail("stored independent submission changed")
        records[rid]=r
    if {p.name for p in (dest/"submissions").iterdir()}!={rid+".json" for rid in records}:
        _fail("unexpected submission inventory")
    return records


def summarize(records,slots):
    """Descriptive per-axis agreement; indeterminate labels never become negatives."""
    values={rid:{r["slot"]:r for r in rec["answers"]} for rid,rec in records.items()}
    result={}
    for field in (*FIELDS,"final_disposition"):
        counts={"unanimous_determinate":0,"disagreement":0,"indeterminate":0,
                "pairwise_observed":0,"pairwise_agree":0}
        histogram={}
        for slot in slots:
            labels=[v[slot][field] for v in values.values()]
            for label in labels:
                histogram[label]=histogram.get(label,0)+1
            if "cannot_judge" in labels:
                counts["indeterminate"]+=1
            elif len(set(labels))==1:
                counts["unanimous_determinate"]+=1
            else:
                counts["disagreement"]+=1
            for a,b in combinations(labels,2):
                if "cannot_judge" not in (a,b):
                    counts["pairwise_observed"]+=1
                    counts["pairwise_agree"]+=int(a==b)
        n=counts["pairwise_observed"]
        result[field]={**counts,"episodes":len(slots),"label_counts":histogram,
                       "pairwise_agreement":counts["pairwise_agree"]/n if n else None}
    return result


def analyze(source,dest):
    dest,b,capture,issued=_load(source,dest)
    records=_records(dest,b,capture,issued)
    if set(records)!=set(issued):
        _fail("all predeclared independent reviewers required")
    out={"schema":"patient-independent-analysis/1","binding_hash":b["binding_hash"],
         "source_manifest":b["source_manifest"],"source_provenance":capture["source_provenance"],
         "synthetic":b["synthetic"],"n_reviewers":len(records),"n_episodes":b["n_episodes"],
         "submission_digests":{rid:r["digest"] for rid,r in sorted(records.items())},
         "axes":summarize(records,[i["slot"] for i in capture["items"]]),
         "decision_grade":False,"claim_upgrade":False,
         "limitations":"Descriptive independent clinician agreement on recorded transcripts; not clinical validity, consensus, population inference or deployment approval."}
    _,latest,again,packets_again=_load(source,dest)
    if latest!=b or again!=capture or _records(dest,latest,again,packets_again)!=records:
        _fail("review evidence changed during analysis")
    out["digest"]=buyer_plan._digest(out)
    formal._publish_once(dest/("analysis-"+out["digest"]+".json"),out)
    return out
