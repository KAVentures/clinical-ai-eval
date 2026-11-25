"""Audited hospital gate-review lifecycle, separate from clinical authorization."""
import json
from . import hospital_referral as h, diagnostic_imaging as d
from . import institution_access as access, unified_assessment as u

POLICY="hospital-referral-policy-frozen"
SNAPSHOT="hospital-referral-snapshot-frozen"
RATING="hospital-referral-gate-locked"
DECISION="hospital-referral-disposition-locked"
MAX_POLICIES=60


def _schema(conn):
    u._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS hospital_referral_policies(
        id TEXT PRIMARY KEY,programme TEXT NOT NULL,vendor TEXT NOT NULL,payload TEXT NOT NULL,
        digest TEXT NOT NULL,actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")
    for table in ("hospital_referral_snapshots","hospital_referral_decisions"):
        conn.execute("CREATE TABLE IF NOT EXISTS "+table+"""(
            policy TEXT PRIMARY KEY REFERENCES hospital_referral_policies(id),
            payload TEXT NOT NULL,digest TEXT NOT NULL,actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS hospital_referral_ratings(
        policy TEXT NOT NULL REFERENCES hospital_referral_policies(id),gate TEXT NOT NULL,
        payload TEXT NOT NULL,digest TEXT NOT NULL,actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(policy,gate))""")


def _inventory(conn):
    u._inventory(conn)
    for table,event,keys in (
        ("hospital_referral_policies",POLICY,("id","digest")),
        ("hospital_referral_snapshots",SNAPSHOT,("policy","digest")),
        ("hospital_referral_ratings",RATING,("policy","gate","digest")),
        ("hospital_referral_decisions",DECISION,("policy","digest"))):
        rows=conn.execute("SELECT audit_seq,actor,"+",".join(keys)+" FROM "+table).fetchall()
        expected={(r["audit_seq"],r["actor"],":".join(r[k] for k in keys)) for r in rows}
        actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
            "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(event,))}
        if len(expected)!=len(rows) or expected!=actual:
            raise access.AccessDenied("hospital policy audit inventory mismatch")


def _policy_preview(directory,conn,spec):
    programme,seq=u._read(conn,spec["programme_id"])
    if not u._current(directory,programme):
        raise access.AccessDenied("current programme required")
    p=h.policy(programme,spec)
    for gate in p["spec"]["gates"]:
        u._actor(conn,{"user_id":gate["reviewer"],"role":"reviewer"},"reviewer")
    if conn.execute("SELECT 1 FROM hospital_referral_policies WHERE id=?",(spec["policy_id"],)).fetchone():
        raise access.AccessDenied("policy ID already frozen")
    # No target-vendor association before initial registration. A later
    # protocol needs a new programme; do not retrospectively select gates.
    for req in p["requirements"]:
        if u._link(conn,programme,seq,req["id"]):
            raise access.AccessDenied("register policy before vendor source association")
    return p


def preview(directory,actor,spec):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        return _policy_preview(directory,conn,spec)


def freeze(directory,actor,spec,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit hospital policy approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        p=_policy_preview(directory,conn,spec)
        if p["digest"]!=approval:
            raise access.AccessDenied("policy differs from approved preview")
        if conn.execute("SELECT count(*) FROM hospital_referral_policies").fetchone()[0]>=MAX_POLICIES:
            raise access.AccessDenied("policy limit reached")
        ident=spec["policy_id"]
        seq=u._audit(conn,actor,POLICY,ident+":"+approval)
        conn.execute("INSERT INTO hospital_referral_policies VALUES(?,?,?,?,?,?,?)",
                     (ident,spec["programme_id"],spec["vendor_id"],json.dumps(p),approval,actor["user_id"],seq))
        return ident


def _get(conn,ident):
    d._require(d._token(ident),"invalid policy ID")
    row=conn.execute("SELECT * FROM hospital_referral_policies WHERE id=?",(ident,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown policy")
    p=json.loads(row["payload"])
    programme,pseq=u._read(conn,row["programme"])
    if (h.policy(programme,p["spec"])!=p or p["digest"]!=row["digest"]
            or p["spec"]["policy_id"]!=ident or p["spec"]["vendor_id"]!=row["vendor"]
            or row["audit_seq"]<=pseq):
        raise access.AccessDenied("hospital policy binding mismatch")
    for req in p["requirements"]:
        linked=conn.execute("SELECT audit_seq FROM unified_assessment_links WHERE programme=? AND requirement=?",
                            (row["programme"],req["id"])).fetchone()
        if linked and linked["audit_seq"]<=row["audit_seq"]:
            raise access.AccessDenied("policy registered after source association")
    snaprow=conn.execute("SELECT * FROM hospital_referral_snapshots WHERE policy=?",(ident,)).fetchone()
    ratings=[]
    decision=None
    if not snaprow:
        if conn.execute("SELECT 1 FROM hospital_referral_ratings WHERE policy=?",(ident,)).fetchone() or conn.execute(
                "SELECT 1 FROM hospital_referral_decisions WHERE policy=?",(ident,)).fetchone():
            raise access.AccessDenied("review without snapshot")
        return p,None,None,ratings,decision
    payload=json.loads(snaprow["payload"])
    d._keys(payload,"coverage snapshot")
    coverage,snap=payload["coverage"],payload["snapshot"]
    u.verify_export(u._package(coverage))
    if (coverage["programme"]!=programme or h.snapshot(p,coverage)!=snap
            or snaprow["digest"]!=d._digest(payload) or snaprow["audit_seq"]<=row["audit_seq"]):
        raise access.AccessDenied("review snapshot integrity mismatch")
    last=snaprow["audit_seq"]
    for r in conn.execute("SELECT * FROM hospital_referral_ratings WHERE policy=? ORDER BY gate",(ident,)):
        doc=json.loads(r["payload"])
        if (h.rating(p,snap,r["actor"],doc["rating"])!=doc or doc["digest"]!=r["digest"]
                or doc["rating"]["gate_id"]!=r["gate"] or r["audit_seq"]<=snaprow["audit_seq"]):
            raise access.AccessDenied("gate review binding mismatch")
        ratings.append(doc)
        last=max(last,r["audit_seq"])
    dec=conn.execute("SELECT * FROM hospital_referral_decisions WHERE policy=?",(ident,)).fetchone()
    if dec:
        decision=json.loads(dec["payload"])
        if (h.disposition(p,snap,ratings,decision["disposition"],decision["rationale"])!=decision
                or decision["digest"]!=dec["digest"] or dec["audit_seq"]<=last):
            raise access.AccessDenied("hospital disposition integrity mismatch")
    return p,coverage,snap,ratings,decision


def _live(directory,conn,actor,p):
    return u._status(directory,conn,actor,p["spec"]["programme_id"])


def _snapshot_preview(directory,conn,actor,ident):
    p,_,snap,_,_=_get(conn,ident)
    if snap:
        raise access.AccessDenied("review snapshot already frozen")
    coverage=_live(directory,conn,actor,p)
    if coverage["state"]!="current":
        raise access.AccessDenied("current programme required")
    payload={"coverage":coverage,"snapshot":h.snapshot(p,coverage)}
    return {"payload":payload,"approval":d._digest(payload)}


def snapshot_preview(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        return _snapshot_preview(directory,conn,actor,ident)


def start_review(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit exact review snapshot approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        p=_snapshot_preview(directory,conn,actor,ident)
        if p["approval"]!=approval:
            raise access.AccessDenied("review snapshot changed since preview")
        seq=u._audit(conn,actor,SNAPSHOT,ident+":"+approval)
        conn.execute("INSERT INTO hospital_referral_snapshots VALUES(?,?,?,?,?)",
                     (ident,json.dumps(p["payload"]),approval,actor["user_id"],seq))
        return approval


def assigned(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"reviewer")
        _inventory(conn)
        p,_,snap,ratings,decision=_get(conn,ident)
        gates=[r for r in p["spec"]["gates"] if r["reviewer"]==actor["user_id"]]
        if not gates or not snap or decision:
            raise access.AccessDenied("active assigned gate review required")
        locked={r["rating"]["gate_id"] for r in ratings}
        return {"policy_id":ident,"target":p["target"],"intended_use":p["spec"]["intended_use"],
                "snapshot":snap,"gates":[{**r,"locked":r["id"] in locked} for r in gates]}


def submit(directory,actor,ident,doc,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit external evidence attestation required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"reviewer")
        _inventory(conn)
        p,_,snap,ratings,decision=_get(conn,ident)
        if not snap or decision:
            raise access.AccessDenied("active review snapshot required")
        r=h.rating(p,snap,actor["user_id"],doc)
        gate=doc["gate_id"]
        if any(x["rating"]["gate_id"]==gate for x in ratings):
            raise access.AccessDenied("gate rating already locked")
        seq=u._audit(conn,actor,RATING,ident+":"+gate+":"+r["digest"])
        conn.execute("INSERT INTO hospital_referral_ratings VALUES(?,?,?,?,?,?)",
                     (ident,gate,json.dumps(r),r["digest"],actor["user_id"],seq))
        return r["digest"]


def _current(directory,conn,actor,p,snap):
    return bool(snap and h.snapshot(p,_live(directory,conn,actor,p))==snap)


def _coverage(directory,conn,actor,p,snap,ratings,decision):
    result=h.evaluate(p,snap,ratings) if snap else None
    current=_current(directory,conn,actor,p,snap)
    return {"id":p["spec"]["policy_id"],"programme_id":p["spec"]["programme_id"],
            "vendor_id":p["spec"]["vendor_id"],"target":p["target"],"policy_digest":p["digest"],
            "state":"decided" if decision else "reviewing" if snap else "registered",
            "snapshot_current":current,"n_gates":len(p["spec"]["gates"]),"n_reviewed":len(ratings),
            "referral_conditions_met":bool(current and result and result["referral_conditions_met"]),
            "disposition":decision["disposition"] if decision else None,
            "disposition_digest":decision["digest"] if decision else None,
            "decision_grade":False,"deployment_authorized":False}


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator","observer","reviewer")
        _inventory(conn)
        out=[]
        for row in conn.execute("SELECT id FROM hospital_referral_policies ORDER BY audit_seq").fetchall():
            p,_,snap,ratings,decision=_get(conn,row["id"])
            if actor["role"]=="reviewer":
                gates=[r for r in p["spec"]["gates"] if r["reviewer"]==actor["user_id"]]
                if gates and snap and not decision:
                    out.append({"id":row["id"],"state":"assigned"})
            else:
                out.append(_coverage(directory,conn,actor,p,snap,ratings,decision))
        return out


def detail(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        p,_,snap,ratings,decision=_get(conn,ident)
        return {"coverage":_coverage(directory,conn,actor,p,snap,ratings,decision),
                "policy":p,"snapshot":snap,"ratings":ratings,"decision":decision,
                "evaluation":h.evaluate(p,snap,ratings) if snap else None}


def _decision_preview(directory,conn,actor,ident,kind,rationale):
    p,_,snap,ratings,prior=_get(conn,ident)
    if not snap or prior:
        raise access.AccessDenied("active undecided review required")
    if kind=="refer_external_assessment" and not _current(directory,conn,actor,p,snap):
        raise access.AccessDenied("stale review cannot be referred")
    # A stop/hold may still be recorded on stale or incomplete evidence.
    return h.disposition(p,snap,ratings,kind,rationale)


def decision_preview(directory,actor,ident,kind,rationale):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        return _decision_preview(directory,conn,actor,ident,kind,rationale)


def decide(directory,actor,ident,kind,rationale,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit committee disposition required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        doc=_decision_preview(directory,conn,actor,ident,kind,rationale)
        if doc["digest"]!=approval:
            raise access.AccessDenied("review or disposition changed since approval")
        seq=u._audit(conn,actor,DECISION,ident+":"+approval)
        conn.execute("INSERT INTO hospital_referral_decisions VALUES(?,?,?,?,?)",
                     (ident,json.dumps(doc),approval,actor["user_id"],seq))
        return approval


def export(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted policy export required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        p,coverage,snap,ratings,decision=_get(conn,ident)
        if not decision or decision["digest"]!=approval:
            raise access.AccessDenied("exact locked disposition required")
        doc=h.package(coverage["programme"],coverage,p,snap,ratings,decision)
        u._audit(conn,actor,"hospital-referral-evidence-exported",ident+":"+doc["digest"])
        return doc
