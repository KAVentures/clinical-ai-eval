"""Narrow local monitoring ingestion capability and minimal polling feed.

No browser authentication bypass, network egress, clinical decision, model
execution or control of deployed products. The separate listener is loopback
only. Bearer grants are scoped to one monitor and an active operator account.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from wsgiref.simple_server import make_server, WSGIRequestHandler
from . import institution_access as access, unified_assessment as u
from . import institution_lifecycle_monitor as monitor, diagnostic_imaging as d
from . import buyer_manual

MAX_GRANTS=200
MAX_TTL=86400
MAX_USES=300
MAX_BATCH=12
MAX_BODY=128*1024
ISSUE="lifecycle-ingestion-grant-issued"
REVOKE="lifecycle-ingestion-grant-revoked"
USE="lifecycle-ingestion-request-locked"
SCOPES=("report","incident","check")


def _hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _schema(conn):
    monitor._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_ingestion_grants(
        id TEXT PRIMARY KEY,token_hash TEXT NOT NULL UNIQUE,monitor TEXT NOT NULL,
        owner TEXT NOT NULL,issued_by TEXT NOT NULL,expires INTEGER NOT NULL,
        scopes TEXT NOT NULL,max_uses INTEGER NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        revoked_seq INTEGER UNIQUE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lifecycle_ingestion_requests(
        grant_id TEXT NOT NULL REFERENCES lifecycle_ingestion_grants(id),
        request_id TEXT NOT NULL,request_digest TEXT NOT NULL,payload TEXT NOT NULL,
        digest TEXT NOT NULL,audit_seq INTEGER NOT NULL UNIQUE,
        PRIMARY KEY(grant_id,request_id))""")


def _binding(row):
    return d._digest([row["id"],row["token_hash"],row["monitor"],row["owner"],
                      row["issued_by"],row["expires"],json.loads(row["scopes"]),row["max_uses"]])


def _inventory(conn):
    monitor._inventory(conn)
    grants=conn.execute("SELECT * FROM lifecycle_ingestion_grants").fetchall()
    requests=conn.execute("SELECT * FROM lifecycle_ingestion_requests").fetchall()
    expected={ISSUE:set(),REVOKE:set(),USE:set()}
    by={r["id"]:r for r in grants}
    for r in grants:
        expected[ISSUE].add((r["audit_seq"],r["issued_by"],r["id"]+":"+_binding(r)))
        if r["revoked_seq"] is not None:
            if r["revoked_seq"]<=r["audit_seq"]:
                raise access.AccessDenied("invalid revocation order")
            # Audit actor is the authenticated admin at revocation time.
            event=conn.execute("SELECT actor FROM audit WHERE seq=?",(r["revoked_seq"],)).fetchone()
            if not event:
                raise access.AccessDenied("missing revocation")
            expected[REVOKE].add((r["revoked_seq"],event["actor"],r["id"]))
    for r in requests:
        grant=by[r["grant_id"]]
        payload=json.loads(r["payload"])
        if (d._digest(payload)!=r["digest"] or r["audit_seq"]<=grant["audit_seq"]
                or (grant["revoked_seq"] is not None and r["audit_seq"]>=grant["revoked_seq"])):
            raise access.AccessDenied("ingestion request integrity mismatch")
        expected[USE].add((r["audit_seq"],grant["owner"],r["grant_id"]+":"+r["request_id"]+":"+r["request_digest"]+":"+r["digest"]))
    for event,wanted in expected.items():
        actual={(r["seq"],r["actor"],r["object_id"]) for r in conn.execute(
            "SELECT seq,actor,object_id FROM audit WHERE action=? AND outcome='ok'",(event,))}
        if actual!=wanted:
            raise access.AccessDenied("ingestion audit inventory mismatch")


