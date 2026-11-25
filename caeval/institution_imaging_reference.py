"""Institutional independent imaging reference review, before candidate exposure."""
import json
from . import (imaging_reference as ref, diagnostic_imaging as imaging,
               institution_imaging as studies, institution_access as access)

PROTOCOL = "imaging-reference-protocol-frozen"
RATING = "imaging-reference-rating-locked"
LINK = "imaging-reference-finalized"


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS imaging_reference_protocols(
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, digest TEXT NOT NULL,
        actor TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS imaging_reference_ratings(
        study TEXT NOT NULL REFERENCES imaging_reference_protocols(id),
        actor TEXT NOT NULL REFERENCES users(id), payload TEXT NOT NULL,
        digest TEXT NOT NULL, audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(study,actor))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS imaging_reference_links(
        study TEXT PRIMARY KEY REFERENCES imaging_reference_protocols(id),
        plan_digest TEXT NOT NULL, digest TEXT NOT NULL, actor TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE)""")


def _inventory(conn):
    for table, action, cols in (
            ("imaging_reference_protocols", PROTOCOL, ("id","digest")),
            ("imaging_reference_ratings", RATING, ("study","actor","digest")),
            ("imaging_reference_links", LINK, ("study","plan_digest","digest"))):
        rows = conn.execute("SELECT audit_seq,actor," + ",".join(dict.fromkeys(cols)) + " FROM " + table).fetchall()
        expected = {(r["audit_seq"],r["actor"],":".join(r[k] for k in cols)) for r in rows}
        events = conn.execute("SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(action,)).fetchall()
        actual = {(r["seq"],r["actor"],r["object_id"]) for r in events}
        if len(expected)!=len(rows) or actual!=expected:
            raise access.AccessDenied("reference review audit inventory mismatch")


def reserved(conn, ident):
    return conn.execute("SELECT 1 FROM imaging_reference_protocols WHERE id=?",(ident,)).fetchone() is not None


def _roles(conn,p):
    for uid in p["readers"]+[p["adjudicator"]]:
        studies._actor(conn,{"user_id":uid,"role":"reviewer"},"reviewer")


def _read(conn, ident):
    if not imaging._token(ident):
        raise access.AccessDenied("invalid reference study identifier")
    row=conn.execute("SELECT * FROM imaging_reference_protocols WHERE id=?",(ident,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown reference study")
    p=json.loads(row["payload"])
    ref.verify_protocol(p)
    if p["digest"]!=row["digest"] or p["spec"]["study_id"]!=ident:
        raise access.AccessDenied("reference protocol integrity mismatch")
    docs,seqs={},{}
    adjudication=None
    adjud_seq=None
    for r in conn.execute("SELECT * FROM imaging_reference_ratings WHERE study=?",(ident,)):
        uid=r["actor"]
        doc=json.loads(r["payload"])
        if imaging._digest(doc)!=r["digest"] or r["audit_seq"]<=row["audit_seq"]:
            raise access.AccessDenied("reference rating integrity mismatch")
        if uid in p["readers"]:
            ref._read_ratings(ref.reader_packet(p,uid),doc)
            docs[uid]=doc
            seqs[uid]=r["audit_seq"]
        elif uid==p["adjudicator"]:
            adjudication=doc
            adjud_seq=r["audit_seq"]
        else:
            raise access.AccessDenied("unassigned reference review")
    if adjudication is not None:
        packet=ref.adjudication_packet(p,docs,p["adjudicator"])
        if not packet["cases"] or adjud_seq<=max(seqs.values()):
            raise access.AccessDenied("invalid adjudication order or inventory")
        ref._read_ratings(packet,adjudication)
    link=conn.execute("SELECT * FROM imaging_reference_links WHERE study=?",(ident,)).fetchone()
    study=conn.execute("SELECT * FROM diagnostic_imaging_studies WHERE id=?",(ident,)).fetchone()
    if bool(link)!=bool(study):
        raise access.AccessDenied("reference reservation or final study linkage missing")
    evidence=None
    if link:
        plan=json.loads(study["payload"])
        evidence=ref.package(p,docs,adjudication,plan)
        if (link["plan_digest"]!=plan["digest"] or study["digest"]!=plan["digest"]
                or link["digest"]!=evidence["digest"] or link["actor"]!=study["actor"]
                or not max([row["audit_seq"],*seqs.values(),adjud_seq or 0])<study["audit_seq"]<link["audit_seq"]):
            raise access.AccessDenied("reference finalization integrity mismatch")
    return p,docs,adjudication,evidence


def check_link(conn,plan):
    ident=plan["spec"]["study_id"]
    if reserved(conn,ident):
        _,_,_,evidence=_read(conn,ident)
        if not evidence or evidence["final_plan"]["digest"]!=plan["digest"]:
            raise access.AccessDenied("reference final plan mismatch")
        return evidence["digest"]
    if plan["spec"]["reference_method"]==ref.METHOD:
        raise access.AccessDenied("independent review protocol required")
    return None


def _prepare(conn,actor,spec,readers,adjudicator):
    studies._actor(conn,actor,"admin")
    p=ref.protocol(spec,readers,adjudicator,nonce="0"*64)
    _roles(conn,p)
    ident=p["spec"]["study_id"]
    if reserved(conn,ident) or conn.execute("SELECT 1 FROM diagnostic_imaging_studies WHERE id=?",(ident,)).fetchone():
        raise access.AccessDenied("study ID already reserved")
    return p


def preview(directory,actor,spec,readers,adjudicator):
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN")
        studies._inventory(conn)
        p=_prepare(conn,actor,spec,readers,adjudicator)
        return {"spec":p["spec"],"readers":p["readers"],"adjudicator":p["adjudicator"],
                "approval_digest":p["digest"],"n_cases":len(p["spec"]["cases"])}


def freeze(directory,actor,spec,readers,adjudicator,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit prospective reference approval required")
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studies._inventory(conn)
        p=_prepare(conn,actor,spec,readers,adjudicator)
        if approval!=p["digest"]:
            raise access.AccessDenied("reference protocol differs from preview")
        if conn.execute("SELECT count(*) FROM imaging_reference_protocols").fetchone()[0]>=studies.MAX_STUDIES:
            raise access.AccessDenied("reference protocol limit")
        p=ref.protocol(spec,readers,adjudicator)
        ident=p["spec"]["study_id"]
        seq=studies._audit(conn,actor,PROTOCOL,ident+":"+p["digest"])
        conn.execute("INSERT INTO imaging_reference_protocols VALUES(?,?,?,?,?)",
                     (ident,studies._json(p),p["digest"],actor["user_id"],seq))
        return ident


def _state(p,docs,adj,evidence):
    n=len(docs)
    ready=False
    disagreement=None
    if n==2:
        disagreement=len(ref.adjudication_packet(p,docs,p["adjudicator"])["cases"])
        ready=not disagreement or adj is not None
    return {"id":p["spec"]["study_id"],"n_cases":len(p["spec"]["cases"]),
            "state":"finalized" if evidence else "ready" if ready else "reviewing",
            "n_primary_locked":n,"adjudication_required":None if disagreement is None else bool(disagreement),
            "adjudication_locked":adj is not None,"reference_digest":evidence["digest"] if evidence else None,
            "decision_grade":False}


def overview(directory,actor,*,committee=False):
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN")
        studies._actor(conn,actor,*(("admin","operator","observer") if committee else ("admin","reviewer")))
        studies._inventory(conn)
        out=[]
        for row in conn.execute("SELECT id FROM imaging_reference_protocols ORDER BY audit_seq").fetchall():
            p,docs,adj,evidence=_read(conn,row["id"])
            uid=actor["user_id"]
            if committee or actor["role"]=="admin":
                out.append(_state(p,docs,adj,evidence))
            elif uid in p["readers"] or uid==p["adjudicator"]:
                # Readers do not get other-reader progress or adjudication
                # outcomes. Only their own assignment and lock state.
                out.append({"id":row["id"],"n_cases":len(p["spec"]["cases"]),
                            "state":"finalized" if evidence else "assigned",
                            "role":"primary" if uid in p["readers"] else "adjudicator",
                            "locked":uid in docs or (uid==p["adjudicator"] and adj is not None)})
        return out


def _packet(p,docs,adj,evidence,uid):
    if evidence:
        raise access.AccessDenied("reference review finalized")
    if uid in p["readers"]:
        if uid in docs:
            raise access.AccessDenied("primary review already locked")
        return ref.reader_packet(p,uid)
    if uid==p["adjudicator"] and adj is None:
        packet=ref.adjudication_packet(p,docs,uid)
        if packet["cases"]:
            return packet
    raise access.AccessDenied("no outstanding assigned review")


def packet(directory,actor,ident):
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studies._actor(conn,actor,"reviewer")
        studies._inventory(conn)
        p,docs,adj,evidence=_read(conn,ident)
        packet=_packet(p,docs,adj,evidence,actor["user_id"])
        studies._audit(conn,actor,"imaging-reference-packet-opened",ident+":"+imaging._digest(packet))
        return packet


def lock(directory,actor,ident,packet_digest,values,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit independent review lock required")
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studies._actor(conn,actor,"reviewer")
        studies._inventory(conn)
        p,docs,adj,evidence=_read(conn,ident)
        uid=actor["user_id"]
        packet=_packet(p,docs,adj,evidence,uid)
        if imaging._digest(packet)!=packet_digest:
            raise access.AccessDenied("review packet differs from approved assignment")
        doc=ref.ratings(packet,values)
        digest=imaging._digest(doc)
        seq=studies._audit(conn,actor,RATING,ident+":"+uid+":"+digest)
        conn.execute("INSERT INTO imaging_reference_ratings VALUES(?,?,?,?,?)",
                     (ident,uid,studies._json(doc),digest,seq))
        return digest


def _binding(p,docs,adj):
    return imaging._digest([p["digest"],{k:imaging._digest(v) for k,v in docs.items()},
                            imaging._digest(adj) if adj is not None else None])


def final_preview(directory,actor,ident):
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN")
        studies._actor(conn,actor,"admin")
        studies._inventory(conn)
        p,docs,adj,evidence=_read(conn,ident)
        if evidence:
            raise access.AccessDenied("reference study already finalized")
        _,summary=ref.resolve(p,docs,adj)
        return {"approval_digest":_binding(p,docs,adj),"summary":summary}


def finalize(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit reference finalization required")
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studies._actor(conn,actor,"admin")
        studies._inventory(conn)
        p,docs,adj,prior=_read(conn,ident)
        if prior or _binding(p,docs,adj)!=approval:
            raise access.AccessDenied("reference inventory changed or already finalized")
        spec,_=ref.resolve(p,docs,adj)
        if conn.execute("SELECT count(*) FROM diagnostic_imaging_studies").fetchone()[0]>=studies.MAX_STUDIES:
            raise access.AccessDenied("imaging study limit reached")
        # Publish the final imaging study and its reference provenance in the
        # same writer transaction. Candidate exchange cannot precede this.
        plan=imaging.freeze(spec,confirmed=True)
        evidence=ref.package(p,docs,adj,plan)
        seq=studies._audit(conn,actor,studies.FROZEN,ident+":"+plan["digest"])
        conn.execute("INSERT INTO diagnostic_imaging_studies VALUES(?,?,?,?,?)",
                     (ident,studies._json(plan),plan["digest"],actor["user_id"],seq))
        seq=studies._audit(conn,actor,LINK,ident+":"+plan["digest"]+":"+evidence["digest"])
        conn.execute("INSERT INTO imaging_reference_links VALUES(?,?,?,?,?)",
                     (ident,plan["digest"],evidence["digest"],actor["user_id"],seq))
        return plan["digest"]


def export(directory,actor,ident,expected_digest,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted reference export required")
    with access.db(directory) as conn:
        studies._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        studies._actor(conn,actor,"admin")
        studies._inventory(conn)
        _,_,_,evidence=_read(conn,ident)
        if not evidence or evidence["digest"]!=expected_digest:
            raise access.AccessDenied("exact finalized reference evidence required")
        studies._audit(conn,actor,"imaging-reference-private-evidence-exported",ident+":"+expected_digest)
        return evidence
