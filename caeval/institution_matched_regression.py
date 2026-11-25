"""Institutional registration and sealed replay of matched version studies."""
import json
from . import matched_version_regression as regression, diagnostic_imaging as d
from . import institution_access as access, unified_assessment as u

EVENT="matched-version-regression-frozen"
MAX_REGISTRATIONS=40


def schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS matched_version_regressions(
        id TEXT PRIMARY KEY,kind TEXT NOT NULL,baseline TEXT NOT NULL,successor TEXT NOT NULL,
        payload TEXT NOT NULL,actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        UNIQUE(kind,successor))""")


def inventory(conn):
    rows=conn.execute("SELECT * FROM matched_version_regressions").fetchall()
    expected={(r["audit_seq"],r["actor"],r["id"]) for r in rows}
    actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
        "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(EVENT,))}
    if len(expected)!=len(rows) or actual!=expected:
        raise access.AccessDenied("longitudinal registration audit inventory mismatch")


def _native(conn,kind):
    if kind=="imaging":
        from . import institution_imaging as module
        tables=("diagnostic_imaging_studies","diagnostic_imaging_submissions","diagnostic_imaging_seals")
        action="diagnostic-imaging-candidate-exported"
    elif kind=="rag_grounding":
        from . import institution_rag_grounding as module
        tables=("rag_grounding_studies","rag_grounding_traces","rag_grounding_seals")
        action="rag-grounding-candidate-exported"
    else:
        raise ValueError("unknown longitudinal modality")
    module._schema(conn)
    module._inventory(conn)
    return module,tables,action


def _sources(conn,kind,baseline,successor):
    d._require(d._token(baseline) and d._token(successor) and baseline!=successor,"distinct valid study IDs required")
    module,(study,submission,seal),action=_native(conn,kind)
    old=module._get(conn,baseline)
    new=module._get(conn,successor)
    old_evidence,new_evidence=old[-1],new[-1]
    if not old_evidence:
        raise access.AccessDenied("sealed baseline required")
    old_seq=conn.execute("SELECT audit_seq FROM "+seal+" WHERE study=?",(baseline,)).fetchone()[0]
    new_seq=conn.execute("SELECT audit_seq FROM "+study+" WHERE id=?",(successor,)).fetchone()[0]
    prefix=successor+":"
    exported=conn.execute("SELECT min(seq) FROM audit WHERE action=? AND outcome='ok' AND substr(object_id,1,?)=?",
                          (action,len(prefix),prefix)).fetchone()[0]
    submitted=conn.execute("SELECT min(audit_seq) FROM "+submission+" WHERE study=?",(successor,)).fetchone()[0]
    new_seal=conn.execute("SELECT audit_seq FROM "+seal+" WHERE study=?",(successor,)).fetchone()
    exposure=[x for x in (exported,submitted,new_seal[0] if new_seal else None) if x is not None]
    return old_evidence,new[0],new_evidence,max(old_seq,new_seq),min(exposure) if exposure else None


def _preview(conn,kind,baseline,successor,spec):
    old,plan,_,_,exposure=_sources(conn,kind,baseline,successor)
    if exposure is not None:
        raise access.AccessDenied("register before any recorded successor exposure")
    if conn.execute("SELECT 1 FROM matched_version_regressions WHERE kind=? AND successor=?",(kind,successor)).fetchone():
        raise access.AccessDenied("successor already registered")
    return regression.protocol(kind,old,plan,spec)


def preview(directory,actor,kind,baseline,successor,spec):
    with access.db(directory) as conn:
        schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        inventory(conn)
        return _preview(conn,kind,baseline,successor,spec)


def freeze(directory,actor,kind,baseline,successor,spec,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit longitudinal protocol approval required")
    with access.db(directory) as conn:
        schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        inventory(conn)
        doc=_preview(conn,kind,baseline,successor,spec)
        if doc["digest"]!=approval:
            raise access.AccessDenied("longitudinal protocol differs from approval")
        if conn.execute("SELECT count(*) FROM matched_version_regressions").fetchone()[0]>=MAX_REGISTRATIONS:
            raise access.AccessDenied("longitudinal registration limit")
        seq=u._audit(conn,actor,EVENT,approval)
        conn.execute("INSERT INTO matched_version_regressions VALUES(?,?,?,?,?,?,?)",
                     (approval,kind,baseline,successor,json.dumps(doc),actor["user_id"],seq))
        return approval


def _read(conn,row):
    old,plan,new,earliest,exposure=_sources(conn,row["kind"],row["baseline"],row["successor"])
    doc=json.loads(row["payload"])
    if (regression.protocol(row["kind"],old,plan,doc["parameters"])!=doc or doc["digest"]!=row["id"]
            or row["audit_seq"]<=earliest or (exposure is not None and row["audit_seq"]>=exposure)):
        raise access.AccessDenied("longitudinal registration binding or order mismatch")
    if new and row["kind"]=="imaging" and not new["report"]["roster_complete"]:
        return doc,None,"successor_roster_incomplete"
    result=regression.analyse(old,new,doc) if new else None
    return doc,result,"sealed_descriptive_comparison" if result else "registered"


def _coverage(doc,result,state):
    return {"id":doc["digest"],"kind":doc["kind"],"baseline_study":doc["baseline_study"],
            "successor_study":doc["successor_study"],"baseline":doc["baseline"],"successor":doc["successor"],
            "primary_endpoint":doc["parameters"]["endpoint"],
            "state":state,
            "comparison_digest":result["digest"] if result else None,"decision_grade":False}


def overview(directory,actor):
    with access.db(directory) as conn:
        schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin","operator","observer")
        inventory(conn)
        return [_coverage(*_read(conn,row)) for row in conn.execute(
            "SELECT * FROM matched_version_regressions ORDER BY audit_seq").fetchall()]


def detail(directory,actor,ident):
    with access.db(directory) as conn:
        schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        inventory(conn)
        row=conn.execute("SELECT * FROM matched_version_regressions WHERE id=?",(ident,)).fetchone()
        if not row:
            raise access.AccessDenied("unknown longitudinal registration")
        doc,result,state=_read(conn,row)
        return {"coverage":_coverage(doc,result,state),"protocol":doc,"result":result}


def export(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit restricted comparison export required")
    with access.db(directory) as conn:
        schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        inventory(conn)
        row=conn.execute("SELECT * FROM matched_version_regressions WHERE id=?",(ident,)).fetchone()
        if not row:
            raise access.AccessDenied("unknown longitudinal registration")
        _,result,_=_read(conn,row)
        if not result or result["digest"]!=approval:
            raise access.AccessDenied("exact sealed comparison required")
        u._audit(conn,actor,"matched-version-regression-exported",ident+":"+approval)
        return result