def issue(directory,actor,spec,*,confirmed=False):
    d._keys(spec,"monitor_id owner scopes ttl_seconds max_uses")
    d._require(confirmed is True and d._token(spec["monitor_id"]) and d._token(spec["owner"])
               and isinstance(spec["scopes"],list) and bool(spec["scopes"])
               and len(set(spec["scopes"]))==len(spec["scopes"]) and set(spec["scopes"])<=set(SCOPES)
               and type(spec["ttl_seconds"]) is int and 60<=spec["ttl_seconds"]<=MAX_TTL
               and type(spec["max_uses"]) is int and 1<=spec["max_uses"]<=MAX_USES,
               "bounded explicit grant required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        u._actor(conn,{"user_id":spec["owner"],"role":"operator"},"operator")
        _inventory(conn)
        monitor._get(conn,spec["monitor_id"],monitor._today())
        if conn.execute("SELECT count(*) FROM lifecycle_ingestion_grants").fetchone()[0]>=MAX_GRANTS:
            raise access.AccessDenied("grant storage limit")
        token=secrets.token_hex(32)
        gid=secrets.token_hex(16)
        row={"id":gid,"token_hash":_hash(token),"monitor":spec["monitor_id"],"owner":spec["owner"],
             "issued_by":actor["user_id"],"expires":int(time.time())+spec["ttl_seconds"],
             "scopes":json.dumps(sorted(spec["scopes"])),"max_uses":spec["max_uses"]}
        seq=u._audit(conn,actor,ISSUE,gid+":"+_binding(row))
        conn.execute("INSERT INTO lifecycle_ingestion_grants VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                     tuple(row[k] for k in ("id","token_hash","monitor","owner","issued_by","expires","scopes","max_uses"))+(seq,))
        return {"grant_id":gid,"bearer_token":token,"expires":row["expires"],
                "monitor_id":row["monitor"],"scopes":json.loads(row["scopes"])}


def revoke(directory,actor,gid):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        row=conn.execute("SELECT revoked_seq FROM lifecycle_ingestion_grants WHERE id=?",(gid,)).fetchone()
        if not row:
            raise access.AccessDenied("unknown grant")
        if row["revoked_seq"] is None:
            seq=u._audit(conn,actor,REVOKE,gid)
            conn.execute("UPDATE lifecycle_ingestion_grants SET revoked_seq=? WHERE id=?",(seq,gid))


def grants(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        u._actor(conn,actor,"admin")
        _inventory(conn)
        return [{"id":r["id"],"monitor_id":r["monitor"],"owner":r["owner"],
                 "scopes":json.loads(r["scopes"]),"expires":r["expires"],
                 "revoked":r["revoked_seq"] is not None,"max_uses":r["max_uses"],
                 "used":conn.execute("SELECT count(*) FROM lifecycle_ingestion_requests WHERE grant_id=?",(r["id"],)).fetchone()[0]}
                for r in conn.execute("SELECT * FROM lifecycle_ingestion_grants ORDER BY audit_seq")]


def _grant(conn,token):
    if not isinstance(token,str) or not re.fullmatch(r"[0-9a-f]{64}",token):
        raise access.AccessDenied("invalid capability")
    digest=_hash(token)
    row=conn.execute("SELECT * FROM lifecycle_ingestion_grants WHERE token_hash=?",(digest,)).fetchone()
    if not row or not hmac.compare_digest(row["token_hash"],digest) or row["revoked_seq"] is not None or int(time.time())>=row["expires"]:
        raise access.AccessDenied("invalid capability")
    actor={"user_id":row["owner"],"role":"operator"}
    u._actor(conn,actor,"operator")
    return row,actor


def _feed(directory,programme,p,status):
    c=monitor._coverage(directory,programme,p,status)
    # The stable opaque alert ID allows an external approved scheduler to
    # deduplicate. No raw metric counts, incident IDs or external digests.
    alerts=[{"id":d._digest([p["digest"],a["id"]]),"kind":a["kind"],"severity":a["severity"]}
            for a in status["alerts"] if a["requires_attention"]]
    if not c["buyer_scope_current"]:
        alerts.append({"id":d._digest([p["digest"],"scope_stale"]),"kind":"scope_stale","severity":"serious"})
    if status["schedule_ended"]:
        alerts.append({"id":d._digest([p["digest"],"schedule_ended"]),"kind":"schedule_ended","severity":"moderate"})
    return {"schema":"lifecycle-monitor-feed/1","monitor_id":c["id"],"plan_digest":p["digest"],
            "as_of":status["as_of"],"alerts":sorted(alerts,key=lambda r:r["id"]),
            "attention_required":bool(alerts),"decision_grade":False,"deployment_authorized":False}


def submit(directory,token,doc):
    d._keys(doc,"schema request_id events confirmed")
    d._require(doc["schema"]=="lifecycle-ingestion/1" and d._token(doc["request_id"])
               and doc["confirmed"] is True and isinstance(doc["events"],list)
               and 1<=len(doc["events"])<=MAX_BATCH,"explicit bounded event batch required")
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _inventory(conn)
        grant,actor=_grant(conn,token)
        scopes=json.loads(grant["scopes"])
        for event in doc["events"]:
            d._keys(event,"kind document")
            d._require(event["kind"] in ("report","incident") and event["kind"] in scopes,"grant scope exceeded")
        request_digest=d._digest(doc)
        prior=conn.execute("SELECT * FROM lifecycle_ingestion_requests WHERE grant_id=? AND request_id=?",
                           (grant["id"],doc["request_id"])).fetchone()
        if prior:
            if prior["request_digest"]!=request_digest:
                raise access.AccessDenied("request identity already used")
            return json.loads(prior["payload"])
        if conn.execute("SELECT count(*) FROM lifecycle_ingestion_requests WHERE grant_id=?",(grant["id"],)).fetchone()[0]>=grant["max_uses"]:
            raise access.AccessDenied("grant usage limit")
        # All events and the receipt share one transaction; any failure
        # rolls back the entire batch and its audit rows.
        digests=[monitor._event_locked(directory,conn,actor,grant["monitor"],e["kind"],e["document"]) for e in doc["events"]]
        payload={"schema":"lifecycle-ingestion-receipt/1","grant_id":grant["id"],"request_id":doc["request_id"],
                 "monitor_id":grant["monitor"],"event_digests":digests,"decision_grade":False}
        digest=d._digest(payload)
        seq=u._audit(conn,actor,USE,grant["id"]+":"+doc["request_id"]+":"+request_digest+":"+digest)
        conn.execute("INSERT INTO lifecycle_ingestion_requests VALUES(?,?,?,?,?,?)",
                     (grant["id"],doc["request_id"],request_digest,json.dumps(payload),digest,seq))
        return payload


def check(directory,token):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        _inventory(conn)
        grant,_=_grant(conn,token)
        if "check" not in json.loads(grant["scopes"]):
            raise access.AccessDenied("check scope required")
        programme,p,_,status=monitor._get(conn,grant["monitor"],monitor._today())
        return _feed(directory,programme,p,status)


class App:
    def __init__(self,directory,port):
        self.directory=directory
        self.host="127.0.0.1:"+str(port)

    def __call__(self,env,start):
        def respond(status,doc):
            raw=json.dumps(doc,ensure_ascii=True,allow_nan=False,separators=(",",":")).encode("ascii")
            start(status,[("Content-Type","application/json"),("Content-Length",str(len(raw))),
                          ("Cache-Control","no-store"),("X-Content-Type-Options","nosniff")])
            return [raw]
        try:
            # No browser cookies or cross-origin requests. No remote bind.
            if env.get("HTTP_HOST")!=self.host or env.get("HTTP_ORIGIN") or env.get("HTTP_COOKIE") or env.get("QUERY_STRING"):
                raise access.AccessDenied("invalid request")
            auth=env.get("HTTP_AUTHORIZATION","")
            if not auth.startswith("Bearer "):
                raise access.AccessDenied("capability required")
            token=auth[7:]
            path,method=env.get("PATH_INFO"),env.get("REQUEST_METHOD")
            if path=="/v1/monitor/check" and method=="GET":
                return respond("200 OK",check(self.directory,token))
            if path=="/v1/monitor/ingest" and method=="POST":
                if env.get("CONTENT_TYPE")!="application/json":
                    raise ValueError("JSON required")
                n=int(env.get("CONTENT_LENGTH","0"))
                if not 1<=n<=MAX_BODY:
                    raise ValueError("body size")
                raw=env["wsgi.input"].read(n)
                if len(raw)!=n:
                    raise ValueError("incomplete body")
                return respond("200 OK",submit(self.directory,token,buyer_manual._parse(raw)))
            return respond("404 Not Found",{"error":"not_found"})
        except (ValueError,RuntimeError,KeyError,TypeError,OSError,AttributeError,RecursionError):
            return respond("403 Forbidden",{"error":"request_refused"})


class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):
        pass


def serve(directory,port):
    d._require(type(port) is int and 1024<=port<=65535,"valid local port required")
    with make_server("127.0.0.1",port,App(directory,port),handler_class=QuietHandler) as server:
        server.serve_forever()


def configure(parser):
    parser.add_argument("directory")
    parser.add_argument("--port",type=int,default=8772)
    parser.set_defaults(func=lambda args:serve(args.directory,args.port))
