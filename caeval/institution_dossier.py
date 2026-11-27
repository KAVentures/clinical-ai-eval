"""Committee-safe cross-workflow evidence inventory.

This module publishes aggregate review coverage and verified assessment
references, not review answers, patient transcripts, execution approvals,
credentials, reviewer identities or a combined procurement score.
"""
from __future__ import annotations

from . import (buyer_agent as buyer, buyer_assessment as assessment, buyer_jobs as jobs,
               buyer_manual as manual, buyer_plan, institution_access as access,
               institution_conversation as conversation, institution_formal as formal,
               institution_lifecycle as lifecycle, institution_monitoring as monitoring,
               institution_patient_review as transcript, institution_patient_validity as validity)

ERRORS=(OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError,StopIteration)


def _account(db,actor):
    conversation._actor(db,actor,"admin","operator","observer")


def _generic(directory,known):
    entries=[]
    for stored in jobs.list_jobs(directory):
        row=jobs.status(directory,stored["id"])
        entry={"id":row["id"],"kind":"single_turn","status":row["status"],
               "mode":row["mode"],"vendor_id":None,"target_version":None,"packages":[],
               "required_but_not_run":[]}
        try:
            if row["status"]=="invalid":
                raise buyer.BuyerError("invalid assessment")
            if row["status"]=="completed":
                bound=manual._read(jobs._job_path(directory,row["id"])/"binding.json")
                if (bound["assessment_id"]!=row["assessment_id"] or
                        buyer_plan._digest({k:v for k,v in bound.items() if k!="assessment_id"})!=row["assessment_id"]):
                    raise buyer.BuyerError("assessment binding changed")
            else:
                bound=assessment.check(directory,row["assessment_id"])
            exchange=manual._load(directory,bound["exchange_id"])
            if exchange["vendor_id"] not in known or exchange["pack_content_hash"]!=bound["pack_content_hash"]:
                raise buyer.BuyerError("candidate association unavailable")
            entry.update(vendor_id=exchange["vendor_id"],target_version=exchange["target"]["version"])
            if row["status"]=="completed":
                if jobs.status(directory,row["id"])!=row:
                    raise buyer.BuyerError("assessment changed during capture")
                entry["packages"]=[{k:p[k] for k in ("family_id","manifest_hash","claim","n_responses","review_count")}
                                   for p in row["result"]["packages"]]
                entry["required_but_not_run"]=row["result"]["required_but_not_run"]
        except ERRORS:
            entry.update(status="unavailable",vendor_id=None,target_version=None,packages=[],required_but_not_run=[])
        entries.append(entry)
    return entries


def _conversations(directory,db,known):
    conversation._schema(db)
    entries=[]
    for item in db.execute("SELECT id FROM conversation_jobs ORDER BY id").fetchall():
        ident=item["id"]
        entry={"id":ident,"kind":"patient_conversation","status":"unavailable",
               "mode":None,"vendor_id":None,"target_version":None,"packages":[],"required_but_not_run":[]}
        try:
            row=conversation._row(db,ident)
            b=row["binding"]
            if b["vendor_id"] not in known:
                raise buyer.BuyerError("candidate association unavailable")
            entry.update(status=row["status"],mode=b["mode"],vendor_id=b["vendor_id"],
                         target_version=b["target"]["version"],required_but_not_run=b["required_but_not_run"])
            if row["status"]=="completed":
                r=conversation._verify(directory,row)
                if r!=row["result"]:
                    raise buyer.BuyerError("conversation receipt changed")
                entry["packages"]=[{"family_id":conversation.FAMILY,"manifest_hash":r["manifest_hash"],
                                    "claim":r["claim"],"n_episodes":r["n_episodes"],"review_count":r["n_review_items"],
                                    "receipt_digest":buyer_plan._digest(r)}]
        except ERRORS:
            entry.update(status="unavailable",vendor_id=None,target_version=None,packages=[],required_but_not_run=[])
        entries.append(entry)
    return entries


def _formal(directory,db):
    formal._schema(db)
    result=[]
    for reg in db.execute("SELECT id,job,family FROM formal_browser_handoffs ORDER BY id").fetchall():
        entry={"id":reg["id"],"kind":"formal_paired_review","job":reg["job"],"family":reg["family"],
               "state":"unavailable"}
        try:
            ws,b,safety,valid,issued,records=formal._load(directory,db,reg["id"])
            pending=sum(formal._pending(db,reg["id"],rid,b,safety,valid,issued) is not None for rid in issued)
            if set(records)==set(issued):
                consensus=formal._verified_consensus(db,reg["id"],ws,b,safety,records,issued)
                state=("consensus_recorded" if consensus else "consensus_required"
                       if formal.formal._consensus_request(b,safety,records,issued)["items"] else "all_attested_no_ties")
            else:
                state="awaiting_attestations"
            entry.update(state=state,assigned=len(issued),submitted=pending,attested=len(records),
                         evidence_digest=buyer_plan._digest([b["binding_hash"],sorted(r["digest"] for r in records.values()),
                             consensus if set(records)==set(issued) else None]))
        except ERRORS:
            entry.update(state="unavailable")
        result.append(entry)
    return result


