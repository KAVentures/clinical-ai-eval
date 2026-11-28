"""Buyer-owned cross-modality evidence requirements, without score pooling.

This is an administrative coverage matrix. Source modules retain their own
scientific definitions, run approvals and verification. A linked source is
not a clinical pass or evidence of prospective collection.
"""
import json
from . import (buyer_plan, buyer_manual as manual, buyer_jobs as jobs,
               institution_access as access, research_buyer_coverage as bridge,
               diagnostic_imaging as d, executors)

FREEZE="unified-assessment-programme-frozen"
LINK="unified-assessment-source-locked"
MAX_PROGRAMMES=40
MAX_PLANS=24
MAX_REQUIREMENTS=80
KINDS=("text","conversation","research","imaging","fhir","hl7","image_text","rag","rag_grounding")
ERRORS=(ValueError,RuntimeError,OSError,KeyError,TypeError,AttributeError,IndexError,StopIteration)
LIMITATIONS=[
    "Administrative source coverage only; no combined score, ranking, procurement approval or deployment authorization.",
    "A frozen programme and locked association do not prove prospective source collection or task relevance.",
    "Source-specific experimental and demonstration limitations remain; an available source is not a passing result.",
    "Missing, stale and unavailable evidence never satisfies a requirement.",
    "Imaging identity binds the registered vendor identifier and exact declared version; task relevance and actual model identity remain operator assertions.",
]


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS unified_assessment_programmes(
        id TEXT PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT NULL,
        actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS unified_assessment_links(
        programme TEXT NOT NULL REFERENCES unified_assessment_programmes(id),
        requirement TEXT NOT NULL,source_id TEXT NOT NULL,payload TEXT NOT NULL,
        digest TEXT NOT NULL,actor TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(programme,requirement))""")


def _actor(conn,actor,*roles):
    access.require(actor,*roles)
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"]!=actor["role"]:
        raise access.AccessDenied("active programme account required")


def _audit(conn,actor,action,obj):
    access._audit(conn,actor["user_id"],action,obj)
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _inventory(conn):
    for table,action,cols in (
        ("unified_assessment_programmes",FREEZE,("id","digest")),
        ("unified_assessment_links",LINK,("programme","requirement","digest"))):
        rows=conn.execute("SELECT audit_seq,actor,"+",".join(cols)+" FROM "+table).fetchall()
        expected={(r["audit_seq"],r["actor"],":".join(r[k] for k in cols)) for r in rows}
        actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
            "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(action,))}
        if len(expected)!=len(rows) or actual!=expected:
            raise access.AccessDenied("unified programme audit inventory mismatch")


def _plan(bp):
    p=bp["plan"]
    return {"plan_id":bp["plan_id"],"vendor_id":bp["vendor_id"],
            "target":{k:p["target"][k] for k in ("name","version")},
            "profiles":p["target_profile"]["types"],
            "required_suites":[r["suite"] for r in p["selection"]["required_suites"]],
            "runnable_suites":p["selection"]["runnable_suites"],
            "required_but_not_run":p["selection"]["required_but_not_run"]}


def _material(directory,spec):
    d._keys(spec,"programme_id plans requirements")
    d._require(d._token(spec["programme_id"]),"bounded programme ID required")
    ids=spec["plans"]
    d._require(isinstance(ids,list) and 1<=len(ids)<=MAX_PLANS and all(d._hex(x) for x in ids)
               and len(set(ids))==len(ids),"distinct saved buyer plans required")
    plans=[_plan(bridge._buyer_plan(directory,pid)) for pid in sorted(ids)]
    by={p["plan_id"]:p for p in plans}
    vendors={}
    for p in plans:
        if p["vendor_id"] in vendors and vendors[p["vendor_id"]]!=p["target"]:
            raise access.AccessDenied("one exact product version per vendor in a programme")
        vendors[p["vendor_id"]]=p["target"]
    reqs=spec["requirements"]
    d._require(isinstance(reqs,list) and 1<=len(reqs)<=MAX_REQUIREMENTS,"bounded requirement inventory required")
    seen,selectors,covered=set(),set(),set()
    for r in reqs:
        d._keys(r,"id kind plan_id family")
        d._require(d._token(r["id"]) and r["id"] not in seen and r["kind"] in KINDS
                   and isinstance(r["plan_id"],str) and r["plan_id"] in by,"invalid requirement or plan")
        p=by[r["plan_id"]]
        if r["kind"] in ("text","conversation","rag"):
            expected={"text":executors.GENERIC_PAIRED_TEXT,"conversation":executors.PATIENT_EPISODE,
                      "rag":executors.RAG_TRACE}[r["kind"]]
            d._require(r["family"] in p["runnable_suites"] and executors.executor_for(r["family"])==expected,
                       "requirement must use exact runnable executor and suite")
        else:
            d._require(r["family"] is None,"noncanonical requirement must not claim canonical suite")
        key=(r["kind"],r["plan_id"],r["family"])
        d._require(key not in selectors,"duplicate requirement selector")
        seen.add(r["id"])
        selectors.add(key)
        covered.add(r["plan_id"])
    d._require(covered==set(ids),"each plan must have an explicit evidence requirement")
    gaps=[]
    for p in plans:
        for suite in p["required_suites"]:
            kinds=[r["id"] for r in reqs if r["plan_id"]==p["plan_id"] and r["family"]==suite]
            if not kinds:
                gaps.append({"plan_id":p["plan_id"],"suite":suite,"state":"not_planned_in_this_programme"
                             if suite in p["runnable_suites"] else "required_but_not_run"})
    return d._sealed({"schema":"unified-assessment-programme/1","programme_id":spec["programme_id"],
                      "plans":plans,"requirements":sorted(reqs,key=lambda r:r["id"]),
                      "unplanned_canonical_suites":gaps,"decision_grade":False,"limitations":LIMITATIONS[:]})


def _read(conn,ident):
    d._require(d._token(ident),"invalid programme ID")
    row=conn.execute("SELECT * FROM unified_assessment_programmes WHERE id=?",(ident,)).fetchone()
    if not row:
        raise access.AccessDenied("unknown programme")
    doc=json.loads(row["payload"])
    if (doc["schema"]!="unified-assessment-programme/1" or doc["programme_id"]!=ident
            or doc["decision_grade"] is not False or doc["digest"]!=row["digest"]
            or d._digest({k:v for k,v in doc.items() if k!="digest"})!=row["digest"]):
        raise access.AccessDenied("programme record changed")
    return doc,row["audit_seq"]


def _spec(doc):
    return {"programme_id":doc["programme_id"],"plans":[p["plan_id"] for p in doc["plans"]],
            "requirements":doc["requirements"]}


def _current(directory,doc):
    return _material(directory,_spec(doc))==doc


def preview(directory,actor,spec):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        _actor(conn,actor,"admin")
        _inventory(conn)
        doc=_material(directory,spec)
        if conn.execute("SELECT 1 FROM unified_assessment_programmes WHERE id=?",(doc["programme_id"],)).fetchone():
            raise access.AccessDenied("programme ID already frozen")
        return doc


def freeze(directory,actor,spec,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit programme approval required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        _inventory(conn)
        doc=_material(directory,spec)
        ident=doc["programme_id"]
        if doc["digest"]!=approval:
            raise access.AccessDenied("programme differs from preview")
        if conn.execute("SELECT count(*) FROM unified_assessment_programmes").fetchone()[0]>=MAX_PROGRAMMES:
            raise access.AccessDenied("programme limit")
        if conn.execute("SELECT 1 FROM unified_assessment_programmes WHERE id=?",(ident,)).fetchone():
            raise access.AccessDenied("programme ID already frozen")
        seq=_audit(conn,actor,FREEZE,ident+":"+doc["digest"])
        conn.execute("INSERT INTO unified_assessment_programmes VALUES(?,?,?,?,?)",
                     (ident,json.dumps(doc),doc["digest"],actor["user_id"],seq))
        return ident


def _source(directory,conn,actor,req,plan,source_id):
    """Derive restricted evidence metadata from exact existing source records."""
    kind=req["kind"]
    pid=plan["plan_id"]
    target=plan["target"]
    vid=plan["vendor_id"]
    if kind in ("imaging","rag_grounding"):
        d._require(d._token(source_id),"invalid imaging study ID")
    else:
        manual._identifier(source_id)
    if kind=="text":
        row=jobs.status(directory,source_id)
        if row["status"]!="completed":
            raise access.AccessDenied("verified completed text job required")
        b=manual._read(jobs._job_path(directory,source_id)/"binding.json")
        ex=manual._load(directory,b["exchange_id"])
        if (b["assessment_id"]!=row["assessment_id"] or b["plan_id"]!=pid or ex["plan_id"]!=pid
                or ex["vendor_id"]!=vid or {k:ex["target"][k] for k in ("name","version")}!=target):
            raise access.AccessDenied("text job scope mismatch")
        packages=[p for p in row["result"]["packages"] if p["family_id"]==req["family"]]
        if len(packages)!=1:
            raise access.AccessDenied("exact verified text family required")
        p=packages[0]
        result={"anchor":row["assessment_id"],"evidence_digest":p["manifest_hash"],
                "mode":row["mode"],"claim":p["claim"],"scope":"exact_buyer_plan"}
    elif kind=="conversation":
        from . import institution_conversation as conversation
        conversation._schema(conn)
        row=conversation._row(conn,source_id)
        b=row["binding"]
        if (row["status"]!="completed" or b["plan_id"]!=pid or b["vendor_id"]!=vid
                or {k:b["target"][k] for k in ("name","version")}!=target):
            raise access.AccessDenied("conversation job scope mismatch")
        current,_=conversation._material(directory,**{k:b[k] for k in
                    ("plan_id","connector_id","pack_id","panel_id","governance")})
        r=conversation._verify(directory,row)
        if current!=b or r!=row["result"]:
            raise access.AccessDenied("conversation job verification failed")
        result={"anchor":row["digest"],"evidence_digest":r["manifest_hash"],
                "mode":b["mode"],"claim":r["claim"],"scope":"exact_buyer_plan"}
    elif kind=="rag_grounding":
        from . import institution_rag_grounding as grounding
        grounding._schema(conn)
        grounding._inventory(conn)
        frozen,docs,reviews,evidence=grounding._get(conn,source_id)
        candidates=[r for r in (evidence or {}).get("results",[]) if r["candidate"]=={"id":vid,"version":target["version"]}]
        if len(candidates)!=1 or not any(c["counts"]["n_available"] for c in candidates[0]["conditions"]):
            raise access.AccessDenied("sealed grounding study with exact available candidate version required")
        from . import institution_rag_collection as collection
        receipts=[r for r in collection.coverage(conn,source_id) if r["candidate_id"]==vid]
        result={"anchor":frozen["digest"],"evidence_digest":evidence["digest"],
                "mode":"experimental_rag_grounding","claim":"not_canonical",
                "scope":"vendor_id_and_version_only","collection_receipt_digest":receipts[0]["receipt_digest"] if receipts else None}
    elif kind=="rag":
        from . import institution_rag as rag
        rag._schema(conn)
        rag._inventory(conn)
        frozen,evidence=rag._get(directory,conn,source_id)
        if (not evidence or frozen["plan_id"]!=pid or frozen["vendor_id"]!=vid
                or frozen["target"]!=target or frozen["family"]!=req["family"]):
            raise access.AccessDenied("exact sealed synthetic RAG source required")
        result={"anchor":frozen["digest"],"evidence_digest":evidence["digest"],
                "mode":"demonstration","claim":"synthetic_context_rehearsal_only","scope":"exact_buyer_plan"}
    elif kind=="research":
        from . import research_program as research
        research._schema(conn)
        row,payload,bp,_=research._load(directory,conn,actor,source_id,committee=True)
        if row["plan_id"]!=pid or bp["vendor_id"]!=vid or payload["target"]!=target:
            raise access.AccessDenied("research programme scope mismatch")
        doc=research._status(directory,conn,actor,source_id,committee=True)
        if not doc["all_planned_reports_available"]:
            raise access.AccessDenied("incomplete experimental research programme")
        result={"anchor":row["digest"],"evidence_digest":d._digest(
                    [{"kind":m["kind"],"report_digest":m["report_digest"]} for m in doc["members"]]),
                "mode":"experimental_integrity_checked","claim":"not_canonical",
                "scope":"exact_buyer_plan"}
    elif kind=="imaging":
        from . import institution_imaging as imaging, institution_imaging_reference as reference, institution_imaging_transport as transport
        imaging._schema(conn)
        imaging._inventory(conn)
        private,docs,evidence=imaging._get(conn,source_id)
        if not evidence:
            raise access.AccessDenied("sealed imaging study required")
        candidates=[c for c in evidence["report"]["candidates"] if c["candidate"]["id"]==vid
                    and c["candidate"]["version"]==target["version"]]
        if len(candidates)!=1 or candidates[0]["status"]!="submitted":
            raise access.AccessDenied("exact submitted imaging candidate version required")
        m=candidates[0]["metrics"]["overall"]
        if not m["counts"]["scored"]:
            raise access.AccessDenied("imaging candidate has no scored outputs")
        ref_digest=reference.check_link(conn,private)
        transport_rows=[r for r in transport.status(conn,source_id) if r["candidate_id"]==vid]
        result={"anchor":private["digest"],"evidence_digest":evidence["digest"],
                "mode":"experimental_imaging","claim":"not_canonical",
                "scope":"vendor_id_and_version_only","reference_digest":ref_digest,
                "transport_receipt_digest":transport_rows[0]["receipt_digest"] if transport_rows else None,
                "complete_roster":evidence["report"]["roster_complete"]}
    else:
        from . import institution_fhir as fhir, institution_hl7 as hl7, institution_image_text as image
        module,table={"fhir":(fhir,"synthetic_fhir_reports"),"hl7":(hl7,"synthetic_hl7_reports"),
                      "image_text":(image,"synthetic_image_text_reports")}[kind]
        module._schema(conn)
        module._check_inventory(conn)
        row=conn.execute("SELECT * FROM "+table+" WHERE id=?",(source_id,)).fetchone()
        if not row:
            raise access.AccessDenied("synthetic source missing")
        doc=module._read(directory,conn,row)
        if doc["plan_id"]!=pid or doc["vendor_id"]!=vid or not doc["scope_current"]:
            raise access.AccessDenied("synthetic source scope mismatch")
        result={"anchor":doc["id"],"evidence_digest":doc["package"]["digest"],
                "mode":"synthetic_readiness","claim":"not_clinical_validity","scope":"exact_buyer_plan"}
    return {"kind":kind,"source_id":source_id,**result}


def _requirement(doc,rid):
    matches=[r for r in doc["requirements"] if r["id"]==rid]
    if len(matches)!=1:
        raise access.AccessDenied("unknown programme requirement")
    req=matches[0]
    return req,next(p for p in doc["plans"] if p["plan_id"]==req["plan_id"])


def _link(conn,doc,seq,rid):
    row=conn.execute("SELECT * FROM unified_assessment_links WHERE programme=? AND requirement=?",
                     (doc["programme_id"],rid)).fetchone()
    if not row:
        return None
    payload=json.loads(row["payload"])
    if (payload["programme_digest"]!=doc["digest"] or payload["requirement_id"]!=rid
            or payload["source"]["source_id"]!=row["source_id"] or row["audit_seq"]<=seq
            or d._digest(payload)!=row["digest"]):
        raise access.AccessDenied("programme source lock changed")
    return payload


def _preview_link(directory,conn,actor,ident,rid,source_id):
    doc,seq=_read(conn,ident)
    if not _current(directory,doc):
        raise access.AccessDenied("programme buyer scope no longer current")
    req,plan=_requirement(doc,rid)
    if _link(conn,doc,seq,rid):
        raise access.AccessDenied("requirement already locked")
    source=_source(directory,conn,actor,req,plan,source_id)
    material={"programme_digest":doc["digest"],"requirement_id":rid,"source":source,
              "association":"administrative_source_binding","decision_grade":False}
    return {"binding":material,"approval_digest":d._digest(material)}


def link_preview(directory,actor,ident,rid,source_id):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        _actor(conn,actor,"admin")
        _inventory(conn)
        return _preview_link(directory,conn,actor,ident,rid,source_id)


def lock(directory,actor,ident,rid,source_id,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit source lock required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        _inventory(conn)
        p=_preview_link(directory,conn,actor,ident,rid,source_id)
        if p["approval_digest"]!=approval:
            raise access.AccessDenied("source differs from approved preview")
        seq=_audit(conn,actor,LINK,ident+":"+rid+":"+approval)
        conn.execute("INSERT INTO unified_assessment_links VALUES(?,?,?,?,?,?,?)",
                     (ident,rid,source_id,json.dumps(p["binding"]),approval,actor["user_id"],seq))
        return approval


def _status(directory,conn,actor,ident):
    doc,seq=_read(conn,ident)
    try:
        current=_current(directory,doc)
    except ERRORS:
        current=False
    if {r["requirement"] for r in conn.execute("SELECT requirement FROM unified_assessment_links WHERE programme=?",(ident,))}-{r["id"] for r in doc["requirements"]}:
        raise access.AccessDenied("unknown locked requirement")
    rows=[]
    for req in doc["requirements"]:
        rid=req["id"]
        _,plan=_requirement(doc,rid)
        link=_link(conn,doc,seq,rid)
        state="unlinked" if current else "stale_plan"
        source=None
        if link:
            source=link["source"]
            if current:
                try:
                    state="source_available" if _source(directory,conn,actor,req,plan,source["source_id"])==source else "source_changed"
                except ERRORS:
                    state="source_unavailable"
        rows.append({"requirement":req,"vendor_id":plan["vendor_id"],"target":plan["target"],
                     "state":state,"locked_source":source,"decision_grade":False})
    return {"programme":doc,"state":"current" if current else "stale_plan","requirements":rows,
            "all_planned_sources_available":all(r["state"]=="source_available" for r in rows),
            "canonical_requirements_satisfied":False,"decision_grade":False,"deployment_authorized":False}


def status(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        _actor(conn,actor,"admin","operator","observer")
        _inventory(conn)
        return _status(directory,conn,actor,ident)


def overview(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        _actor(conn,actor,"admin","operator","observer")
        _inventory(conn)
        return [_status(directory,conn,actor,r["id"]) for r in conn.execute(
            "SELECT id FROM unified_assessment_programmes ORDER BY id").fetchall()]


def protected_fhir(conn):
    """Retention must preserve exact programme-linked FHIR source records."""
    _schema(conn)
    _inventory(conn)
    ids=set()
    for row in conn.execute("SELECT id FROM unified_assessment_programmes"):
        doc,seq=_read(conn,row["id"])
        for req in doc["requirements"]:
            link=_link(conn,doc,seq,req["id"])
            if req["kind"]=="fhir" and link:
                ids.add(link["source"]["source_id"])
    return ids


def _package(doc):
    # Exact captured state, not a claim that a remote verifier can re-read
    # private source records. No raw responses, labels or reviewer identities.
    return d._sealed({"schema":"unified-assessment-coverage-export/1",
                      "coverage":doc,"decision_grade":False})


def export(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit programme coverage export required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,"admin")
        _inventory(conn)
        doc=_package(_status(directory,conn,actor,ident))
        if doc["digest"]!=approval:
            raise access.AccessDenied("programme coverage changed since approval")
        _audit(conn,actor,"unified-assessment-coverage-exported",ident+":"+doc["digest"])
        return doc


def export_preview(directory,actor,ident):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        _actor(conn,actor,"admin")
        _inventory(conn)
        return _package(_status(directory,conn,actor,ident))


def verify_export(package,expected_digest=None):
    d._keys(package,"schema coverage decision_grade digest")
    d._require(package["schema"]=="unified-assessment-coverage-export/1" and package["decision_grade"] is False
               and d._digest({k:v for k,v in package.items() if k!="digest"})==package["digest"]
               and (expected_digest is None or expected_digest==package["digest"]),"coverage export digest mismatch")
    c=package["coverage"]
    d._keys(c,"programme state requirements all_planned_sources_available canonical_requirements_satisfied decision_grade deployment_authorized")
    d._require(c["decision_grade"] is False and c["canonical_requirements_satisfied"] is False
               and c["deployment_authorized"] is False and c["state"] in ("current","stale_plan"),"invalid coverage claim")
    p=c["programme"]
    d._keys(p,"schema programme_id plans requirements unplanned_canonical_suites decision_grade limitations digest")
    d._require(p["schema"]=="unified-assessment-programme/1" and p["decision_grade"] is False
               and p["limitations"]==LIMITATIONS and d._digest({k:v for k,v in p.items() if k!="digest"})==p["digest"],
               "programme content digest mismatch")
    from . import unified_coverage_contract as contract
    contract.validate_programme(p,kinds=KINDS,executor_for=executors.executor_for,
        executors={"text":executors.GENERIC_PAIRED_TEXT,"conversation":executors.PATIENT_EPISODE,"rag":executors.RAG_TRACE},
        max_plans=MAX_PLANS,max_requirements=MAX_REQUIREMENTS)
    reqs,rows=p["requirements"],c["requirements"]
    d._require(isinstance(reqs,list) and isinstance(rows,list) and 1<=len(reqs)==len(rows)<=MAX_REQUIREMENTS,
               "coverage requirement inventory mismatch")
    seen=set()
    for req,row in zip(reqs,rows):
        d._keys(req,"id kind plan_id family")
        d._keys(row,"requirement vendor_id target state locked_source decision_grade")
        d._require(row["requirement"]==req and d._token(req["id"]) and req["id"] not in seen
                   and req["kind"] in KINDS and row["decision_grade"] is False,"invalid coverage row")
        seen.add(req["id"])
        _,plan=_requirement(p,req["id"])
        d._require(row["vendor_id"]==plan["vendor_id"] and row["target"]==plan["target"],"coverage candidate mismatch")
        allowed=("unlinked","source_available","source_changed","source_unavailable") if c["state"]=="current" else ("stale_plan",)
        d._require(row["state"] in allowed,"invalid coverage state")
        source=row["locked_source"]
        if source is None:
            d._require(row["state"] in ("unlinked","stale_plan"),"missing source metadata")
        else:
            required="kind source_id anchor evidence_digest mode claim scope"
            if req["kind"]=="imaging":
                required+=" reference_digest transport_receipt_digest complete_roster"
            if req["kind"]=="rag_grounding" and "collection_receipt_digest" in source:
                required+=" collection_receipt_digest"
            d._keys(source,required)
            d._require(source["kind"]==req["kind"] and d._hex(source["anchor"]) and d._hex(source["evidence_digest"])
                       and isinstance(source["source_id"],str) and row["state"]!="unlinked","invalid locked source")
            contract.validate_source(req["kind"],source)
    d._require(c["all_planned_sources_available"] is all(r["state"]=="source_available" for r in rows),
               "coverage completeness mismatch")
    return {"status":"COVERAGE_STRUCTURE_VERIFIED","digest":package["digest"],
            "expected_digest_matched":expected_digest is not None,
            "source_records_reverified":False,"decision_grade":False}


def configure(parser):
    parser.add_argument("action",choices=["verify-export"])
    parser.add_argument("file")
    parser.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    try:
        print(json.dumps(verify_export(d._read(args.file),args.expected_digest),allow_nan=False))
    except ERRORS:
        raise SystemExit("Unified assessment coverage verification refused.")


def available_plans(directory,actor):
    from . import institution_setup as setup
    with access.db(directory) as conn:
        _actor(conn,actor,"admin")
        out=[]
        for pid in setup._ids(directory,"plans"):
            try:
                out.append(_plan(bridge._buyer_plan(directory,pid)))
            except ERRORS:
                continue
        return out


def guided_spec(directory,actor,ident,choices):
    """Derive supported canonical requirements from exact selected buyer plans.

    Every selected noncanonical lane is explicit. Unsupported canonical suites
    remain gaps; selecting imaging never silently fulfills a text requirement.
    """
    d._require(isinstance(choices,dict) and 1<=len(choices)<=MAX_PLANS,
               "select one or more saved plans")
    known={p["plan_id"]:p for p in available_plans(directory,actor)}
    requirements=[]
    for i,pid in enumerate(sorted(choices),1):
        d._require(pid in known,"current saved plan required")
        kinds=choices[pid]
        d._require(isinstance(kinds,list) and len(set(kinds))==len(kinds)
                   and set(kinds)<=set(KINDS)-{"text","conversation","rag"},"invalid extra evidence selection")
        for family in known[pid]["runnable_suites"]:
            executor=executors.executor_for(family)
            kind={executors.GENERIC_PAIRED_TEXT:"text",executors.PATIENT_EPISODE:"conversation",executors.RAG_TRACE:"rag"}.get(executor)
            if kind:
                requirements.append({"id":"p"+str(i)+"-"+family,"kind":kind,"plan_id":pid,"family":family})
        for kind in sorted(kinds):
            requirements.append({"id":"p"+str(i)+"-"+kind,"kind":kind,"plan_id":pid,"family":None})
    spec={"programme_id":ident,"plans":sorted(choices),"requirements":requirements}
    # The same exact validation and gap reconstruction applies to guided and
    # advanced JSON inputs; the generated spec is never silently persisted.
    _material(directory,spec)
    return spec
