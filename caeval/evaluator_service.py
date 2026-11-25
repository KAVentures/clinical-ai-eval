"""Scoped evaluator request API. No browser credentials, raw records or execution.

Trusted IT issues an expiring bearer grant for exactly one prepared assessment.
Requests express intent only. A local administrator separately associates an
existing, independently authorized canonical job with the request. All API
responses are restricted metadata; no arbitrary workspace paths are accepted.
"""
import hashlib
import json
import re
import secrets
import time
from wsgiref.simple_server import make_server, WSGIRequestHandler

from . import (institution_access as access, buyer_plan, buyer_manual as manual,
               buyer_assessment as assessment, buyer_jobs as jobs, agent_grants)

MAX_TTL=86400
MAX_REQUESTS=200
MAX_GRANTS=200
MAX_BODY=4096
MAX_REPLY=256*1024
RECEIPT_SCHEMA="clinical-ai-eval-service-receipt/1"
TOKEN=re.compile(r"[A-Za-z0-9_-]{43}")
KEY=re.compile(r"[A-Za-z0-9_-]{16,80}")
ID=re.compile(r"[0-9a-f]{32}")
SCHEMA="clinical-ai-eval-service/1"


def _fail():
    raise access.AccessDenied("evaluation service request denied")


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS evaluator_service_grants(
        id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
        owner TEXT NOT NULL REFERENCES users(id), assessment_id TEXT NOT NULL,
        expires INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS evaluator_service_requests(
        id TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES evaluator_service_grants(id),
        idempotency_key TEXT NOT NULL, assessment_id TEXT NOT NULL,
        job_id TEXT UNIQUE, created INTEGER NOT NULL,
        UNIQUE(grant_id,idempotency_key))""")


def _owner(conn,owner):
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(owner,)).fetchone()
    if not row or not row["enabled"] or row["role"] not in ("admin","operator"):
        _fail()


def _hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _audit_exact(conn,action,obj,actor=None):
    events=conn.execute("SELECT seq,actor FROM audit WHERE action=? AND object_id=? AND outcome='ok'",
                        (action,obj)).fetchall()
    if len(events)!=1 or (actor is not None and events[0]["actor"]!=actor):
        _fail()


def _grant_event(row):
    return row["id"]+":"+buyer_plan._digest([row[k] for k in ("id","token_hash","owner","assessment_id","expires")])


def _request_event(row):
    return row["id"]+":"+buyer_plan._digest([row[k] for k in ("id","grant_id","idempotency_key","assessment_id","created")])


def _issued(conn,row):
    if not row:
        _fail()
    _audit_exact(conn,"evaluator-service-grant-issued",_grant_event(row),"local-admin")


def _active_grant(conn,row):
    _issued(conn,row)
    if (row["revoked"] or row["expires"]<=int(time.time()) or
            conn.execute("SELECT 1 FROM audit WHERE action='evaluator-service-grant-revoked' AND object_id=? AND outcome='ok'",(row["id"],)).fetchone()):
        _fail()
    _owner(conn,row["owner"])


def _check_request(conn,row):
    if not row:
        _fail()
    grant=conn.execute("SELECT * FROM evaluator_service_grants WHERE id=?",(row["grant_id"],)).fetchone()
    _issued(conn,grant)
    if row["assessment_id"]!=grant["assessment_id"]:
        _fail()
    _audit_exact(conn,"evaluator-service-requested",_request_event(row),grant["owner"])
    return grant


def _link_event(row,jid):
    return row["id"]+":"+buyer_plan._digest([_request_event(row),jid])


def _link_inventory(conn,row):
    events=conn.execute("SELECT action,object_id FROM audit WHERE outcome='ok' "
                        "AND action IN ('evaluator-service-job-linked','evaluator-service-queue-approved') "
                        "AND substr(object_id,1,33)=?",(row["id"]+":",)).fetchall()
    jid=row["job_id"]
    if jid is None:
        if events:
            _fail()
    else:
        action=("evaluator-service-queue-approved" if jid.startswith("job-service-")
                else "evaluator-service-job-linked")
        if len(events)!=1 or events[0]["action"]!=action or events[0]["object_id"]!=_link_event(row,jid):
            _fail()


def _check_link(conn,row):
    _link_inventory(conn,row)
    jid=row["job_id"]
    if jid is None:
        return
    if jid.startswith("job-service-"):
        if jid!="job-service-"+row["id"]:
            _fail()
        _queue_approval(conn,jid,row["assessment_id"])
    else:
        _audit_exact(conn,"evaluator-service-job-linked",_link_event(row,jid))


def issue(directory,owner,assessment_id,ttl,*,confirmed=False):
    if confirmed is not True or type(ttl) is not int or not 1<=ttl<=MAX_TTL:
        _fail()
    manual._identifier(owner)
    manual._identifier(assessment_id,digest=True)
    token=secrets.token_urlsafe(32)
    gid=secrets.token_hex(16)
    expires=int(time.time())+ttl
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _owner(conn,owner)
        assessment.check(directory,assessment_id,conn=conn)
        if conn.execute("SELECT count(*) FROM evaluator_service_grants").fetchone()[0]>=MAX_GRANTS:
            _fail()
        conn.execute("INSERT INTO evaluator_service_grants VALUES(?,?,?,?,?,0)",
                     (gid,_hash(token),owner,assessment_id,expires))
        row=conn.execute("SELECT * FROM evaluator_service_grants WHERE id=?",(gid,)).fetchone()
        access._audit(conn,"local-admin","evaluator-service-grant-issued",_grant_event(row))
    return {"grant_id":gid,"token":token,"assessment_id":assessment_id,"expires":expires}


def revoke(directory,gid,*,actor=None):
    if not isinstance(gid,str) or not ID.fullmatch(gid):
        _fail()
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        if actor is not None:
            _admin(conn,actor)
        if conn.execute("UPDATE evaluator_service_grants SET revoked=1 WHERE id=? AND revoked=0",(gid,)).rowcount!=1:
            _fail()
        access._audit(conn,actor["user_id"] if actor is not None else "local-admin","evaluator-service-grant-revoked",gid)


def _grant(conn,token):
    if not isinstance(token,str) or not TOKEN.fullmatch(token):
        _fail()
    row=conn.execute("SELECT * FROM evaluator_service_grants WHERE token_hash=?",(_hash(token),)).fetchone()
    _active_grant(conn,row)
    return row


def _request(conn,gid,rid):
    if not isinstance(rid,str) or not ID.fullmatch(rid):
        _fail()
    row=conn.execute("SELECT * FROM evaluator_service_requests WHERE id=? AND grant_id=?",(rid,gid)).fetchone()
    _check_request(conn,row)
    _check_link(conn,row)
    return row


def _summary(row):
    return {"schema":SCHEMA,"request_id":row["id"],"assessment_id":row["assessment_id"],
            "status":"awaiting_local_approval" if row["job_id"] is None else "job_linked",
            "execution_authorized_by_api":False,"decision_grade":False}


def submit(directory,token,key):
    if not isinstance(key,str) or not KEY.fullmatch(key):
        _fail()
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        grant=_grant(conn,token)
        prior=conn.execute("SELECT * FROM evaluator_service_requests WHERE grant_id=? AND idempotency_key=?",
                           (grant["id"],key)).fetchone()
        if prior:
            return _summary(_request(conn,grant["id"],prior["id"]))
        # One outstanding request per grant. The service is not a job launcher.
        if conn.execute("SELECT 1 FROM evaluator_service_requests WHERE grant_id=? AND job_id IS NULL",
                        (grant["id"],)).fetchone():
            _fail()
        if conn.execute("SELECT count(*) FROM evaluator_service_requests").fetchone()[0]>=MAX_REQUESTS:
            _fail()
        rid=secrets.token_hex(16)
        conn.execute("INSERT INTO evaluator_service_requests VALUES(?,?,?,?,NULL,?)",
                     (rid,grant["id"],key,grant["assessment_id"],int(time.time())))
        row=conn.execute("SELECT * FROM evaluator_service_requests WHERE id=?",(rid,)).fetchone()
        access._audit(conn,grant["owner"],"evaluator-service-requested",_request_event(row))
        return _summary(_request(conn,grant["id"],rid))


def discover(directory,token):
    """Describe only the assessment authorized by this grant."""
    with access.db(directory) as conn:
        _schema(conn)
        grant=_grant(conn,token)
        return {"schema":SCHEMA,"assessment_id":grant["assessment_id"],"expires":grant["expires"],
                "operations":["discover","list_requests","request_evaluation","evaluation_status","result_receipt","download_approved_package"],
                "execution_authorized_by_api":False,"decision_grade":False}


def list_requests(directory,token):
    """Allow reconnecting clients to recover their own request identifiers."""
    with access.db(directory) as conn:
        _schema(conn)
        grant=_grant(conn,token)
        rows=conn.execute("SELECT * FROM evaluator_service_requests WHERE grant_id=? ORDER BY created,id",
                          (grant["id"],)).fetchall()
        if len(rows)>MAX_REQUESTS or any(r["assessment_id"]!=grant["assessment_id"] for r in rows):
            _fail()
        return {"schema":SCHEMA,"requests":[_summary(_request(conn,grant["id"],r["id"])) for r in rows],"decision_grade":False}


def inventory(directory,actor=None):
    """Local CLI or authenticated institutional administrator; not machine API."""
    with access.db(directory) as conn:
        _schema(conn)
        if actor is not None:
            _admin(conn,actor)
        rows=conn.execute("SELECT r.*,g.owner,g.revoked,g.expires FROM evaluator_service_requests r "
                          "JOIN evaluator_service_grants g ON r.grant_id=g.id ORDER BY r.created,r.id").fetchall()
        result=[]
        for row in rows:
            grant=_check_request(conn,row)
            _check_link(conn,row)
            active=True
            try:
                _active_grant(conn,grant)
            except access.AccessDenied:
                active=False
            result.append({**_summary(row),"grant_id":row["grant_id"],"job_id":row["job_id"],
                           "owner":grant["owner"],"grant_active":active})
        return result


def _enabled_owner(conn,owner):
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(owner,)).fetchone()
    return bool(row and row["enabled"] and row["role"] in ("admin","operator"))


def _admin(conn,actor):
    access.require(actor,"admin")
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"]!="admin":
        _fail()


def _active_request(conn,rid):
    if not isinstance(rid,str) or not ID.fullmatch(rid):
        _fail()
    row=conn.execute("SELECT r.*,g.owner,g.revoked,g.expires,g.assessment_id AS granted_assessment "
                     "FROM evaluator_service_requests r JOIN evaluator_service_grants g ON r.grant_id=g.id "
                     "WHERE r.id=?",(rid,)).fetchone()
    grant=_check_request(conn,row)
    _active_grant(conn,grant)
    _check_link(conn,row)
    return row


def approve_queue(directory,rid,*,confirmed=False,actor=None):
    """Create one canonical queue entry after explicit local approval.

    Deterministic job IDs permit retry after interruption between database
    commits. The worker refuses execution without the committed approval audit.
    """
    if confirmed is not True:
        _fail()
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        if actor is not None:
            _admin(conn,actor)
        row=_active_request(conn,rid)
        jid="job-service-"+rid
        if row["job_id"] is not None:
            if row["job_id"]!=jid:
                _fail()
            _queue_approval(conn,jid,row["assessment_id"])
            return {"request_id":rid,"job_id":jid,"status":"already_approved","decision_grade":False}
        job=jobs.enqueue(directory,row["assessment_id"],service_request=rid,conn=conn)
        if job["id"]!=jid or job["assessment_id"]!=row["assessment_id"] or job["status"]!="queued":
            _fail()
        conn.execute("UPDATE evaluator_service_requests SET job_id=? WHERE id=?",(jid,rid))
        access._audit(conn,actor["user_id"] if actor is not None else "local-admin",
                      "evaluator-service-queue-approved",_link_event(row,jid))
        return {"request_id":rid,"job_id":jid,"status":"queued","decision_grade":False}


def _queue_approval(conn,jid,assessment_id=None):
    rid=jid[len("job-service-"):]
    if not ID.fullmatch(rid):
        _fail()
    row=conn.execute("SELECT * FROM evaluator_service_requests WHERE id=?",(rid,)).fetchone()
    _check_request(conn,row)
    if row["job_id"]!=jid or (assessment_id is not None and row["assessment_id"]!=assessment_id):
        _fail()
    _link_inventory(conn,row)
    _audit_exact(conn,"evaluator-service-queue-approved",_link_event(row,jid))
    return row["assessment_id"]


def require_queue_approval(directory,jid):
    # Revocation stops client access, not an already approved canonical job.
    with access.db(directory) as conn:
        _schema(conn)
        return _queue_approval(conn,jid)


def link(directory,rid,job_id,*,confirmed=False,actor=None):
    if confirmed is not True or not isinstance(rid,str) or not ID.fullmatch(rid):
        _fail()
    manual._identifier(job_id)
    if job_id.startswith("job-service-"):
        _fail()
    if actor is not None:
        with access.db(directory) as conn:
            _admin(conn,actor)
    # Job status revalidates completed packages and may open the access store.
    # Do not hold its SQLite write lock during the expensive verification.
    job=jobs.status(directory,job_id)
    if job["status"] not in ("queued","running","completed"):
        _fail()
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        if actor is not None:
            _admin(conn,actor)
        row=_active_request(conn,rid)
        if row["assessment_id"]!=job["assessment_id"] or row["job_id"] not in (None,job_id):
            _fail()
        existing=conn.execute("SELECT id FROM evaluator_service_requests WHERE job_id=?",(job_id,)).fetchone()
        if existing and existing["id"]!=rid:
            _fail()
        if row["job_id"] is None:
            conn.execute("UPDATE evaluator_service_requests SET job_id=? WHERE id=?",(job_id,rid))
            access._audit(conn,actor["user_id"] if actor is not None else "local-admin","evaluator-service-job-linked",_link_event(row,job_id))
    return {"request_id":rid,"job_id":job_id,"status":"linked","decision_grade":False}


def status(directory,token,rid):
    with access.db(directory) as conn:
        _schema(conn)
        grant=_grant(conn,token)
        row=dict(_request(conn,grant["id"],rid))
        if row["assessment_id"]!=grant["assessment_id"]:
            _fail()
        if row["job_id"] is not None and row["job_id"].startswith("job-service-"):
            _queue_approval(conn,row["job_id"],row["assessment_id"])
    result=_summary(row)
    if row["job_id"] is not None:
        job=jobs.status(directory,row["job_id"])
        if job["assessment_id"]!=row["assessment_id"]:
            _fail()
        result["status"]=job["status"]
        # Do not return paths, prompts, responses, reviewer notes or raw logs.
        result["packages"]=[]
        if job["status"]=="completed":
            result["packages"]=[{k:p[k] for k in ("family_id","manifest_hash","claim","n_responses","review_count")}
                                for p in job["result"]["packages"]]
            result["required_but_not_run"]=job["result"]["required_but_not_run"]
    # Revalidate authorization after costly package verification.
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        grant=_grant(conn,token)
        if dict(_request(conn,grant["id"],rid))!=row:
            _fail()
        if row["job_id"] is not None and row["job_id"].startswith("job-service-"):
            _queue_approval(conn,row["job_id"],row["assessment_id"])
        access._audit(conn,grant["owner"],"evaluator-service-status-read",rid)
    return result


def receipt(directory,token,rid):
    result=status(directory,token,rid)
    if result["status"]!="completed":
        _fail()
    payload={"schema":RECEIPT_SCHEMA,"result":result,
             "scope":"verified_package_metadata_only","decision_grade":False}
    return {**payload,"digest":buyer_plan._digest(payload)}


def verify_receipt(doc,expected_digest):
    """Offline integrity check of metadata, NOT independent scientific replay."""
    import hmac
    if (not isinstance(doc,dict) or set(doc)!={"schema","result","scope","decision_grade","digest"}
            or doc["schema"]!=RECEIPT_SCHEMA or doc["scope"]!="verified_package_metadata_only"
            or doc["decision_grade"] is not False):
        _fail()
    manual._identifier(expected_digest,digest=True)
    manual._identifier(doc["digest"],digest=True)
    if (not hmac.compare_digest(doc["digest"],expected_digest)
            or buyer_plan._digest({k:v for k,v in doc.items() if k!="digest"})!=doc["digest"]):
        _fail()
    result=doc["result"]
    if (not isinstance(result,dict) or set(result)!={"schema","request_id","assessment_id","status",
            "execution_authorized_by_api","decision_grade","packages","required_but_not_run"}
            or result["schema"]!=SCHEMA or result["status"]!="completed"
            or result["execution_authorized_by_api"] is not False or result["decision_grade"] is not False
            or not isinstance(result["request_id"],str) or not ID.fullmatch(result["request_id"])):
        _fail()
    manual._identifier(result["assessment_id"],digest=True)
    packages=result["packages"]
    if not isinstance(packages,list) or not 1<=len(packages)<=100:
        _fail()
    families=set()
    for p in packages:
        if not isinstance(p,dict) or set(p)!={"family_id","manifest_hash","claim","n_responses","review_count"}:
            _fail()
        family=manual._identifier(p["family_id"])
        manual._identifier(p["manifest_hash"],digest=True)
        if (family in families or p["claim"] not in ("none","demonstration","internal_regression")
                or any(type(p[k]) is not int or p[k]<0 for k in ("n_responses","review_count"))
                or p["review_count"]>p["n_responses"]):
            _fail()
        families.add(family)
    blocked=result["required_but_not_run"]
    if not isinstance(blocked,list) or len(blocked)>100:
        _fail()
    for entry in blocked:
        if (not isinstance(entry,dict) or set(entry)!={"suite","blocked_reason"}
                or not isinstance(entry["blocked_reason"],str) or not entry["blocked_reason"].strip()):
            _fail()
        family=manual._identifier(entry["suite"])
        if family in families:
            _fail()
        families.add(family)
    return {"status":"METADATA_INTEGRITY_VERIFIED","digest":doc["digest"],
            "expected_digest_matched":True,"scientific_replay_verified":False,"decision_grade":False}


def openapi():
    spec={"openapi":"3.1.0","info":{"title":"Clinical AI Eval request service","version":"1.0.0",
            "description":"Request and status metadata for a preapproved assessment. No remote execution."},
            "components":{"securitySchemes":{"grant":{"type":"http","scheme":"bearer"}}},
            "security":[{"grant":[]}],"paths":{
            "/v1/evaluation-requests":{"post":{"operationId":"requestEvaluation",
              "requestBody":{"required":True,"content":{"application/json":{"schema":{"type":"object",
                 "additionalProperties":False,"required":["idempotency_key"],
                 "properties":{"idempotency_key":{"type":"string","pattern":"^[A-Za-z0-9_-]{16,80}$"}}}}}},
              "responses":{"200":{"description":"Request accepted or already exists"},"403":{"description":"Denied"}}}},
            "/v1/evaluation-requests/{request_id}":{"get":{"operationId":"evaluationStatus",
              "parameters":[{"name":"request_id","in":"path","required":True,
                             "schema":{"type":"string","pattern":"^[0-9a-f]{32}$"}}],
              "responses":{"200":{"description":"Scoped verified job metadata"},"403":{"description":"Denied"}}}}}}
    spec["paths"]["/v1/assessment"]={"get":{"operationId":"discoverAssessment",
        "responses":{"200":{"description":"Authorized assessment metadata"},"403":{"description":"Denied"}}}}
    spec["paths"]["/v1/evaluation-requests"]["get"]={"operationId":"listRequests",
        "responses":{"200":{"description":"Requests scoped to this grant"},"403":{"description":"Denied"}}}}
    spec["paths"]["/v1/evaluation-requests/{request_id}/receipt"]={"get":{"operationId":"resultReceipt",
        "parameters":[{"name":"request_id","in":"path","required":True,"schema":{"type":"string","pattern":"^[0-9a-f]{32}$"}}],
        "responses":{"200":{"description":"Digest-bound completed metadata receipt"},"403":{"description":"Denied"}}}}
    spec["paths"]["/v1/evaluation-requests/{request_id}/packages/{family}"]={"get":{
        "operationId":"downloadApprovedPackage",
        "parameters":[{"name":"request_id","in":"path","required":True,"schema":{"type":"string","pattern":"^[0-9a-f]{32}$"}},
                      {"name":"family","in":"path","required":True,"schema":{"type":"string"}}],
        "responses":{"200":{"description":"Separately approved exact canonical archive",
            "content":{"application/zip":{"schema":{"type":"string","format":"binary"}}}},
            "403":{"description":"Denied or release unavailable"}}}}
    return spec


class App:
    def __init__(self,directory):
        self.directory=directory

    def __call__(self,env,start):
        try:
            # Separate machine interface; never accept cookie authentication,
            # browser cross-origin requests, or credential-bearing query strings.
            auth=env.get("HTTP_AUTHORIZATION","")
            if (not auth.startswith("Bearer ") or env.get("QUERY_STRING") or env.get("HTTP_ORIGIN")
                    or env.get("HTTP_COOKIE")):
                _fail()
            token=auth[7:]
            path=env["PATH_INFO"]
            method=env["REQUEST_METHOD"]
            base="/v1/evaluation-requests"
            if method=="GET" and path.startswith(base+"/") and "/packages/" in path:
                from . import evaluator_package_release as release
                rid,family=path[len(base)+1:].split("/packages/")
                raw,meta=release.download(self.directory,token,rid,family)
                start("200 OK",[("Content-Type","application/zip"),("Content-Length",str(len(raw))),
                      ("Content-Disposition",'attachment; filename="clinical-evaluation.zip"'),
                      ("X-Content-SHA256",meta["sha256"]),("Cache-Control","no-store"),
                      ("X-Content-Type-Options","nosniff")])
                return [raw]
            if method=="POST" and path==base:
                if env.get("CONTENT_TYPE","").split(";")[0]!="application/json":
                    _fail()
                n=int(env.get("CONTENT_LENGTH","0"))
                if not 0<n<=MAX_BODY:
                    _fail()
                raw=env["wsgi.input"].read(n)
                if len(raw)!=n:
                    _fail()
                doc=manual._parse(raw)
                if not isinstance(doc,dict) or set(doc)!={"idempotency_key"}:
                    _fail()
                result=submit(self.directory,token,doc["idempotency_key"])
            elif method=="GET" and path=="/v1/assessment":
                result=discover(self.directory,token)
            elif method=="GET" and path==base:
                result=list_requests(self.directory,token)
            elif method=="GET" and path.startswith(base+"/") and path.endswith("/receipt"):
                result=receipt(self.directory,token,path[len(base)+1:-len("/receipt")])
            elif method=="GET" and path.startswith(base+"/"):
                result=status(self.directory,token,path[len(base)+1:])
            else:
                _fail()
            body=buyer_plan._canonical({"ok":True,"result":result})
            if len(body)>MAX_REPLY:
                _fail()
            code="200 OK"
        except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError,RecursionError):
            code="403 Forbidden"
            body=b'{"ok":false,"error":"Request denied or evaluation unavailable"}'
        start(code,[("Content-Type","application/json"),("Content-Length",str(len(body))),
                    ("Cache-Control","no-store"),("X-Content-Type-Options","nosniff")])
        return [body]


class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):
        pass


def serve(directory,port):
    if type(port) is not int or not 1024<=port<=65535:
        _fail()
    with make_server("127.0.0.1",port,App(directory),handler_class=QuietHandler) as server:
        server.serve_forever()


def cli(args):
    try:
        if args.action=="openapi":
            result=openapi()
        elif args.action=="verify-receipt":
            from pathlib import Path
            path=Path(args.file)
            if path.is_symlink() or not path.is_file():
                _fail()
            with path.open("rb") as f:
                raw=f.read(MAX_REPLY+1)
            if len(raw)>MAX_REPLY:
                _fail()
            result=verify_receipt(manual._parse(raw),args.expected_digest)
        else:
            if not args.path:
                _fail()
            if args.action=="serve":
                serve(args.path,args.port)
                return
            if args.action=="issue":
                agent_grants.outside_workspace(args.path,args.out)
                result=issue(args.path,args.owner,args.assessment_id,args.ttl,confirmed=args.confirm)
                token=result.pop("token")
                try:
                    agent_grants.write_private(args.out,token)
                except BaseException:
                    revoke(args.path,result["grant_id"])
                    raise
            elif args.action=="revoke":
                revoke(args.path,args.grant_id)
                result={"revoked":True}
            elif args.action=="inventory":
                result=inventory(args.path)
            elif args.action=="link":
                result=link(args.path,args.request_id,args.job_id,confirmed=args.confirm)
            elif args.action=="approve-queue":
                result=approve_queue(args.path,args.request_id,confirmed=args.confirm)
            elif args.action in ("package-preview","package-approve"):
                from . import evaluator_package_release as release
                if args.action=="package-preview":
                    result=release.preview(args.path,args.request_id,args.family,args.out)
                else:
                    result=release.approve(args.path,args.request_id,args.family,args.expected_digest,confirmed=args.confirm)
            else:
                _fail()
        print(json.dumps({"ok":True,"result":result},allow_nan=False))
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
        print(json.dumps({"ok":False,"error":"Evaluation service operation refused"}))
        raise SystemExit(1)
