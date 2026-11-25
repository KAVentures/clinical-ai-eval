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
from . import buyer_manual, buyer_agent

MAX_GRANTS=200
MAX_TTL=86400
MAX_USES=300
MAX_TOTAL_REQUESTS=5000
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
    counts={}
    for r in requests:
        grant=by[r["grant_id"]]
        counts[grant["id"]]=counts.get(grant["id"],0)+1
        if counts[grant["id"]]>grant["max_uses"]:
            raise access.AccessDenied("grant usage inventory mismatch")
        payload=json.loads(r["payload"])
        if (d._digest(payload)!=r["digest"] or r["audit_seq"]<=grant["audit_seq"]
                or (grant["revoked_seq"] is not None and r["audit_seq"]>=grant["revoked_seq"])):
            raise access.AccessDenied("ingestion request integrity mismatch")
        event=conn.execute("SELECT at FROM audit WHERE seq=?",(r["audit_seq"],)).fetchone()
        if not event or event["at"]>=grant["expires"]:
            raise access.AccessDenied("ingestion receipt outside grant lifetime")
        d._keys(payload,"schema grant_id request_id monitor_id event_digests decision_grade")
        if (payload["schema"]!="lifecycle-ingestion-receipt/1" or payload["grant_id"]!=grant["id"]
                or payload["request_id"]!=r["request_id"] or payload["monitor_id"]!=grant["monitor"]
                or payload["decision_grade"] is not False or not isinstance(payload["event_digests"],list)
                or not 1<=len(payload["event_digests"])<=MAX_BATCH):
            raise access.AccessDenied("invalid receipt binding")
        for digest in payload["event_digests"]:
            event=conn.execute("SELECT 1 FROM lifecycle_monitor_events WHERE monitor=? AND digest=? AND kind IN ('report','incident') AND audit_seq<?",
                               (grant["monitor"],digest,r["audit_seq"])).fetchone()
            if not event:
                raise access.AccessDenied("receipt references unavailable monitor event")
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


def revoke_restored_grants(conn):
    """Trusted stopped-service restore only, inside the restore transaction."""
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ledger={"lifecycle_ingestion_grants","lifecycle_ingestion_requests"}
    if not ledger<=tables:
        if tables&ledger or conn.execute(
                "SELECT 1 FROM audit WHERE action IN (?,?,?) LIMIT 1",(ISSUE,REVOKE,USE)).fetchone():
            raise access.AccessDenied("incomplete restored ingestion ledger")
        return 0
    _inventory(conn)
    rows=conn.execute("SELECT id FROM lifecycle_ingestion_grants WHERE revoked_seq IS NULL ORDER BY id").fetchall()
    for row in rows:
        access._audit(conn,"local-admin",REVOKE,row["id"])
        seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("UPDATE lifecycle_ingestion_grants SET revoked_seq=? WHERE id=?",(seq,row["id"]))
    _inventory(conn)
    return len(rows)


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
        grant,actor=_grant(conn,token)
        _inventory(conn)
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
        if conn.execute("SELECT count(*) FROM lifecycle_ingestion_requests").fetchone()[0]>=MAX_TOTAL_REQUESTS:
            raise access.AccessDenied("ingestion receipt storage limit")
        # All events and the receipt share one transaction; any failure
        # rolls back the entire batch and its audit rows.
        digests=[monitor._event_locked(directory,conn,actor,grant["monitor"],e["kind"],e["document"]) for e in doc["events"]]
        payload={"schema":"lifecycle-ingestion-receipt/1","grant_id":grant["id"],"request_id":doc["request_id"],
                 "monitor_id":grant["monitor"],"event_digests":digests,"decision_grade":False}
        digest=d._digest(payload)
        # Recheck expiry after the batch; an expired transaction rolls back.
        _grant(conn,token)
        seq=u._audit(conn,actor,USE,grant["id"]+":"+doc["request_id"]+":"+request_digest+":"+digest)
        conn.execute("INSERT INTO lifecycle_ingestion_requests VALUES(?,?,?,?,?,?)",
                     (grant["id"],doc["request_id"],request_digest,json.dumps(payload),digest,seq))
        return payload


def check(directory,token):
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN")
        grant,_=_grant(conn,token)
        _inventory(conn)
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
        except (buyer_agent.BuyerError,ValueError,RuntimeError,KeyError,TypeError,OSError,AttributeError,RecursionError):
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


def openapi():
    """Structural transport schema; native monitor rules are also enforced."""
    def obj(fields):
        return {"type":"object","properties":fields,"required":list(fields),"additionalProperties":False}
    token={"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"}
    digest={"type":"string","pattern":"^[a-f0-9]{64}$"}
    count={"type":"integer","minimum":0,"maximum":10000000}
    target=obj({"name":{"type":"string"},"version":{"type":"string"}})
    report=obj({"plan_digest":digest,"target":target,"window_index":{"type":"integer","minimum":0,"maximum":103},
                "metrics":{"type":"array","minItems":1,"maxItems":12,"items":obj({"id":token,"numerator":count,"denominator":count})},
                "source_digest":digest,"complete_aggregate_attested":{"const":True}})
    incident=obj({"plan_digest":digest,"incident_id":token,"date":{"type":"string","format":"date"},
                  "severity":{"enum":["low","moderate","serious","critical"]},
                  "category":{"enum":["clinical_safety","data_quality","privacy_security","workflow","availability","version_change","other"]},
                  "external_record_digest":digest,"observed_version":token})
    envelope=obj({"schema":{"const":"lifecycle-ingestion/1"},"request_id":token,"confirmed":{"const":True},
                  "events":{"type":"array","minItems":1,"maxItems":MAX_BATCH,"items":{"oneOf":[
                      obj({"kind":{"const":"report"},"document":report}),
                      obj({"kind":{"const":"incident"},"document":incident})]}}})
    receipt=obj({"schema":{"const":"lifecycle-ingestion-receipt/1"},"grant_id":token,"request_id":token,
                 "monitor_id":token,"event_digests":{"type":"array","minItems":1,"maxItems":MAX_BATCH,"items":digest},
                 "decision_grade":{"const":False}})
    feed=obj({"schema":{"const":"lifecycle-monitor-feed/1"},"monitor_id":token,"plan_digest":digest,
              "as_of":{"type":"string","format":"date"},"alerts":{"type":"array","items":obj({
                  "id":digest,"kind":token,"severity":{"enum":["low","moderate","serious","critical"]}})},
              "attention_required":{"type":"boolean"},"decision_grade":{"const":False},"deployment_authorized":{"const":False}})
    def replies(schema):
        return {"200":{"description":"Scoped result","content":{"application/json":{"schema":schema}}},
                "403":{"description":"Request refused"}}
    return {"openapi":"3.1.0","info":{"title":"Local aggregate monitoring ingestion","version":"1.0.0"},
            "servers":[{"url":"http://127.0.0.1:8772"}],
            "components":{"securitySchemes":{"localGrant":{"type":"http","scheme":"bearer"}}},
            "security":[{"localGrant":[]}],
            "paths":{"/v1/monitor/ingest":{"post":{"operationId":"ingestMonitorEvents",
                "requestBody":{"required":True,"content":{"application/json":{"schema":envelope}}},
                "responses":replies(receipt)}},
                     "/v1/monitor/check":{"get":{"operationId":"checkMonitorAlerts","responses":replies(feed)}}}}


def configure_openapi(parser):
    parser.set_defaults(func=lambda args:print(json.dumps(openapi(),indent=2)))
