"""Local durable notification acceptance; not proof of human delivery."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIRequestHandler

MAX_BODY = 1024 * 1024
MAX_EVENTS = 3200
MAX_BATCHES = 10000
KINDS = {'missing_window','low_volume','threshold','version_overlap','version_change','incident','scope_stale','schedule_ended'}
SEVERITIES = {'low','moderate','serious','critical'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def hex64(s):
    return isinstance(s,str) and re.fullmatch(r'[a-f0-9]{64}',s) is not None


def token(s):
    return isinstance(s,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',s) is not None


def keys(obj, fields):
    require(isinstance(obj,dict) and set(obj)==set(fields.split()),'invalid fields')


def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode('ascii')).hexdigest()


def parse(raw):
    def pairs(items):
        result={}
        for k,v in items:
            require(k not in result,'duplicate JSON key')
            result[k]=v
        return result
    def invalid(value):
        raise ValueError('nonfinite JSON')
    return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=invalid)


def private_path(path, existing=True):
    p=Path(path).absolute()
    require(p.parent.is_dir() and not p.is_symlink() and not any(x.is_symlink() for x in p.parents),'invalid private path')
    if existing:
        s=p.stat()
        require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and (os.name!='posix' or not s.st_mode&0o077),'private regular file required')
    return p


@contextmanager
def db(path):
    p=private_path(path)
    conn=sqlite3.connect(p.as_uri()+'?mode=rw',uri=True,timeout=10)
    try:
        conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA synchronous=FULL')
        require(conn.execute('PRAGMA journal_mode').fetchone()[0]=='delete','unsupported journal mode')
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(path,monitor_id,plan_digest):
    require(token(monitor_id) and hex64(plan_digest),'invalid monitor scope')
    p=private_path(path,False)
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    with db(p) as conn:
        conn.execute('CREATE TABLE scope(id INTEGER PRIMARY KEY CHECK(id=1), monitor TEXT NOT NULL, plan TEXT NOT NULL)')
        conn.execute('INSERT INTO scope VALUES(1,?,?)',(monitor_id,plan_digest))
        conn.execute('''CREATE TABLE batches(id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                     payload_digest TEXT NOT NULL, accepted_at INTEGER NOT NULL, reviewed_at INTEGER,
                     review_digest TEXT)''')
    return {'monitor_id':monitor_id,'plan_digest':plan_digest}


def validate(doc):
    keys(doc,'schema attempt_digest monitor_id plan_digest as_of sequence events')
    require(doc['schema']=='monitor-alert-delivery/1' and hex64(doc['attempt_digest']) and token(doc['monitor_id'])
            and hex64(doc['plan_digest']) and type(doc['sequence']) is int and 1<=doc['sequence']<=2**53-1,'invalid delivery envelope')
    require(date.fromisoformat(doc['as_of']).isoformat()==doc['as_of'],'invalid date')
    require(isinstance(doc['events'],list) and 1<=len(doc['events'])<=MAX_EVENTS,'invalid event inventory')
    seen=set()
    for event in doc['events']:
        transition=event['transition']
        require(transition in ('newly_reported','severity_changed','no_longer_reported'),'invalid transition')
        keys(event,'transition alert previous_severity' if transition=='severity_changed' else 'transition alert')
        alert=event['alert'];keys(alert,'id kind severity')
        require(hex64(alert['id']) and alert['id'] not in seen and alert['kind'] in KINDS and alert['severity'] in SEVERITIES,'invalid alert')
        seen.add(alert['id'])
        if transition=='severity_changed':
            require(event['previous_severity'] in SEVERITIES and event['previous_severity']!=alert['severity'],'invalid severity transition')
    require(len(canonical(doc).encode('ascii'))<=MAX_BODY,'delivery too large')
    return doc


def _scope(conn):
    rows=conn.execute('SELECT * FROM scope').fetchall()
    require(len(rows)==1 and rows[0]['id']==1 and token(rows[0]['monitor']) and hex64(rows[0]['plan']),'invalid relay scope')
    return rows[0]['monitor'],rows[0]['plan']


def _row(row,scope):
    doc=validate(parse(row['payload'].encode('ascii')))
    require((doc['monitor_id'],doc['plan_digest'])==scope and doc['attempt_digest']==row['id']
            and doc['sequence']==row['sequence'] and digest(doc)==row['payload_digest'],'stored payload mismatch')
    require(type(row['accepted_at']) is int and row['accepted_at']>=0 and
            ((row['reviewed_at'] is None and row['review_digest'] is None) or
             (type(row['reviewed_at']) is int and row['reviewed_at']>=row['accepted_at'] and hex64(row['review_digest']))),'invalid review metadata')
    return doc


def accept(path,doc,key):
    doc=validate(doc)
    require(key==doc['attempt_digest'],'idempotency key mismatch')
    with db(path) as conn:
        scope=_scope(conn)
        require((doc['monitor_id'],doc['plan_digest'])==scope,'relay scope mismatch')
        row=conn.execute('SELECT * FROM batches WHERE id=?',(key,)).fetchone()
        if row:
            require(_row(row,scope)==doc,'conflicting content for attempt')
        else:
            require(conn.execute('SELECT count(*) FROM batches').fetchone()[0]<MAX_BATCHES,'relay capacity exhausted')
            conn.execute('INSERT INTO batches VALUES(?,?,?,?,?,NULL,NULL)',(key,doc['sequence'],canonical(doc),digest(doc),int(time.time())))
    return {'schema':'monitor-alert-delivery-receipt/1','attempt_digest':key,'accepted':True}


def inbox(path,limit=100):
    require(type(limit) is int and 1<=limit<=200,'invalid page size')
    with db(path) as conn:
        scope=_scope(conn)
        n=conn.execute('SELECT count(*) FROM batches WHERE reviewed_at IS NULL').fetchone()[0]
        rows=conn.execute('SELECT * FROM batches WHERE reviewed_at IS NULL ORDER BY accepted_at,id LIMIT ?',(limit,)).fetchall()
        return {'pending_count':n,'batches':[{'envelope':_row(r,scope),'payload_digest':r['payload_digest'],'accepted_at':r['accepted_at']} for r in rows],
                'human_delivery_verified':False,'clinical_safety_established':False}


def review(path,attempt_digest,payload_digest,external_record_digest,*,confirmed=False):
    require(confirmed is True and all(hex64(x) for x in (attempt_digest,payload_digest,external_record_digest)),'explicit exact review required')
    with db(path) as conn:
        scope=_scope(conn)
        row=conn.execute('SELECT * FROM batches WHERE id=?',(attempt_digest,)).fetchone()
        require(row is not None,'unknown attempt')
        _row(row,scope)
        require(row['payload_digest']==payload_digest,'review digest mismatch')
        if row['reviewed_at'] is None:
            at=max(int(time.time()),row['accepted_at'])
            conn.execute('UPDATE batches SET reviewed_at=?,review_digest=? WHERE id=?',(at,external_record_digest,attempt_digest))
        else:
            require(row['review_digest']==external_record_digest,'review already recorded')
    return {'attempt_digest':attempt_digest,'review_record_attested':True,'alert_resolved':False,'human_delivery_verified':False}


def secret(path):
    p=private_path(path)
    fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    try:
        s=os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and (os.name!='posix' or not s.st_mode&0o077),'private credential required')
        raw=os.read(fd,128)
    finally:
        os.close(fd)
    require(re.fullmatch(rb'[a-f0-9]{64}\n?',raw) is not None,'invalid credential')
    return raw.strip().decode('ascii')


class App:
    def __init__(self,path,port,secret_file):
        self.path=path;self.host='127.0.0.1:'+str(port);self.secret_file=secret_file

    def __call__(self,env,start):
        def response(status,doc):
            raw=canonical(doc).encode('ascii')
            start(status,[('Content-Type','application/json'),('Content-Length',str(len(raw))),('Cache-Control','no-store')])
            return [raw]
        try:
            require(env.get('HTTP_HOST')==self.host and not any(env.get(k) for k in ('HTTP_ORIGIN','HTTP_COOKIE','QUERY_STRING')),'invalid request origin')
            auth=env.get('HTTP_AUTHORIZATION','')
            require(auth.isascii() and hmac.compare_digest(auth,'Bearer '+secret(self.secret_file)),'invalid bearer')
            require(env.get('PATH_INFO')=='/v1/alerts/deliver' and env.get('REQUEST_METHOD')=='POST' and env.get('CONTENT_TYPE')=='application/json','invalid route')
            size=int(env.get('CONTENT_LENGTH','0'));require(1<=size<=MAX_BODY,'invalid length')
            raw=env['wsgi.input'].read(size);require(len(raw)==size,'truncated request')
            return response('200 OK',accept(self.path,parse(raw),env.get('HTTP_IDEMPOTENCY_KEY')))
        except (ValueError,TypeError,KeyError,AttributeError,OSError,sqlite3.Error,RecursionError):
            return response('403 Forbidden',{'error':'request_refused'})


class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):
        pass


def serve(path,port,secret_file):
    require(type(port) is int and 1024<=port<=65535,'invalid local port')
    secret(secret_file)
    with db(path) as conn:
        _scope(conn)
    with make_server('127.0.0.1',port,App(path,port,secret_file),handler_class=QuietHandler) as server:
        server.serve_forever()


def configure(parser):
    sub=parser.add_subparsers(dest='relay_action',required=True)
    p=sub.add_parser('initialize');p.add_argument('store');p.add_argument('monitor_id');p.add_argument('plan_digest')
    p=sub.add_parser('serve');p.add_argument('store');p.add_argument('--port',type=int,default=8773);p.add_argument('--secret-file',required=True)
    p=sub.add_parser('inbox');p.add_argument('store');p.add_argument('--limit',type=int,default=100)
    p=sub.add_parser('review');p.add_argument('store');p.add_argument('attempt_digest');p.add_argument('payload_digest');p.add_argument('external_record_digest');p.add_argument('--confirm',action='store_true')
    parser.set_defaults(func=cli)


def cli(args):
    try:
        if args.relay_action=='serve':
            return serve(args.store,args.port,args.secret_file)
        if args.relay_action=='initialize':
            result=initialize(args.store,args.monitor_id,args.plan_digest)
        elif args.relay_action=='inbox':
            result=inbox(args.store,args.limit)
        else:
            result=review(args.store,args.attempt_digest,args.payload_digest,args.external_record_digest,confirmed=args.confirm)
        print(canonical(result))
    except (ValueError,TypeError,KeyError,AttributeError,OSError,sqlite3.Error,RecursionError):
        raise SystemExit('Local notification relay operation refused.')
