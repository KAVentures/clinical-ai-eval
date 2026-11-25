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


def issue(directory,owner,assessment_id,ttl,*,confirmed=False):
    if confirmed is not True or type(ttl) is not int or not 1<=ttl<=MAX_TTL:
        _fail()
    manual._identifier(owner)
    manual._identifier(assessment_id,digest=True)
    assessment.check(directory,assessment_id)
    token=secrets.token_urlsafe(32)
    gid=secrets.token_hex(16)
    expires=int(time.time())+ttl
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _owner(conn,owner)
        if conn.execute("SELECT count(*) FROM evaluator_service_grants").fetchone()[0]>=MAX_GRANTS:
            _fail()
        conn.execute("INSERT INTO evaluator_service_grants VALUES(?,?,?,?,?,0)",
                     (gid,_hash(token),owner,assessment_id,expires))
        access._audit(conn,"local-admin","evaluator-service-grant-issued",gid)
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
    if not row or row["revoked"] or row["expires"]<=int(time.time()):
        _fail()
    _owner(conn,row["owner"])
    return row


def _request(conn,gid,rid):
    if not isinstance(rid,str) or not ID.fullmatch(rid):
        _fail()
    row=conn.execute("SELECT * FROM evaluator_service_requests WHERE id=? AND grant_id=?",(rid,gid)).fetchone()
    if not row:
        _fail()
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
            return _summary(prior)
        # One outstanding request per grant. The service is not a job launcher.
        if conn.execute("SELECT 1 FROM evaluator_service_requests WHERE grant_id=? AND job_id IS NULL",
                        (grant["id"],)).fetchone():
            _fail()
        if conn.execute("SELECT count(*) FROM evaluator_service_requests").fetchone()[0]>=MAX_REQUESTS:
            _fail()
        rid=secrets.token_hex(16)
        conn.execute("INSERT INTO evaluator_service_requests VALUES(?,?,?,?,NULL,?)",
                     (rid,grant["id"],key,grant["assessment_id"],int(time.time())))
        access._audit(conn,grant["owner"],"evaluator-service-requested",rid)
        return _summary(_request(conn,grant["id"],rid))


def discover(directory,token):
    """Describe only the assessment authorized by this grant."""
    with access.db(directory) as conn:
        _schema(conn)
        grant=_grant(conn,token)
        return {"schema":SCHEMA,"assessment_id":grant["assessment_id"],"expires":grant["expires"],
                "operations":["discover","list_requests","request_evaluation","evaluation_status","result_receipt"],
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
        return {"schema":SCHEMA,"requests":[_summary(r) for r in rows],"decision_grade":False}


def inventory(directory,actor=None):
    """Local CLI or authenticated institutional administrator; not machine API."""
    with access.db(directory) as conn:
        _schema(conn)
        if actor is not None:
            _admin(conn,actor)
        rows=conn.execute("SELECT r.*,g.owner,g.revoked,g.expires FROM evaluator_service_requests r "
                          "JOIN evaluator_service_grants g ON r.grant_id=g.id ORDER BY r.created,r.id").fetchall()
        return [{**_summary(r),"grant_id":r["grant_id"],"job_id":r["job_id"],
                 "owner":r["owner"],"grant_active":not r["revoked"] and r["expires"]>int(time.time())
                     and _enabled_owner(conn,r["owner"])} for r in rows]


def _enabled_owner(conn,owner):
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(owner,)).fetchone()
    return bool(row and row["enabled"] and row["role"] in ("admin","operator"))


def _admin(conn,actor):
    access.require(actor,"admin")
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"]!="admin":
        _fail()


def link(directory,rid,job_id,*,confirmed=False,actor=None):
    if confirmed is not True or not isinstance(rid,str) or not ID.fullmatch(rid):
        _fail()
    manual._identifier(job_id)
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
        row=conn.execute("SELECT r.*,g.owner,g.revoked,g.expires FROM evaluator_service_requests r "
                         "JOIN evaluator_service_grants g ON r.grant_id=g.id WHERE r.id=?",(rid,)).fetchone()
        if (not row or row["revoked"] or row["expires"]<=int(time.time()) or
                row["assessment_id"]!=job["assessment_id"] or row["job_id"] not in (None,job_id)):
            _fail()
        _owner(conn,row["owner"])
        existing=conn.execute("SELECT id FROM evaluator_service_requests WHERE job_id=?",(job_id,)).fetchone()
        if existing and existing["id"]!=rid:
            _fail()
        if row["job_id"] is None:
            conn.execute("UPDATE evaluator_service_requests SET job_id=? WHERE id=?",(job_id,rid))
            access._audit(conn,actor["user_id"] if actor is not None else "local-admin","evaluator-service-job-linked",rid+":"+job_id)
    return {"request_id":rid,"job_id":job_id,"status":"linked","decision_grade":False}


def status(directory,token,rid):
    with access.db(directory) as conn:
        _schema(conn)
        grant=_grant(conn,token)
        row=dict(_request(conn,grant["id"],rid))
        if row["assessment_id"]!=grant["assessment_id"]:
            _fail()
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
    if not isinstance(result["required_but_not_run"],list):
        _fail()
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
            else:
                _fail()
        print(json.dumps({"ok":True,"result":result},allow_nan=False))
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
        print(json.dumps({"ok":False,"error":"Evaluation service operation refused"}))
        raise SystemExit(1)
