"""One scheduler-driven local alert delivery cycle; no clinical actions."""
import http.client
import json
import os
import re
import sqlite3
import stat
from . import monitor_alert_store as store, monitor_alert_outbox as outbox

MAX_FEED = 512*1024
MAX_REPLY = 8192


def _config(source_port, sink_port, timeout):
    if (any(type(p) is not int or not 1024 <= p <= 65535 for p in (source_port, sink_port))
            or source_port == sink_port or type(timeout) is not int or not 1 <= timeout <= 60):
        raise ValueError('invalid distinct local endpoints or timeout')
    return {'source_port':source_port,'sink_port':sink_port,'timeout':timeout}


def _binding(state, config):
    return outbox._seal({'schema':'monitor-alert-worker/1','store_id':state['store_id'],
                         'scope':state['scope'],'config':_config(**config)})


def preview(path, source_port, sink_port, timeout=10):
    with store._db(store._path(path,existing=True)) as conn:
        return _binding(store._load(conn),_config(source_port,sink_port,timeout))


def bind(path, config, approval, *, confirmed=False):
    if confirmed is not True:
        raise ValueError('explicit local notification handoff approval required')
    with store._db(store._path(path,existing=True)) as conn:
        state=store._load(conn)
        doc=_binding(state,config)
        if doc['digest']!=approval:
            raise ValueError('worker approval differs')
        conn.execute('CREATE TABLE IF NOT EXISTS worker_binding (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        prior=conn.execute('SELECT payload FROM worker_binding').fetchone()
        if prior:
            if json.loads(prior[0])!=doc:
                raise ValueError('worker already bound to different endpoints')
            return doc
        if state['pending'] is not None:
            raise ValueError('resolve existing pending attempt before binding worker')
        conn.execute('INSERT INTO worker_binding VALUES (1,?)',(store._json(doc),))
        return doc


def _read_binding(path, approval):
    with store._db(store._path(path,existing=True)) as conn:
        state=store._load(conn)
        rows=conn.execute('SELECT id,payload FROM worker_binding').fetchall()
        if len(rows)!=1 or rows[0][0]!=1 or len(rows[0][1])>4096:
            raise ValueError('missing or invalid worker binding')
        doc=json.loads(rows[0][1])
        if doc!=_binding(state,doc['config']) or doc['digest']!=approval:
            raise ValueError('worker binding verification failed')
        return doc,store._public(state)


def _secret(path):
    p=store._path(path,existing=True)
    fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or
                (os.name=='posix' and info.st_mode & 0o077)):
            raise ValueError('private credential file required')
        raw=os.read(fd,128)
    finally:
        os.close(fd)
    if not re.fullmatch(rb'[0-9a-f]{64}\n?',raw):
        raise ValueError('invalid local bearer credential')
    return raw.strip().decode('ascii')


def _parse(raw):
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:
                raise ValueError('duplicate response key')
            result[k]=v
        return result
    def invalid(value):
        raise ValueError('nonfinite response')
    return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=invalid)


def _request(port, path, token, timeout, *, body=None, limit):
    # No DNS, proxies, redirects or credentials in URLs. Only fixed routes.
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:
        headers={'Authorization':'Bearer '+token,'Accept':'application/json','Connection':'close'}
        payload=None
        if body is not None:
            payload=store._json(body).encode('ascii')
            headers['Content-Type']='application/json'
            headers['Idempotency-Key']=body['attempt_digest']
        conn.request('GET' if body is None else 'POST',path,body=payload,headers=headers)
        response=conn.getresponse()
        if (response.status!=200 or response.getheader('Content-Type','').split(';',1)[0].strip().lower()!='application/json'
                or response.getheader('Content-Encoding','identity').lower()!='identity'):
            raise ValueError('local endpoint refused or returned non-JSON')
        raw=response.read(limit+1)
        if len(raw)>limit:
            raise ValueError('local response exceeds limit')
        return _parse(raw)
    finally:
        conn.close()


def envelope(attempt):
    batch=outbox.verify(attempt['batch'])
    feed=batch['next_checkpoint']['feed']
    return {'schema':'monitor-alert-delivery/1','attempt_digest':attempt['digest'],
            'monitor_id':feed['monitor_id'],'plan_digest':feed['plan_digest'],'as_of':feed['as_of'],
            'sequence':attempt['sequence'],'events':batch['events']}


def cycle(path, approval, source_secret, sink_secret):
    binding,state=_read_binding(path,approval)
    cfg=binding['config']
    source,sink=_secret(source_secret),_secret(sink_secret)
    if source==sink:
        raise ValueError('separate source and destination credentials required')
    attempt=state['pending']
    resumed=attempt is not None
    if attempt is None:
        feed=_request(cfg['source_port'],'/v1/monitor/check',source,cfg['timeout'],limit=MAX_FEED)
        attempt=store.enqueue(path,feed)
    if attempt['batch']['delivery_required']:
        doc=envelope(attempt)
        reply=_request(cfg['sink_port'],'/v1/alerts/deliver',sink,cfg['timeout'],body=doc,limit=MAX_REPLY)
        outbox._keys(reply,'schema attempt_digest accepted')
        if reply['schema']!='monitor-alert-delivery-receipt/1' or reply['attempt_digest']!=attempt['digest'] or reply['accepted'] is not True:
            raise ValueError('destination did not acknowledge exact attempt')
        delivered=True
    else:
        delivered=False
    receipt=store.acknowledge(path,attempt['digest'],delivered=delivered)
    return {'schema':'monitor-alert-worker-cycle/1','resumed_pending':resumed,'attempt_digest':attempt['digest'],
            'events':len(attempt['batch']['events']),'destination_acceptance_attested':delivered,
            'acknowledgment_digest':receipt['digest'],'human_delivery_verified':False,'deployment_authorized':False}


def configure(parser):
    sub=parser.add_subparsers(dest='worker_action',required=True)
    for action in ('preview','bind'):
        p=sub.add_parser(action);p.add_argument('store');p.add_argument('--source-port',type=int,required=True)
        p.add_argument('--sink-port',type=int,required=True);p.add_argument('--timeout',type=int,default=10)
        if action=='bind':
            p.add_argument('--approved-digest',required=True);p.add_argument('--confirm-local-handoff',action='store_true')
    p=sub.add_parser('cycle');p.add_argument('store');p.add_argument('--approved-digest',required=True)
    p.add_argument('--source-secret-file',required=True);p.add_argument('--sink-secret-file',required=True)
    parser.set_defaults(func=cli)


def cli(args):
    try:
        if args.worker_action=='preview':
            result=preview(args.store,args.source_port,args.sink_port,args.timeout)
        elif args.worker_action=='bind':
            result=bind(args.store,_config(args.source_port,args.sink_port,args.timeout),args.approved_digest,confirmed=args.confirm_local_handoff)
        else:
            result=cycle(args.store,args.approved_digest,args.source_secret_file,args.sink_secret_file)
        print(store._json(result))
    except (ValueError,TypeError,KeyError,AttributeError,OSError,sqlite3.Error,RecursionError,http.client.HTTPException):
        raise SystemExit('Monitoring worker cycle refused or interrupted; inspect retained pending state.')
