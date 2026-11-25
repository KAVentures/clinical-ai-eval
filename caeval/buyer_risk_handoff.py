"""Approved institutional handoff of a completed risk-bound canonical job.

Copies only manifest-bound evidence and records all family results together.
No model calls, arbitrary paths, credentials, or clinical maturity changes.
"""
import json
import os
import tempfile
from pathlib import Path

from . import (buyer_agent as buyer, buyer_jobs as jobs, buyer_assessment as assessment,
               buyer_manual as manual, buyer_research as research, manifest,
               procurement_workflow as pw, buyer_risk_activation as activation,
               institution_access as access, institution_setup as setup,
               institution_committee as committee)

EVENT="risk-bound-job-ingested"


def _study(directory):
    root=buyer._root(directory)
    dest=root/"assessment"
    if dest.is_symlink() or (dest/pw.STATE_FILE).is_symlink():
        raise access.AccessDenied("invalid study path")
    state=pw.load(dest)
    binding=state["conditions"].get("risk_activation")
    if not binding:
        raise access.AccessDenied("activated risk study required")
    activation._check_conditions(state,binding)
    return root,dest,state,binding


def _candidate(directory,state,binding,job_id,*,conn=None):
    job=jobs.status(directory,job_id)
    if job["status"]!="completed":
        raise access.AccessDenied("verified completed job required")
    record=assessment.check(directory,job["assessment_id"],conn=conn)
    if record.get("conditions_hash")!=state["conditions_hash"]:
        raise access.AccessDenied("job not prepared under this exact study")
    candidate=[(vid,entry) for vid,entry in activation.candidates(binding).items()
               if entry["plan_id"]==record["plan_id"]]
    if len(candidate)!=1:
        raise access.AccessDenied("job does not identify exactly one frozen candidate")
    vid,entry=candidate[0]
    refs=job["result"]["packages"]
    if len(refs)!=len(binding["families"]) or {r["family_id"] for r in refs}!=set(binding["families"]):
        raise access.AccessDenied("job lacks complete frozen family inventory")
    return job,vid,refs


def inventory(directory,actor):
    with access.db(directory) as conn:
        setup._actor(conn,actor)
    root,dest,state,binding=_study(directory)
    rows=[]
    for job in jobs.list_jobs(directory):
        if job["status"]!="completed":
            continue
        try:
            checked,vid,refs=_candidate(directory,state,binding,job["id"])
        except (ValueError,RuntimeError,OSError,KeyError,TypeError):
            continue
        existing=state["results"].get(vid,{})
        status=("already_recorded" if all(existing.get(r["family_id"],{}).get("environment",{}).get("package_digest")==r["manifest_hash"] for r in refs)
                else "conflict" if existing else "ready")
        rows.append({"job_id":job["id"],"vendor_id":vid,"status":status,
                     "packages":[{"family_id":r["family_id"],"manifest_hash":r["manifest_hash"]} for r in refs],
                     "decision_grade":False})
    return rows


def _copy_package(source,dest,expected_digest,budget):
    if source.is_symlink() or not source.is_dir():
        raise access.AccessDenied("invalid source package")
    metadata=research._preflight(source)
    if metadata["manifest_hash"]!=expected_digest or manifest.verify_manifest(source)["verdict"]!=manifest.VALID:
        raise access.AccessDenied("source package changed")
    paths={manifest.MANIFEST_FILE,*metadata["review_inputs"],
           *(rec["path"] for rec in metadata["artifacts"].values() if rec["present"])}
    dest.mkdir(mode=0o700)
    for relative in sorted(paths):
        if not research._relative(relative):
            raise access.AccessDenied("unsafe package path")
        src=source/relative
        # The preflight refused linked directories. Check path components again
        # at the point of reading, under the trusted server filesystem boundary.
        cursor=source
        for part in Path(relative).parts:
            cursor=cursor/part
            if cursor.is_symlink():
                raise access.AccessDenied("linked evidence refused")
        if not src.is_file():
            raise access.AccessDenied("manifest-bound file unavailable")
        with src.open("rb") as f:
            raw=f.read(budget+1)
        budget-=len(raw)
        if budget<0:
            raise access.AccessDenied("job package handoff exceeds byte limit")
        out=dest/relative
        out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with out.open("xb") as f:
            out.chmod(0o600)
            f.write(raw)
    if research._preflight(dest)!=metadata or manifest.verify_manifest(dest)["verdict"]!=manifest.VALID:
        raise access.AccessDenied("staged package failed independent manifest verification")
    return budget


def ingest(directory,actor,job_id,conditions_hash,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit confidential evidence handoff approval required")
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        access.require(actor,"admin")
        conn.execute("BEGIN IMMEDIATE")
        root,dest,state,binding=_study(directory)
        if conditions_hash!=state["conditions_hash"]:
            raise access.AccessDenied("study changed; reopen handoff")
        job,vid,refs=_candidate(directory,state,binding,job_id,conn=conn)
        existing=state["results"].get(vid,{})
        if existing:
            if set(existing)=={r["family_id"] for r in refs} and all(
                    existing[r["family_id"]]["environment"]["package_digest"]==r["manifest_hash"] for r in refs):
                vendor=next(v for v in state["vendors"] if v["vendor_id"]==vid)
                for ref in refs:
                    committee._package(root,vendor,ref["family_id"],existing[ref["family_id"]])
                return {"status":"already_recorded","vendor_id":vid,"job_id":job_id,"decision_grade":False}
            raise access.AccessDenied("existing result inventory cannot be replaced")
        packages=dest/"packages"
        if packages.is_symlink() or (packages.exists() and not packages.is_dir()):
            raise access.AccessDenied("invalid package staging directory")
        packages.mkdir(mode=0o700,exist_ok=True)
        final=packages/manual._identifier(job_id)
        if final.exists() or final.is_symlink():
            raise access.AccessDenied("unreferenced staging directory exists; IT recovery required")
        with tempfile.TemporaryDirectory(prefix=".handoff-",dir=dest) as tmp:
            scratch=Path(tmp)
            staged=scratch/"packages"
            staged.mkdir(mode=0o700)
            pw.save(scratch,state)
            budget=research.MAX_BYTES
            for ref in refs:
                family=manual._identifier(ref["family_id"])
                source=jobs._job_path(directory,job_id)/"out"/("run_"+family)
                target=staged/family
                budget=_copy_package(source,target,ref["manifest_hash"],budget)
                rebuilt=pw.ingest_package(scratch,vid,target)
                result=rebuilt["results"][vid][family]
                if result["environment"]["package_digest"]!=ref["manifest_hash"]:
                    raise access.AccessDenied("ingested package differs from job")
                result["package"]["workspace"]=str(final/family)
                pw.save(scratch,rebuilt)
            if jobs.status(directory,job_id)!=job or pw.load(dest)!=state:
                raise access.AccessDenied("source job or study changed during staging")
            rebuilt=pw.load(scratch)
            staged.rename(final)
            # Atomic state replacement after all packages are complete. A crash
            # between rename and replacement may leave an orphan; never delete
            # or reuse it automatically. Trusted CLI writers must be quiesced.
            vendor=next(v for v in rebuilt["vendors"] if v["vendor_id"]==vid)
            for ref in refs:
                committee._package(root,vendor,ref["family_id"],rebuilt["results"][vid][ref["family_id"]])
            buyer._write_json(dest/pw.STATE_FILE,rebuilt)
        access._audit(conn,actor["user_id"],EVENT,job_id)
        return {"status":"recorded","vendor_id":vid,"job_id":job_id,
                "families":sorted(r["family_id"] for r in refs),"decision_grade":False}