def _patient_reviews(directory,db):
    result=[]
    for module,table,kind in ((transcript,"patient_review_handoffs","patient_transcript_review"),
                              (validity,"patient_validity_handoffs","patient_world_validity")):
        module._schema(db)
        for row in db.execute("SELECT * FROM "+table+" ORDER BY id").fetchall():
            entry={"id":row["id"],"kind":kind,"state":"unavailable"}
            if module is transcript:
                entry["job"]=row["job"]
            # A source association is published only after source verification.
            try:
                b,digest,items=module._load(directory,db,row["id"])
                records=[module._submission(db,b,digest,items,rid) for rid in b["reviewers"]]
                n_attested=sum(bool(r and r[2]) for r in records)
                entry.update(state="all_attested" if n_attested==len(records) else "awaiting_attestations",
                             assigned=len(records),submitted=sum(r is not None for r in records),
                             attested=n_attested,n_items=len(items),
                             evidence_digest=buyer_plan._digest([digest,sorted(r[1] for r in records if r and r[2])]))
                if module is validity:
                    entry["pack_content_hash"]=b["source"]["descriptor"]["content_hash"]
                    # No claim of linkage based merely on the content hash.
                    # Exact source/definition linkage remains the validity report's job.
            except ERRORS:
                entry.update(state="unavailable")
            result.append(entry)
    return result


def _operations(directory,actor):
    pilots=[]
    for p in lifecycle.overview(directory,actor)["pilots"]:
        pilots.append({k:p[k] for k in ("id","vendor_id","product_version","state","revision","review_date","blocking_open","overdue")})
        pilots[-1]["open_items"]=[{k:i[k] for k in ("id","kind","severity","due","revision")}
                                 for i in p["items"] if i["state"]=="open"]
        pilots[-1]["evidence"]=[{k:e[k] for k in ("job_id","purpose","integrity","family_count","assessment_type")}
                               for e in p["evidence"]]
    schedules=[]
    for s in monitoring.overview(directory,actor):
        entry={k:s[k] for k in ("id","pilot_id","baseline_version","target_version","enabled","next_due","interval_seconds")}
        entry["occurrences"]=[]
        for r in s["occurrences"]:
            occ={k:r[k] for k in ("id","due","state","skipped_intervals")}
            if r["state"]=="completed":
                report=r["report"]
                if not isinstance(report,dict) or not report.get("families"):
                    occ["state"]="invalid"
                else:
                    occ["report_digest"]=r["report_digest"]
                    occ["metric_states"]={family:{m:v["status"] for m,v in sorted(metrics.items())}
                                          for family,metrics in sorted(report["families"].items())}
            entry["occurrences"].append(occ)
        schedules.append(entry)
    return pilots,schedules


def collect(directory,actor,vendor_ids):
    with access.db(directory) as db:
        _account(db,actor)
    assessments=_generic(directory,set(vendor_ids))
    with access.db(directory) as db:
        _account(db,actor)
        assessments.extend(_conversations(directory,db,set(vendor_ids)))
        reviews=_formal(directory,db)+_patient_reviews(directory,db)
        from . import research_program
        programmes=research_program.committee_inventory(directory,db,actor,set(vendor_ids))
    pilots,schedules=_operations(directory,actor)
    with access.db(directory) as db:
        _account(db,actor)
        access._audit(db,actor["user_id"],"committee-lifecycle-inventory-opened")
    return {"schema":"committee-lifecycle-inventory/1",
            "assessments":sorted(assessments,key=lambda r:r["id"]),
            "reviews":sorted(reviews,key=lambda r:r["id"]),
            "pilots":sorted(pilots,key=lambda r:r["id"]),
            "schedules":sorted(schedules,key=lambda r:r["id"]),
            "experimental_research_programmes":programmes,
            "decision_grade":False,"deployment_authorized":False,
            "interpretation":"Inventory only. Attested coverage is not a safety verdict. Unavailable, incomplete and experimental evidence cannot establish clinical readiness."}
