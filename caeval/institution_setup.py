"""Institutional operator assessment preparation.

Reuse the canonical buyer workflow instead of exposing the standalone console.
All browser writes are serialized by the institutional access database. Trusted
filesystem/CLI operators must not concurrently edit the buyer register.
No execution, remote calls, arbitrary filesystem paths or credential input.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import institution_access as access, buyer_agent as buyer, buyer_plan as plans
from . import buyer_manual as manual, buyer_questionnaire as questionnaire
from . import buyer_assessment as assessment, buyer_jobs as jobs, intake, project

MAX_RECORDS=200
MAX_UPLOAD=8*1024*1024


def _actor(conn,actor):
    access.require(actor,"admin","operator")
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"]!=actor["role"]:
        raise access.AccessDenied("active operator account required")


def _text(value,limit=300):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise access.AccessDenied("invalid required text")
    return value.strip()


def _exact(form,fields):
    if set(form)!=set(fields):
        raise access.AccessDenied("unexpected or incomplete form fields")


def _ids(directory,folder,extension=".json"):
    path=manual._directory(directory,folder)
    if not path.is_dir():
        return []
    items=[]
    for p in path.iterdir():
        if len(items)>=MAX_RECORDS:
            break
        if p.is_symlink():
            continue
        identifier=p.stem if extension else p.name
        if extension and (not p.is_file() or p.suffix!=extension):
            continue
        if not extension and not p.is_dir():
            continue
        try:
            manual._identifier(identifier,digest=True)
        except buyer.BuyerError:
            continue
        items.append(identifier)
    return sorted(items)


def overview(directory,actor):
    with access.db(directory) as conn:
        _actor(conn,actor)
    state=buyer.status(directory)
    saved=[]
    for ident in _ids(directory,"plans"):
        try:
            p=plans.load(directory,ident)
            saved.append({"id":ident,"vendor_id":p["vendor_id"],"target":p["plan"]["target"],
                          "current":p["definitions_current"],"selection":p["plan"]["selection"]})
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):
            saved.append({"id":ident,"invalid":True})
    exchanges=[]
    for ident in _ids(directory,"manual-exchanges"):
        try:
            e=manual._load(directory,ident)
            exchanges.append({"id":ident,"target":e["target"],"n_slots":len(e["slots"])})
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):
            exchanges.append({"id":ident,"invalid":True})
    submissions=[]
    for ident in _ids(directory,"manual-submissions"):
        try:
            s=manual.submission_status(directory,ident)
            submissions.append({"id":ident,"status":s["status"],"provided":s["n_provided"],"expected":s["n_expected"]})
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):
            submissions.append({"id":ident,"invalid":True})
    prepared=[]
    for ident in _ids(directory,"prepared-assessments",""):
        try:
            p=assessment.check(directory,ident)
            prepared.append({"id":ident,"mode":p["mode"],"n_responses":p["n_responses"]})
        except (OSError,ValueError,RuntimeError,KeyError,TypeError):
            prepared.append({"id":ident,"invalid":True})
    return {"vendors":state["vendors"],"plans":saved,"exchanges":exchanges,
            "submissions":submissions,"prepared":prepared,"packs":manual.packs(directory),
            "panels":assessment.panels(directory),"jobs":jobs.list_jobs(directory),
            "decision_grade":False}


def download(directory,actor,kind,identifier):
    with access.db(directory) as conn:
        _actor(conn,actor)
        if kind=="questionnaire":
            result=questionnaire.template(directory,identifier)
        elif kind=="prompts":
            result=manual.template(directory,identifier)
        else:
            raise access.AccessDenied("unsupported download")
        access._audit(conn,actor["user_id"],"setup-download-"+kind,identifier)
    return result


def dispatch(directory,actor,action,form,blob=None,filename=""):
    """Serialize institutional writes and audit their success.

    Existing research CLI remains an administrator-only maintenance interface.
    The SQLite transaction serializes browser requests, not arbitrary OS edits.
    """
    with access.db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor)
        if action=="vendor":
            _exact(form,("vendor_id","name"))
            result=buyer.add_vendor(directory,form["vendor_id"],_text(form["name"]))
            identifier=result["vendor_id"]
        elif action=="document":
            _exact(form,("vendor_id","kind","title"))
            if form["kind"] not in buyer.EVIDENCE_KINDS or not isinstance(blob,bytes) or not 0<len(blob)<=MAX_UPLOAD:
                raise access.AccessDenied("approved evidence kind and bounded document required")
            if (Path(directory)/"evidence").is_symlink():
                raise access.AccessDenied("linked evidence directory refused")
            with tempfile.TemporaryDirectory(prefix=".upload-",dir=access._root(directory)) as tmp:
                file=Path(tmp)/"document"
                file.write_bytes(blob)
                result=buyer.add_evidence(directory,form["vendor_id"],form["kind"],str(file),
                                          title=_text(form["title"]),source_filename=_text(filename,200))
            identifier=result["evidence_id"]
        elif action=="questionnaire":
            _exact(form,("vendor_id",))
            if not isinstance(blob,bytes) or len(blob)>questionnaire.MAX_BYTES:
                raise access.AccessDenied("bounded questionnaire required")
            if (Path(directory)/"evidence").is_symlink():
                raise access.AccessDenied("linked evidence directory refused")
            result=questionnaire.import_response(directory,form["vendor_id"],blob)
            identifier=result["evidence_id"]
        elif action=="plan":
            profile_keys={"profile_"+p for p in intake.TARGET_PROFILES}
            required={"vendor_id","target_name","target_version",*intake.INTAKE_QUESTIONS}
            if not required<=set(form) or set(form)-required-profile_keys:
                raise access.AccessDenied("invalid plan form")
            if any(form[k]!="yes" for k in set(form)&profile_keys):
                raise access.AccessDenied("invalid selected profile")
            result=plans.preview(directory,{"vendor_id":form["vendor_id"],"target_name":form["target_name"],
                "target_version":form["target_version"],"profiles":[p for p in intake.TARGET_PROFILES if "profile_"+p in form],
                "answers":{q:form[q] for q in intake.INTAKE_QUESTIONS}})
            identifier=result["plan_id"]
        elif action=="exchange":
            _exact(form,("plan_id","pack_id"))
            result=manual.prepare(directory,form["plan_id"],form["pack_id"])
            identifier=result["request"]["exchange_id"]
        elif action=="responses":
            _exact(form,())
            if not isinstance(blob,bytes):
                raise access.AccessDenied("completed response template required")
            result=manual.import_response(directory,blob)
            identifier=result["submission_id"]
        elif action=="assessment":
            required={"submission_id","panel_id",*project.MANDATORY_GOVERNANCE}
            _exact(form,required)
            result=assessment.prepare(directory,form["submission_id"],form["panel_id"],
                                      {q:form[q] for q in project.MANDATORY_GOVERNANCE})
            identifier=result["assessment_id"]
        elif action=="queue":
            _exact(form,("assessment_id",))
            result=jobs.enqueue(directory,form["assessment_id"])
            identifier=result["id"]
        elif action=="cancel":
            _exact(form,("job_id",))
            result=jobs.cancel(directory,form["job_id"])
            identifier=result["id"]
        else:
            raise access.AccessDenied("unsupported setup action")
        access._audit(conn,actor["user_id"],"setup-"+action,identifier)
    # Only a local identifier, never raw case content or provider config.
    return {"id":identifier,"decision_grade":False}
