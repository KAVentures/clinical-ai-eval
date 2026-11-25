"""Version-only unified programme successors linked to native approved reassessments.

No old source association transfers, and coverage change is not a performance
change. If definitions or required family selection change incompatibly, create
a separately scoped programme instead.
"""
import json
from . import unified_assessment as u, diagnostic_imaging as d
from . import institution_access as access, version_reassessment as version
from . import research_buyer_coverage as bridge, buyer_plan

EVENT="unified-assessment-successor-frozen"


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS unified_assessment_successors(
        successor TEXT PRIMARY KEY REFERENCES unified_assessment_programmes(id),
        baseline TEXT NOT NULL REFERENCES unified_assessment_programmes(id),
        payload TEXT NOT NULL,digest TEXT NOT NULL,actor TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE)""")


def _inventory(conn):
    rows=conn.execute("SELECT successor,baseline,digest,actor,audit_seq FROM unified_assessment_successors").fetchall()
    actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
        "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,))}
    expected={(r["audit_seq"],r["actor"],r["baseline"]+":"+r["successor"]+":"+r["digest"]) for r in rows}
    if len(expected)!=len(rows) or actual!=expected:
        raise access.AccessDenied("programme successor audit inventory mismatch")


def _option(directory,conn,old_pid,new_pid):
    if old_pid==new_pid:
        current=bridge._buyer_plan(directory,new_pid)
        return {"baseline_plan_id":old_pid,"successor_plan_id":new_pid,
                "baseline_version":current["plan"]["target"]["version"],
                "successor_version":current["plan"]["target"]["version"],
                "native_reassessment_digest":None}
    version._schema(conn)
    old=version._baseline(directory,old_pid)
    matches=[r for r in version._history(conn,old_pid) if r["successor_id"]==new_pid]
    if len(matches)!=1:
        raise access.AccessDenied("approved native version reassessment required")
    record=matches[0]
    version._validate_successor(directory,old,record)
    bridge._buyer_plan(directory,new_pid)
    return {"baseline_plan_id":old_pid,"successor_plan_id":new_pid,
            "baseline_version":record["baseline_version"],"successor_version":record["successor_version"],
            "native_reassessment_digest":record["digest"]}


def choices(directory,actor,baseline):
    with access.db(directory) as conn:
        u._schema(conn)
        _schema(conn)
        version._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        u._inventory(conn)
        _inventory(conn)
        doc,_=u._read(conn,baseline)
        result=[]
        for p in doc["plans"]:
            pid=p["plan_id"]
            options=[]
            candidates=[pid]+[r["successor_id"] for r in version._history(conn,pid)]
            for candidate in candidates:
                try:
                    options.append(_option(directory,conn,pid,candidate))
                except u.ERRORS:
                    continue
            result.append({"plan":p,"options":options})
        return result


def _material(directory,conn,baseline,successor,mapping):
    old,seq=u._read(conn,baseline)
    d._require(d._token(successor) and successor!=baseline,"distinct successor programme ID required")
    d._require(isinstance(mapping,dict) and set(mapping)=={p["plan_id"] for p in old["plans"]}
               and all(d._hex(v) for v in mapping.values()) and len(set(mapping.values()))==len(mapping),
               "exact one-to-one plan mapping required")
    options=[_option(directory,conn,pid,mapping[pid]) for pid in sorted(mapping)]
    d._require(any(r["native_reassessment_digest"] is not None for r in options),
               "at least one approved version change required")
    new=u._material(directory,{"programme_id":successor,"plans":list(mapping.values()),
                               "requirements":[{**r,"plan_id":mapping[r["plan_id"]]} for r in old["requirements"]]})
    # Programme requirement selectors and canonical gap semantics must not
    # drift in this version-only route. Native definition changes are allowed
    # only when they do not alter the selected suite inventory.
    new_by={p["plan_id"]:p for p in new["plans"]}
    for p in old["plans"]:
        q=new_by[mapping[p["plan_id"]]]
        if any(p[k]!=q[k] for k in ("vendor_id","profiles","required_suites","runnable_suites","required_but_not_run")):
            raise access.AccessDenied("canonical suite scope changed; use a separately scoped programme")
    lineage=d._sealed({"schema":"unified-assessment-successor/1","baseline_id":baseline,
                       "baseline_digest":old["digest"],"successor_id":successor,
                       "successor_digest":new["digest"],"plans":options,
                       "prior_evidence_transferred":False,"performance_comparison_authorized":False,
                       "decision_grade":False})
    return new,lineage,seq


def preview(directory,actor,baseline,successor,mapping):
    with access.db(directory) as conn:
        u._schema(conn)
        _schema(conn)
        version._schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        u._inventory(conn)
        _inventory(conn)
        if conn.execute("SELECT 1 FROM unified_assessment_programmes WHERE id=?",(successor,)).fetchone():
            raise access.AccessDenied("successor programme ID already frozen")
        new,lineage,_=_material(directory,conn,baseline,successor,mapping)
        return {"programme":new,"lineage":lineage}


def freeze(directory,actor,baseline,successor,mapping,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit successor approval required")
    with access.db(directory) as conn:
        u._schema(conn)
        _schema(conn)
        version._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        u._inventory(conn)
        _inventory(conn)
        new,lineage,_=_material(directory,conn,baseline,successor,mapping)
        if lineage["digest"]!=approval:
            raise access.AccessDenied("successor differs from approved preview")
        if conn.execute("SELECT count(*) FROM unified_assessment_programmes").fetchone()[0]>=u.MAX_PROGRAMMES:
            raise access.AccessDenied("programme limit reached")
        if conn.execute("SELECT 1 FROM unified_assessment_programmes WHERE id=?",(successor,)).fetchone():
            raise access.AccessDenied("successor programme ID already frozen")
        seq=u._audit(conn,actor,u.FREEZE,successor+":"+new["digest"])
        conn.execute("INSERT INTO unified_assessment_programmes VALUES(?,?,?,?,?)",
                     (successor,json.dumps(new),new["digest"],actor["user_id"],seq))
        seq=u._audit(conn,actor,EVENT,baseline+":"+successor+":"+approval)
        conn.execute("INSERT INTO unified_assessment_successors VALUES(?,?,?,?,?,?)",
                     (successor,baseline,json.dumps(lineage),approval,actor["user_id"],seq))
        return successor


def _read(conn,row):
    doc=json.loads(row["payload"])
    d._keys(doc,"schema baseline_id baseline_digest successor_id successor_digest plans prior_evidence_transferred performance_comparison_authorized decision_grade digest")
    old,oseq=u._read(conn,row["baseline"])
    new,nseq=u._read(conn,row["successor"])
    if (doc["schema"]!="unified-assessment-successor/1" or doc["baseline_id"]!=row["baseline"]
            or doc["successor_id"]!=row["successor"] or doc["baseline_digest"]!=old["digest"]
            or doc["successor_digest"]!=new["digest"] or doc["digest"]!=row["digest"]
            or d._digest({k:v for k,v in doc.items() if k!="digest"})!=row["digest"]
            or any(doc[k] is not False for k in ("prior_evidence_transferred","performance_comparison_authorized","decision_grade"))
            or not oseq<nseq<row["audit_seq"]):
        raise access.AccessDenied("programme lineage integrity mismatch")
    mapping={p["baseline_plan_id"]:p["successor_plan_id"] for p in doc["plans"]}
    if (len(mapping)!=len(doc["plans"]) or set(mapping)!={p["plan_id"] for p in old["plans"]}
            or set(mapping.values())!={p["plan_id"] for p in new["plans"]}
            or new["requirements"]!=[{**r,"plan_id":mapping[r["plan_id"]]} for r in old["requirements"]]):
        raise access.AccessDenied("programme lineage requirement inventory mismatch")
    version._schema(conn)
    old_by={p["plan_id"]:p for p in old["plans"]}
    new_by={p["plan_id"]:p for p in new["plans"]}
    for pair in doc["plans"]:
        d._keys(pair,"baseline_plan_id successor_plan_id baseline_version successor_version native_reassessment_digest")
        a,b=pair["baseline_plan_id"],pair["successor_plan_id"]
        p,q=old_by[a],new_by[b]
        if (pair["baseline_version"]!=p["target"]["version"] or pair["successor_version"]!=q["target"]["version"]
                or p["vendor_id"]!=q["vendor_id"] or p["target"]["name"]!=q["target"]["name"]):
            raise access.AccessDenied("programme lineage candidate mismatch")
        if a==b:
            if pair["native_reassessment_digest"] is not None:
                raise access.AccessDenied("unchanged plan cannot claim reassessment")
        else:
            matches=[r for r in version._history(conn,a) if r["successor_id"]==b]
            if len(matches)!=1 or matches[0]["digest"]!=pair["native_reassessment_digest"]:
                raise access.AccessDenied("native reassessment lineage changed")
    if not any(p["native_reassessment_digest"] is not None for p in doc["plans"]):
        raise access.AccessDenied("version lineage lacks a native change")
    return doc


def inventory(directory,actor):
    with access.db(directory) as conn:
        u._schema(conn)
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator","observer")
        u._inventory(conn)
        _inventory(conn)
        return [_read(conn,row) for row in conn.execute("SELECT * FROM unified_assessment_successors ORDER BY audit_seq").fetchall()]


def compare(directory,actor,successor):
    with access.db(directory) as conn:
        u._schema(conn)
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator","observer")
        u._inventory(conn)
        _inventory(conn)
        row=conn.execute("SELECT * FROM unified_assessment_successors WHERE successor=?",(successor,)).fetchone()
        if not row:
            raise access.AccessDenied("unknown programme successor")
        lineage=_read(conn,row)
        old=u._status(directory,conn,actor,lineage["baseline_id"])
        new=u._status(directory,conn,actor,lineage["successor_id"])
        previous={r["requirement"]["id"]:r for r in old["requirements"]}
        comparison=[]
        for current in new["requirements"]:
            rid=current["requirement"]["id"]
            prior=previous[rid]
            comparison.append({"requirement_id":rid,"kind":current["requirement"]["kind"],
                               "baseline_version":prior["target"]["version"],
                               "successor_version":current["target"]["version"],
                               "baseline_state":prior["state"],"successor_state":current["state"],
                               "baseline_source_digest":prior["locked_source"]["evidence_digest"] if prior["locked_source"] else None,
                               "successor_source_digest":current["locked_source"]["evidence_digest"] if current["locked_source"] else None})
        return {"schema":"unified-assessment-successor-coverage/1","lineage":lineage,
                "requirements":comparison,"performance_comparison_authorized":False,
                "decision_grade":False}
