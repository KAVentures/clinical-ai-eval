"""Transactional single-monitor outbox. External delivery remains caller-owned."""
import json
import os
import re
import secrets
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path
from . import monitor_alert_outbox as outbox

MAX_SEQUENCE = 2**53-1


def _json(doc):
    return json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _path(path, *, existing):
    p = Path(path).absolute()
    # The operator must control the directory and all ancestors; reject symlinks.
    for parent in p.parents:
        if parent.is_symlink():
            raise ValueError('symlinked outbox path')
    if not p.parent.is_dir() or p.is_symlink():
        raise ValueError('invalid outbox path')
    if existing:
        info = p.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('outbox must be a regular private file')
        if os.name == 'posix' and info.st_mode & 0o077:
            raise ValueError('outbox file permissions must be private')
    return p


@contextmanager
def _db(p):
    conn = sqlite3.connect(p.as_uri()+'?mode=rw', uri=True, timeout=10)
    try:
        conn.execute('PRAGMA synchronous=FULL')
        if conn.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            raise ValueError('outbox requires DELETE journal mode')
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _scope(monitor_id, plan_digest):
    if not isinstance(monitor_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', monitor_id) or not outbox._hex(plan_digest):
        raise ValueError('invalid monitor scope')
    return {'monitor_id':monitor_id, 'plan_digest':plan_digest}


def _sealed_state(store_id, scope, sequence=0, checkpoint=None, pending=None, last_ack=None):
    return outbox._seal({'schema':'monitor-alert-store/1','store_id':store_id,'scope':scope,
                         'sequence':sequence,'checkpoint':checkpoint,'pending':pending,'last_ack':last_ack})


def initialize(path, monitor_id, plan_digest):
    scope = _scope(monitor_id, plan_digest)
    p = _path(path, existing=False)
    fd = os.open(p, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    os.close(fd)
    state = _sealed_state(secrets.token_hex(32), scope)
    # An interrupted initialization is deliberately not auto-reinitialized.
    with _db(p) as conn:
        conn.execute('CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        conn.execute('INSERT INTO state VALUES (1,?)', (_json(state),))
    return _public(state)


def _scope_matches(state, checkpoint):
    feed = outbox._checkpoint(checkpoint)['feed']
    if _scope(feed['monitor_id'], feed['plan_digest']) != state['scope']:
        raise ValueError('feed scope differs from store')


def _attempt(state, batch):
    return outbox._seal({'schema':'monitor-alert-attempt/1','store_id':state['store_id'],
                         'sequence':state['sequence']+1,'batch':batch})


def _receipt(attempt, delivered):
    return outbox._seal({'schema':'monitor-alert-ack/1','attempt_digest':attempt['digest'],
                         'sequence':attempt['sequence'],'checkpoint_digest':attempt['batch']['next_checkpoint']['digest'],
                         'delivery_attested':delivered,'delivery_verified':False})


def _load(conn):
    rows = conn.execute('SELECT id,payload FROM state').fetchall()
    if len(rows)!=1 or rows[0][0]!=1 or len(rows[0][1])>4*1024*1024:
        raise ValueError('invalid outbox state inventory')
    state = json.loads(rows[0][1])
    outbox._keys(state,'schema store_id scope sequence checkpoint pending last_ack digest')
    seq, cp, pending, ack = (state[k] for k in ('sequence','checkpoint','pending','last_ack'))
    scope = _scope(**state['scope'])
    if (state['schema']!='monitor-alert-store/1' or not outbox._hex(state['store_id'])
        or type(seq) is not int or not 0<=seq<=MAX_SEQUENCE
        or _sealed_state(state['store_id'],scope,seq,cp,pending,ack)!=state):
        raise ValueError('outbox state digest or shape mismatch')
    if seq==0:
        if cp is not None or ack is not None:
            raise ValueError('invalid initial outbox state')
    else:
        _scope_matches(state,cp)
        outbox._keys(ack,'schema attempt_digest sequence checkpoint_digest delivery_attested delivery_verified digest')
        if (ack['schema']!='monitor-alert-ack/1' or ack['sequence']!=seq or ack['checkpoint_digest']!=cp['digest']
            or not outbox._hex(ack['attempt_digest']) or type(ack['delivery_attested']) is not bool
            or ack['delivery_verified'] is not False or outbox._seal({k:v for k,v in ack.items() if k!='digest'})!=ack):
            raise ValueError('invalid last acknowledgment')
    if pending is not None:
        batch=outbox.verify(pending['batch'])
        _scope_matches(state,batch['next_checkpoint'])
        if seq>=MAX_SEQUENCE or batch['previous']!=cp or _attempt(state,batch)!=pending:
            raise ValueError('invalid pending attempt binding')
    return state


def _public(state):
    return {'schema':'monitor-alert-store-status/1','store_id':state['store_id'],'scope':state['scope'],
            'sequence':state['sequence'],'checkpoint':state['checkpoint'],'pending':state['pending'],
            'last_ack':state['last_ack'],'delivery_verified':False,'deployment_authorized':False}


def _write(conn,state):
    conn.execute('UPDATE state SET payload=? WHERE id=1',(_json(state),))


def status(path):
    with _db(_path(path,existing=True)) as conn:
        return _public(_load(conn))


def enqueue(path,feed):
    with _db(_path(path,existing=True)) as conn:
        state=_load(conn)
        batch=outbox.prepare(feed,state['checkpoint'])
        _scope_matches(state,batch['next_checkpoint'])
        if state['pending'] is not None:
            if state['pending']['batch']!=batch:
                raise ValueError('unacknowledged attempt exists; retain it before polling again')
            return state['pending']
        if state['sequence']>=MAX_SEQUENCE:
            raise ValueError('outbox sequence exhausted')
        attempt=_attempt(state,batch)
        _write(conn,_sealed_state(state['store_id'],state['scope'],state['sequence'],state['checkpoint'],attempt,state['last_ack']))
        return attempt


def acknowledge(path,expected_digest,*,delivered=False):
    if not outbox._hex(expected_digest) or type(delivered) is not bool:
        raise ValueError('exact attempt digest and explicit attestation required')
    with _db(_path(path,existing=True)) as conn:
        state=_load(conn)
        prior=state['last_ack']
        if prior is not None and prior['attempt_digest']==expected_digest:
            if prior['delivery_attested']!=delivered:
                raise ValueError('acknowledgment attestation changed')
            return prior
        attempt=state['pending']
        if attempt is None or attempt['digest']!=expected_digest:
            raise ValueError('unknown pending attempt')
        batch=attempt['batch']
        cp=outbox.acknowledge(batch,batch['digest'],delivered=delivered)
        receipt=_receipt(attempt,delivered)
        _write(conn,_sealed_state(state['store_id'],state['scope'],attempt['sequence'],cp,None,receipt))
        return receipt


def configure(parser):
    sub=parser.add_subparsers(dest='store_action',required=True)
    p=sub.add_parser('initialize')
    p.add_argument('store');p.add_argument('monitor_id');p.add_argument('plan_digest')
    p=sub.add_parser('status');p.add_argument('store')
    p=sub.add_parser('enqueue');p.add_argument('store');p.add_argument('feed')
    p=sub.add_parser('acknowledge');p.add_argument('store');p.add_argument('--expected-digest',required=True)
    p.add_argument('--confirm-delivered',action='store_true')
    parser.set_defaults(func=cli)


def cli(args):
    try:
        if args.store_action=='initialize':
            result=initialize(args.store,args.monitor_id,args.plan_digest)
        elif args.store_action=='status':
            result=status(args.store)
        elif args.store_action=='enqueue':
            result=enqueue(args.store,outbox._read(args.feed))
        else:
            result=acknowledge(args.store,args.expected_digest,delivered=args.confirm_delivered)
        print(_json(result))
    except (ValueError,TypeError,KeyError,AttributeError,OSError,sqlite3.Error,RecursionError):
        raise SystemExit('Monitor alert store operation refused.')
