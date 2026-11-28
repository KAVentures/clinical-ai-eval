"""Durable single-cycle guard and operational heartbeat, not clinical safety."""
import json
import os
import secrets
import sqlite3
import time
from . import monitor_alert_relay as relay

MAX_FAILURES = 2**53-1


def _now():
    return int(time.time())


def _policy(binding, max_gap, max_run):
    relay.require(relay.hex64(binding) and type(max_gap) is int and 60 <= max_gap <= 86400
                  and type(max_run) is int and 1 <= max_run <= max_gap, 'invalid heartbeat policy')
    return {'binding':binding,'max_gap':max_gap,'max_run':max_run}


def _seal(state):
    return {**state,'digest':relay.digest(state)}


def initialize(path,binding,max_gap,max_run):
    policy=_policy(binding,max_gap,max_run)
    p=relay.private_path(path,False)
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    state=_seal({'schema':'monitor-alert-health/1','policy':policy,'created_at':_now(),
                 'last_success':None,'failures':0,'run':None})
    with relay.db(p) as conn:
        conn.execute('CREATE TABLE heartbeat(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        conn.execute('INSERT INTO heartbeat VALUES(1,?)',(relay.canonical(state),))
    return state


def _load(conn):
    rows=conn.execute('SELECT id,payload FROM heartbeat').fetchall()
    relay.require(len(rows)==1 and rows[0]['id']==1 and len(rows[0]['payload'])<=4096,'invalid heartbeat inventory')
    s=relay.parse(rows[0]['payload'].encode('ascii'))
    relay.keys(s,'schema policy created_at last_success failures run digest')
    relay.require(s['schema']=='monitor-alert-health/1' and _policy(**s['policy'])==s['policy']
                  and relay.digest({k:v for k,v in s.items() if k!='digest'})==s['digest'], 'heartbeat digest mismatch')
    relay.require(type(s['created_at']) is int and s['created_at']>=0 and type(s['failures']) is int
                  and 0<=s['failures']<=MAX_FAILURES,'invalid heartbeat counters')
    success=s['last_success']; run=s['run']
    relay.require(success is None or type(success) is int and success>=s['created_at'],'invalid success time')
    if run is not None:
        relay.keys(run,'id started ended state')
        relay.require(relay.hex64(run['id']) and type(run['started']) is int and run['started']>=s['created_at']
                      and run['state'] in ('running','succeeded','failed','interrupted'),'invalid run')
        relay.require((run['state']=='running' and run['ended'] is None) or
                      (run['state']!='running' and type(run['ended']) is int and run['ended']>=run['started']), 'invalid run end')
        if run['state']=='succeeded':
            relay.require(success==run['ended'] and s['failures']==0,'invalid successful heartbeat')
        else:
            relay.require(success is None or success<=run['started'],'invalid previous success')
    return s


def _save(conn,s):
    s=_seal({k:v for k,v in s.items() if k!='digest'})
    conn.execute('UPDATE heartbeat SET payload=? WHERE id=1',(relay.canonical(s),))
    return s


def _clock(s):
    now=_now()
    run=s['run']
    last=max(s['created_at'],s['last_success'] or 0,(run['ended'] or run['started']) if run else 0)
    relay.require(now>=last,'clock moved backwards')
    return now


def start(path,binding):
    with relay.db(path) as conn:
        s=_load(conn)
        relay.require(s['policy']['binding']==binding,'worker binding mismatch')
        relay.require(s['run'] is None or s['run']['state']!='running','cycle already running; investigate before recovery')
        s['run']={'id':secrets.token_hex(32),'started':_clock(s),'ended':None,'state':'running'}
        _save(conn,s)
        return s['run']['id']


def finish(path,run_id,state,*,process_stopped=False):
    relay.require(state in ('succeeded','failed','interrupted') and relay.hex64(run_id),'invalid finish')
    relay.require(state!='interrupted' or process_stopped is True,'explicit stopped-process confirmation required')
    with relay.db(path) as conn:
        s=_load(conn); run=s['run']
        relay.require(run is not None and run['id']==run_id and run['state']=='running','run not active')
        now=_clock(s)
        run.update(ended=now,state=state)
        if state=='succeeded':
            s.update(last_success=now,failures=0)
        else:
            relay.require(s['failures']<MAX_FAILURES,'failure counter exhausted')
            s['failures']+=1
        return _save(conn,s)


def status(path):
    with relay.db(path) as conn:
        s=_load(conn); run=s['run']; issues=[]
        try:
            now=_clock(s)
        except ValueError:
            now=_now();issues.append('clock_regression')
        if s['last_success'] is None:
            issues.append('no_success_recorded')
        elif now-s['last_success']>=s['policy']['max_gap']:
            issues.append('success_overdue')
        if run and run['state']=='running':
            issues.append('cycle_overdue' if now-run['started']>=s['policy']['max_run'] else 'cycle_in_progress')
        elif run and run['state']!='succeeded':
            issues.append('last_cycle_'+run['state'])
        return {'schema':'monitor-alert-health-status/1','state':s,'as_of':now,'issues':issues,
                'attention_required':bool(issues),'human_delivery_verified':False,'clinical_safety_established':False}


def run_cycle(path,binding,operation):
    """No auto-takeover. A killed process leaves a durable running marker."""
    run_id=start(path,binding)
    try:
        result=operation()
    except Exception:
        finish(path,run_id,'failed')
        raise
    finish(path,run_id,'succeeded')
    return result


def configure(parser):
    sub=parser.add_subparsers(dest='health_action',required=True)
    p=sub.add_parser('initialize');p.add_argument('health');p.add_argument('binding')
    p.add_argument('--max-gap',type=int,required=True);p.add_argument('--max-run',type=int,required=True)
    p=sub.add_parser('status');p.add_argument('health')
    p=sub.add_parser('recover');p.add_argument('health');p.add_argument('run_id');p.add_argument('--confirm-process-stopped',action='store_true')
    p=sub.add_parser('cycle');p.add_argument('health');p.add_argument('store');p.add_argument('--approved-digest',required=True)
    p.add_argument('--source-secret-file',required=True);p.add_argument('--sink-secret-file',required=True)
    parser.set_defaults(func=cli)


def cli(args):
    try:
        if args.health_action=='initialize':
            result=initialize(args.health,args.binding,args.max_gap,args.max_run)
        elif args.health_action=='status':
            result=status(args.health)
        elif args.health_action=='recover':
            result=finish(args.health,args.run_id,'interrupted',process_stopped=args.confirm_process_stopped)
        else:
            from . import monitor_alert_worker as worker
            result=run_cycle(args.health,args.approved_digest,lambda:worker.cycle(
                args.store,args.approved_digest,args.source_secret_file,args.sink_secret_file))
        print(relay.canonical(result))
        if args.health_action=='status' and result['attention_required']:
            raise SystemExit(1)
    except Exception:
        raise SystemExit('Monitoring health operation failed; inspect heartbeat and pending outbox.')
