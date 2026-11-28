"""Assigned reviewer inbox; no peer ratings, candidate outputs or identifiers.

Native reviewer endpoints remain the authorization and submission boundary.
An assignment listing is not proof that a packet is presently openable.
"""
import sqlite3
from urllib.parse import urlencode
from . import institution_access as access, unified_assessment as u


def _route(path,**query):
    return path+"?"+urlencode(query)


def _ordinary(directory,actor):
    return [{"type":"standard","state":"submitted" if r["submitted"] else "assigned",
             "label":r["family"],"route":None if r["submitted"] else _route("/review",job=r["job"],family=r["family"])}
            for r in access.assignments(directory,actor)]


def _formal(directory,actor):
    from . import institution_formal as formal
    return [{"type":"formal","state":"submitted" if r["submitted"] else "assigned",
             "label":r["kind"],"route":None if r["submitted"] else _route("/formal-review",id=r["id"])}
            for r in formal.overview(directory,actor)]


def _validation(directory,actor):
    from . import validation_cohort as validation
    return [{"type":"validation","state":"open_native_assignment" if r["linked"] else "waiting_for_evidence",
             "label":"Prospective validation cohort","route":_route("/validation-review",id=r["id"]) if r["linked"] else None}
            for r in validation.inventory(directory,actor)]


def _imaging(directory,actor):
    from . import institution_imaging_reference as imaging
    result=[]
    for r in imaging.overview(directory,actor):
        if r["state"]=="finalized" or r["locked"]:
            state="submitted_or_finalized"
            route=None
        elif r["role"]=="adjudicator":
            # Do not infer readiness from other readers' progress or reveal
            # whether disagreements exist. Native route decides.
            state="conditional_assignment"
            route=_route("/imaging-reference-review",id=r["id"])
        else:
            state="assigned"
            route=_route("/imaging-reference-review",id=r["id"])
        result.append({"type":"imaging_reference","state":state,"label":r["role"],"route":route})
    return result


def _rag(directory,actor):
    from . import institution_rag_grounding as rag
    return [{"type":"rag_grounding","state":r["state"],"label":"Citation grounding review",
             "route":_route("/rag-grounding-review",id=r["id"]) if r["state"]=="assigned" else None}
            for r in rag.overview(directory,actor)]


def _policy(directory,actor):
    from . import institution_hospital_referral as policy
    result=[]
    for r in policy.overview(directory,actor):
        own=policy.assigned(directory,actor,r["id"])
        pending=any(not g["locked"] for g in own["gates"])
        result.append({"type":"hospital_gate","state":"assigned" if pending else "submitted",
                       "label":"Local policy gate review",
                       "route":_route("/hospital-referral-assigned",id=r["id"]) if pending else None})
    return result


def overview(directory,actor):
    with access.db(directory) as conn:
        u._actor(conn,actor,"reviewer")
    sections=[]
    for name,fn in (("standard",_ordinary),("formal",_formal),("validation",_validation),
                    ("imaging_reference",_imaging),("rag_grounding",_rag),("hospital_gate",_policy)):
        try:
            rows=fn(directory,actor)
            sections.append({"type":name,"state":"available","assignments":rows})
        except u.ERRORS+(sqlite3.Error,):
            sections.append({"type":name,"state":"unavailable","assignments":[]})
    with access.db(directory) as conn:
        u._actor(conn,actor,"reviewer")
    return {"sections":sections,"partial":any(s["state"]!="available" for s in sections),
            "decision_grade":False,"deployment_authorized":False}


def render(directory,actor):
    from .institution_web import page,esc
    doc=overview(directory,actor)
    body="<p><a href='/'>Workspace</a></p><p>Your own assigned reviews only. Native workflows determine packet access and lock state. An unavailable inventory or conditional assignment does not imply no remaining work.</p>"
    if doc["partial"]:
        body+="<p><strong>Some review inventories are unavailable.</strong></p>"
    for section in doc["sections"]:
        body+="<section><h2>"+esc(section["type"].replace("_"," "))+"</h2><p>"+esc(section["state"])+"</p>"
        for r in section["assignments"]:
            body+="<p>"+esc(r["label"])+" · "+esc(r["state"].replace("_"," "))
            if r["route"]:
                body+=" · <a href='"+esc(r["route"])+"'>Open native assignment</a>"
            body+="</p>"
        body+="</section>"
    return page("My independent reviews",body)
