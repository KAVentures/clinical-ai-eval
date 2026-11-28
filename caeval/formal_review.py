"""Operator-managed, offline formal review handoff for a completed generic run.

Separate derivative workspace. Existing account submissions are NOT converted
to clinical reviews. Named clinicians return independently issued packets;
the trusted operator attests identity and independence outside the software.
HMAC proves packet binding, not clinical qualifications or verdict authorship.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import uuid
from pathlib import Path

from . import (adjudicate, buyer_agent as buyer, buyer_jobs as jobs,
               buyer_manual as manual, buyer_plan, buyer_research,
               manifest, report, review_packets as packets)

INPUTS = {"results":"results.jsonl","run_meta":"run_meta.json",
          "review_manifest":"review_manifest.json","human_review":"human_review.csv",
          "validity_review":"validity_review.csv"}
VALIDITY = (*adjudicate.VALIDITY_FIELDS,"more_than_one_reasonable_response")
VERDICTS = ("safe","unsafe","cannot_judge")
MAX_BYTES = 32 * 1024 * 1024
MAX_CELLS = 1000
MAX_ROSTERS = 100


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _root(directory):
    buyer.load(directory)
    root=manual._directory(directory,"formal-reviews")
    root.mkdir(mode=0o700,exist_ok=True)
    return root


def _read(path,limit=MAX_BYTES):
    if path.is_symlink() or not path.is_file():
        raise buyer.BuyerError("missing or unsafe formal review file")
    with path.open("rb") as f:
        raw=f.read(limit+1)
    if len(raw)>limit:
        raise buyer.BuyerError("formal review file exceeds size limit")
    return raw


def _json(path):
    return manual._parse(_read(path,manual.MAX_REQUEST_BYTES))


def _csv(raw):
    rows=list(csv.DictReader(io.StringIO(raw.decode("utf-8"),newline="")))
    if len(rows)>MAX_CELLS or any(None in r or any(v is None for v in r.values()) for r in rows):
        raise buyer.BuyerError("malformed or oversized review queue")
    return rows


def _source(directory,job,family):
    manual._identifier(job)
    manual._identifier(family)
    status=jobs.status(directory,job)
    if status["status"]!="completed":
        raise buyer.BuyerError("verified completed job required")
    pkg=next((p for p in status["result"]["packages"] if p["family_id"]==family),None)
    if not pkg:
        raise buyer.BuyerError("family not in completed job")
    ws=jobs._job_path(directory,job)/"out"/("run_"+family)
    meta=buyer_research._preflight(ws)
    if (meta["manifest_hash"]!=pkg["manifest_hash"] or meta["executor"]!="generic_paired_text"
            or manifest.verify_manifest(ws)["verdict"]!=manifest.VALID):
        raise buyer.BuyerError("verified generic paired-text package required")
    blobs={}
    # Verify a private captured copy using the scientific canonical hash
    # contract, rather than compare raw SHA-256 to canonical JSON digests.
    with tempfile.TemporaryDirectory(prefix=".formal-capture-",dir=_root(directory)) as tmp:
        for key,name in INPUTS.items():
            rec=meta["artifacts"][key]
            if not rec.get("present"):
                raise buyer.BuyerError("formal review input missing")
            raw=_read(ws/name)
            dest=Path(tmp)/name
            dest.write_bytes(raw)
            if manifest.hash_file(dest)!=rec["sha256"]:
                raise buyer.BuyerError("source artifact changed during capture")
            blobs[name]=raw
    if jobs.status(directory,job)!=status:
        raise buyer.BuyerError("source job changed during capture")
    return pkg,blobs


def _queues(blobs):
    locked=json.loads(blobs["review_manifest.json"])
    if locked.get("manifest_hash")!=report.manifest_fingerprint(locked):
        raise buyer.BuyerError("locked review manifest changed")
    cells=[r["cell_id"] for r in locked["expected_cells"]]
    safety=_csv(blobs["human_review.csv"])
    validity=_csv(blobs["validity_review.csv"])
    if (not cells or len(cells)>MAX_CELLS or len(cells)!=len(set(cells))
            or locked.get("n_expected")!=len(cells)
            or any(len(rows)!=len(cells) or {r["cell_id"] for r in rows}!=set(cells)
                   for rows in (safety,validity))):
        raise buyer.BuyerError("review queues differ from locked manifest")
    return locked,safety,validity


def _roster(reviewers,validity_reviewer):
    if not isinstance(reviewers,list) or not 2<=len(reviewers)<=5:
        raise buyer.BuyerError("predeclare two to five independent safety reviewers")
    ids=[manual._identifier(r) for r in reviewers]
    vid=manual._identifier(validity_reviewer)
    if len(set(ids+[vid]))!=len(ids)+1:
        raise buyer.BuyerError("distinct safety and validity reviewer IDs required")
    return sorted(ids),vid


def prepare(directory,job,family,reviewers,validity_reviewer,operator,*,confirm_roster=False):
    if confirm_roster is not True:
        raise buyer.BuyerError("explicit independent roster confirmation required")
    operator=manual._identifier(operator)
    ids,vid=_roster(reviewers,validity_reviewer)
    if operator in ids+[vid]:
        raise buyer.BuyerError("issuing operator cannot be a reviewer")
    pkg,blobs=_source(directory,job,family)
    locked,safety,validity=_queues(blobs)
    meta=json.loads(blobs["run_meta.json"])
    predeclared=meta.get("clinical_review") or {}
    assigned=predeclared.get("reviewers") or []
    excluded=set(predeclared.get("excluded_roles") or [])
    if (assigned and not set(ids)<=set(assigned)) or excluded.intersection(ids+[vid]):
        raise buyer.BuyerError("roster conflicts with source project reviewer restrictions")
    synthetic=pkg["claim"]=="demonstration" or not adjudicate.panel_can_anchor_l2(meta["panel"])
    root=_root(directory)
    if sum(p.is_dir() for p in root.iterdir())>=MAX_ROSTERS:
        raise buyer.BuyerError("formal review workspace limit")
    identifier="formal-"+uuid.uuid4().hex
    binding={"schema":"formal-review-handoff/1","id":identifier,"job":job,"family":family,
             "source_manifest":pkg["manifest_hash"],"source_claim":pkg["claim"],
             "review_manifest":locked["manifest_hash"],"run_id":locked["run_id"],
             "reviewers":ids,"validity_reviewer":vid,"issuing_operator":operator,
             "synthetic":synthetic,"inputs":{name:_sha(raw) for name,raw in blobs.items()},
             "identity_proof":"trusted_operator_attestation_only","decision_grade":False}
    with tempfile.TemporaryDirectory(prefix=".formal-prepare-",dir=root) as tmp:
        stage=Path(tmp)/"workspace"
        stage.mkdir(mode=0o700)
        for name,raw in blobs.items():
            with (stage/name).open("xb") as f:
                (stage/name).chmod(0o600)
                f.write(raw)
        for rid in ids+[vid]:
            packet=packets.issue_packet(stage,str(locked["run_id"]),locked["manifest_hash"],rid,
                 "perturbation_validity" if rid==vid else "blinded_adjudicator",
                 safety,synthetic=synthetic)
            packets.write_packet(stage,packet)
        # Bind the issued packet files as well as original input artifacts.
        binding["packets"]={rid:_sha(_read(stage/"review_packets"/(rid+".packet.json"))) for rid in ids+[vid]}
        binding["binding_hash"]=buyer_plan._digest(binding)
        buyer._write_json(stage/"binding.json",binding,create=True)
        (stage/"submissions").mkdir(mode=0o700)
        stage.rename(root/identifier)
    return {"id":identifier,"binding_hash":binding["binding_hash"],"synthetic":synthetic,
            "n_reviewers":len(ids),"n_cells":len(safety),"decision_grade":False}


def _load(directory,identifier):
    manual._identifier(identifier)
    ws=_root(directory)/identifier
    if ws.is_symlink() or not ws.is_dir():
        raise buyer.BuyerError("unknown formal review handoff")
    binding=_json(ws/"binding.json")
    if (binding.get("schema")!="formal-review-handoff/1" or binding.get("id")!=identifier
            or buyer_plan._digest({k:v for k,v in binding.items() if k!="binding_hash"})!=binding.get("binding_hash")):
        raise buyer.BuyerError("formal review binding changed")
    ids,vid=_roster(binding["reviewers"],binding["validity_reviewer"])
    pkg,source=_source(directory,binding["job"],binding["family"])
    if (pkg["manifest_hash"]!=binding["source_manifest"] or pkg["claim"]!=binding["source_claim"]
            or set(binding["inputs"])!=set(INPUTS.values())):
        raise buyer.BuyerError("source package changed")
    blobs={name:_read(ws/name) for name in INPUTS.values()}
    if blobs!=source or {name:_sha(raw) for name,raw in blobs.items()}!=binding["inputs"]:
        raise buyer.BuyerError("frozen review source changed")
    locked,safety,validity=_queues(blobs)
    if (locked["manifest_hash"]!=binding["review_manifest"] or locked["run_id"]!=binding["run_id"]
            or binding["synthetic"] != (pkg["claim"]=="demonstration" or not adjudicate.panel_can_anchor_l2(json.loads(blobs["run_meta.json"])["panel"]))):
        raise buyer.BuyerError("review policy changed")
    if (ws/"review_packets").is_symlink() or (ws/"submissions").is_symlink() or (ws/packets.SECRET_FILE).is_symlink():
        raise buyer.BuyerError("unsafe review storage")
    if set(binding["packets"])!=set(ids+[vid]):
        raise buyer.BuyerError("packet roster changed")
    issued={}
    for rid in ids+[vid]:
        raw=_read(ws/"review_packets"/(rid+".packet.json"))
        if _sha(raw)!=binding["packets"][rid]:
            raise buyer.BuyerError("issued packet changed")
        p=json.loads(raw)
        if (p["synthetic"]!=binding["synthetic"] or p["reviewer_role"]!=("perturbation_validity" if rid==vid else "blinded_adjudicator")
                or packets.verify_packet(ws,p,str(locked["run_id"]),locked["manifest_hash"],
                                         [r["cell_id"] for r in safety],rid)):
            raise buyer.BuyerError("invalid issued packet")
        issued[rid]=p
    return ws,binding,safety,validity,issued


def _slots(binding, rows):
    return {buyer_plan._digest([binding["binding_hash"],r["cell_id"]]):r for r in rows}


def _template(binding,safety,validity,packet,rid):
    is_validity=rid==binding["validity_reviewer"]
    # Do not expose canonical cell IDs: they can encode test/stress labels.
    if is_validity:
        items=[{"slot":slot,"original_case":r["original_case"],
                "perturbed_case":r["perturbed_case"]} for slot,r in sorted(_slots(binding,validity).items())]
        answers=[{"slot":r["slot"],**{q:"" for q in VALIDITY},"notes":""} for r in items]
    else:
        items=[{"slot":slot,"case":r["case_as_shown_to_product"],
                "response":r["product_response"]} for slot,r in sorted(_slots(binding,safety).items())]
        answers=[{"slot":r["slot"],"verdict":"","notes":""} for r in items]
    return {"request":{"schema":"formal-review-packet/1","binding_hash":binding["binding_hash"],
                        "source_manifest":binding["source_manifest"],"packet":packet,
                        "kind":"validity" if is_validity else "safety","items":items},
            "submission":{"answers":answers,"independent_review_confirmed":False}}


def template(directory,identifier,reviewer_id):
    ws,binding,safety,validity,issued=_load(directory,identifier)
    if reviewer_id not in issued:
        raise buyer.BuyerError("reviewer not in locked roster")
    return _template(binding,safety,validity,issued[reviewer_id],reviewer_id)


def _validate_submission(doc,expected):
    if (not isinstance(doc,dict) or set(doc)!={"request","submission"} or doc["request"]!=expected["request"]
            or not isinstance(doc["submission"],dict)
            or set(doc["submission"])!={"answers","independent_review_confirmed"}
            or doc["submission"]["independent_review_confirmed"] is not True):
        raise buyer.BuyerError("changed packet or missing independent review confirmation")
    rows=doc["submission"]["answers"]
    want={r["slot"] for r in expected["submission"]["answers"]}
    validity=expected["request"]["kind"]=="validity"
    fields={"slot","notes",*VALIDITY} if validity else {"slot","notes","verdict"}
    if not isinstance(rows,list) or len(rows)!=len(want):
        raise buyer.BuyerError("one answer per issued cell required")
    seen=set()
    for r in rows:
        if (not isinstance(r,dict) or set(r)!=fields or not isinstance(r["slot"],str)
                or r["slot"] not in want or r["slot"] in seen
                or not isinstance(r["notes"],str) or len(r["notes"])>4000):
            raise buyer.BuyerError("invalid or duplicate review answer")
        if validity:
            if any(r[q] not in ("yes","no","cannot_judge") for q in VALIDITY):
                raise buyer.BuyerError("explicit validity answers required")
        elif r["verdict"] not in VERDICTS:
            raise buyer.BuyerError("explicit safety verdict required")
        seen.add(r["slot"])
    return sorted(rows,key=lambda r:r["slot"])


def _publish_once(path,record):
    """Publish complete private JSON exclusively, including concurrent imports."""
    raw=(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode("utf-8")
    if len(raw)>manual.MAX_REQUEST_BYTES:
        raise buyer.BuyerError("stored review exceeds size limit")
    fd,temp=tempfile.mkstemp(prefix=".formal-write-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temp,path)
        except FileExistsError:
            if _json(path)!=record:
                raise buyer.BuyerError("existing review differs; refusing overwrite")
    finally:
        os.unlink(temp)


def submit(directory,identifier,reviewer_id,document,operator,*,confirm_identity=False):
    if confirm_identity is not True:
        raise buyer.BuyerError("trusted operator must verify reviewer identity and independence externally")
    operator=manual._identifier(operator)
    ws,binding,safety,validity,issued=_load(directory,identifier)
    if reviewer_id not in issued or operator in issued:
        raise buyer.BuyerError("reviewer/receiving operator role conflict")
    expected=_template(binding,safety,validity,issued[reviewer_id],reviewer_id)
    rows=_validate_submission(document,expected)
    payload={"schema":"formal-review-submission/1","binding_hash":binding["binding_hash"],
             "reviewer":reviewer_id,"operator":operator,"operator_attested_identity_and_independence":True,
             "answers":rows}
    payload["digest"]=buyer_plan._digest(payload)
    _publish_once(ws/"submissions"/(reviewer_id+".json"),payload)
    return {"reviewer":reviewer_id,"digest":payload["digest"],"decision_grade":False}


def _submissions(ws,binding,safety,validity,issued):
    records={}
    for rid in issued:
        path=ws/"submissions"/(rid+".json")
        if not path.exists() and not path.is_symlink():
            continue
        r=_json(path)
        if (set(r)!={"schema","binding_hash","reviewer","operator","operator_attested_identity_and_independence","answers","digest"}
                or r["schema"]!="formal-review-submission/1" or r["binding_hash"]!=binding["binding_hash"]
                or r["reviewer"]!=rid or r["operator_attested_identity_and_independence"] is not True
                or manual._identifier(r["operator"]) in issued
                or buyer_plan._digest({k:v for k,v in r.items() if k!="digest"})!=r["digest"]):
            raise buyer.BuyerError("stored review submission changed")
        expected=_template(binding,safety,validity,issued[rid],rid)
        doc={"request":expected["request"],"submission":{"answers":r["answers"],"independent_review_confirmed":True}}
        if _validate_submission(doc,expected)!=r["answers"]:
            raise buyer.BuyerError("stored answers differ from packet")
        records[rid]=r
    if {p.name for p in (ws/"submissions").iterdir()}!={rid+".json" for rid in records}:
        raise buyer.BuyerError("unexpected submission files")
    return records


def _consensus_request(binding,safety,records,issued):
    if set(records)!=set(issued):
        raise buyer.BuyerError("all independent submissions must be locked before consensus")
    slots=_slots(binding,safety)
    votes={rid:{r["slot"]:r["verdict"] for r in records[rid]["answers"]}
           for rid in binding["reviewers"]}
    contested=[]
    for slot,row in sorted(slots.items()):
        labels=[votes[rid][slot] for rid in binding["reviewers"]]
        safe,unsafe=labels.count("safe"),labels.count("unsafe")
        if safe==unsafe and safe:
            contested.append({"slot":slot,"case":row["case_as_shown_to_product"],
                              "response":row["product_response"],"safe_votes":safe,
                              "unsafe_votes":unsafe,"cannot_judge_votes":labels.count("cannot_judge")})
    return {"schema":"formal-consensus-request/1","binding_hash":binding["binding_hash"],
            "submission_digests":{rid:r["digest"] for rid,r in sorted(records.items())},
            "items":contested}


def consensus_template(directory,identifier):
    ws,binding,safety,validity,issued=_load(directory,identifier)
    records=_submissions(ws,binding,safety,validity,issued)
    req=_consensus_request(binding,safety,records,issued)
    if not req["items"]:
        raise buyer.BuyerError("no tied cells require consensus")
    return {"request":req,"submission":{"confirmed_by_original_reviewers":False,
            "answers":[{"slot":r["slot"],"verdict":"","rationale":""} for r in req["items"]]}}


def _consensus_answers(request,answers):
    want={r["slot"] for r in request["items"]}
    if not want or not isinstance(answers,list) or len(answers)!=len(want):
        raise buyer.BuyerError("consensus must address exactly the tied cells")
    seen=set()
    for row in answers:
        if (not isinstance(row,dict) or set(row)!={"slot","verdict","rationale"}
                or not isinstance(row["slot"],str) or row["slot"] not in want or row["slot"] in seen
                or row["verdict"] not in VERDICTS or not isinstance(row["rationale"],str)
                or not row["rationale"].strip() or len(row["rationale"])>4000):
            raise buyer.BuyerError("invalid consensus response")
        seen.add(row["slot"])
    return sorted(answers,key=lambda r:r["slot"])


def submit_consensus(directory,identifier,document,operator,*,confirm_consensus=False):
    if confirm_consensus is not True:
        raise buyer.BuyerError("operator must attest original reviewers confirmed consensus")
    operator=manual._identifier(operator)
    ws,binding,safety,validity,issued=_load(directory,identifier)
    if operator in issued:
        raise buyer.BuyerError("operator cannot also be a reviewer")
    records=_submissions(ws,binding,safety,validity,issued)
    req=_consensus_request(binding,safety,records,issued)
    if (not isinstance(document,dict) or set(document)!={"request","submission"}
            or document["request"]!=req or not isinstance(document["submission"],dict)
            or set(document["submission"])!={"answers","confirmed_by_original_reviewers"}
            or document["submission"]["confirmed_by_original_reviewers"] is not True):
        raise buyer.BuyerError("stale consensus request or missing reviewer confirmation")
    payload={"schema":"formal-consensus/1","binding_hash":binding["binding_hash"],
             "submission_digests":req["submission_digests"],"operator":operator,
             "operator_attested_consensus":True,
             "answers":_consensus_answers(req,document["submission"]["answers"])}
    payload["digest"]=buyer_plan._digest(payload)
    _publish_once(ws/"consensus.json",payload)
    return {"digest":payload["digest"],"decision_grade":False}


def _consensus_record(ws,binding,safety,records,issued):
    path=ws/"consensus.json"
    if not path.exists() and not path.is_symlink():
        return None
    obj=_json(path)
    req=_consensus_request(binding,safety,records,issued)
    if (set(obj)!={"schema","binding_hash","submission_digests","operator","operator_attested_consensus","answers","digest"}
            or obj["schema"]!="formal-consensus/1" or obj["binding_hash"]!=binding["binding_hash"]
            or obj["submission_digests"]!=req["submission_digests"]
            or obj["operator_attested_consensus"] is not True or manual._identifier(obj["operator"]) in issued
            or _consensus_answers(req,obj["answers"])!=obj["answers"]
            or buyer_plan._digest({k:v for k,v in obj.items() if k!="digest"})!=obj["digest"]):
        raise buyer.BuyerError("consensus integrity or roster changed")
    return obj


def status(directory,identifier):
    ws,binding,safety,validity,issued=_load(directory,identifier)
    records=_submissions(ws,binding,safety,validity,issued)
    consensus=_consensus_record(ws,binding,safety,records,issued)
    return {"id":identifier,"source_manifest":binding["source_manifest"],"synthetic":binding["synthetic"],
            "expected_accounts":len(issued),"received":len(records),"n_cells":len(safety),
            "ready_for_adjudication":set(records)==set(issued),"consensus_recorded":consensus is not None,"decision_grade":False}


def _write_csv(path,rows,fields):
    with path.open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def analyze(directory,identifier):
    ws,binding,safety,validity,issued=_load(directory,identifier)
    records=_submissions(ws,binding,safety,validity,issued)
    if set(records)!=set(issued):
        raise buyer.BuyerError("all predeclared reviewers must return packets")
    consensus=_consensus_record(ws,binding,safety,records,issued)
    # Do not alter source or frozen packet inputs. Analysis operates in a
    # disposable private derivative, with the ORIGINAL issued HMAC packets.
    with tempfile.TemporaryDirectory(prefix=".formal-analysis-",dir=_root(directory)) as tmp:
        dest=Path(tmp)
        (dest/"adjudication").mkdir(mode=0o700)
        (dest/"review_packets").mkdir(mode=0o700)
        for name in INPUTS.values():
            raw=_read(ws/name)
            if _sha(raw)!=binding["inputs"][name]:
                raise buyer.BuyerError("frozen review input changed")
            (dest/name).write_bytes(raw)
        (dest/packets.SECRET_FILE).write_bytes(_read(ws/packets.SECRET_FILE,1024))
        for rid,packet in issued.items():
            packets.write_packet(dest,packet)
        safety_slots=_slots(binding,safety)
        validity_slots=_slots(binding,validity)
        files=[]
        for rid in binding["reviewers"]:
            path=dest/"adjudication"/(rid+".csv")
            _write_csv(path,[{"cell_id":safety_slots[r["slot"]]["cell_id"],"reviewer_id":rid,
                             "human_verdict_safe_unsafe":r["verdict"]}
                            for r in records[rid]["answers"]],
                       ["cell_id","reviewer_id","human_verdict_safe_unsafe"])
            files.append(str(path))
        # A "no" on any of the canonical positive validity gates blocks
        # promotion. Do not silently exclude a cell or reweight denominators.
        vrows=records[binding["validity_reviewer"]]["answers"]
        nonvalid=[r["slot"] for r in vrows if any(r[q]!="yes" for q in adjudicate.VALIDITY_FIELDS)]
        if not nonvalid:
            _write_csv(dest/"adjudication"/"validity_review_filled.csv",
                       [{"cell_id":validity_slots[r["slot"]]["cell_id"],**{q:r[q] for q in adjudicate.VALIDITY_FIELDS}} for r in vrows],
                       ["cell_id",*adjudicate.VALIDITY_FIELDS])
        consensus_path=None
        if consensus:
            consensus_path=dest/"adjudication"/"consensus.csv"
            _write_csv(consensus_path,[{"cell_id":safety_slots[r["slot"]]["cell_id"],
                       "consensus_verdict_safe_unsafe":r["verdict"]} for r in consensus["answers"]],
                       ["cell_id","consensus_verdict_safe_unsafe"])
        result=adjudicate.adjudicate(str(dest),files,consensus_file=str(consensus_path) if consensus_path else None)
        from . import review_calibration
        locked=json.loads(_read(dest/"review_manifest.json"))
        source_results=[json.loads(line) for line in _read(dest/"results.jsonl").splitlines() if line.strip()]
        judge_names=json.loads(_read(dest/"run_meta.json"))["panel"]["names"]
        calibration=review_calibration.analyze(
            locked,source_results,judge_names,
            [{safety_slots[r["slot"]]["cell_id"]:r["verdict"] for r in records[rid]["answers"]}
             for rid in binding["reviewers"]],
            {validity_slots[r["slot"]]["cell_id"]:r for r in vrows},
            synthetic=binding["synthetic"])
        if nonvalid and result["l2_adjudication_gate_passed"]:
            raise buyer.BuyerError("invalid perturbations cannot pass formal review")
    # Recheck source, roster and exact submissions after the computation.
    after,latest,s2,v2,p2=_load(directory,identifier)
    if (latest!=binding or _submissions(after,latest,s2,v2,p2)!=records
            or _consensus_record(after,latest,s2,records,p2)!=consensus):
        raise buyer.BuyerError("review evidence changed during analysis")
    out={"schema":"formal-review-analysis/1","binding_hash":binding["binding_hash"],
         "source_manifest":binding["source_manifest"],"source_claim":binding["source_claim"],
         "submission_digests":{rid:r["digest"] for rid,r in sorted(records.items())},
         "canonical_adjudication":result,"selected_queue_calibration":calibration,
         "n_invalid_or_indeterminate_validity":len(nonvalid),
         "consensus_digest":consensus["digest"] if consensus else None,
         "original_package_modified":False,"clinical_identity_independently_verified":False,
         "decision_grade":False,
         "limitations":["Operator attestations are not independent identity proof.",
                        "This derivative does not upgrade the original run or claim authority.",
                        "Consensus is operator-attested and does not change independent labels or inter-rater agreement."]}
    out["digest"]=buyer_plan._digest(out)
    # Canonical adjudication is deterministic for unchanged review data.
    _publish_once(ws/("analysis-"+out["digest"]+".json"),out)
    return out
