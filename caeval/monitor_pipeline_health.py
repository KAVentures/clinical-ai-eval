"""Read-only operational reconciliation for one approved local alert pipeline."""
from datetime import datetime, timezone, date
import sqlite3
import time
from . import monitor_alert_relay as relay, monitor_relay_bridge as bridge

ERRORS=(ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error, RecursionError)


def relay_snapshot(path, scope, wanted):
    """Bounded metadata inventory; replay the selected receipt payloads only."""
    with bridge.read_db(path, scope['monitor_id'], scope['plan_digest']) as (conn, binding):
        rows=conn.execute('SELECT id,sequence,accepted_at,reviewed_at,review_digest FROM batches ORDER BY sequence,id LIMIT ?',
                          (relay.MAX_BATCHES+1,)).fetchall()
        relay.require(len(rows)<=relay.MAX_BATCHES,'relay inventory exceeds limit')
        ids=set(); sequences=set(); duplicate=False; oldest=None; pending=0; maximum=0
        for row in rows:
            ident,seq,accepted,reviewed,rd=(row[k] for k in ('id','sequence','accepted_at','reviewed_at','review_digest'))
            relay.require(relay.hex64(ident) and ident not in ids and type(seq) is int and 1<=seq<=2**53-1
                          and type(accepted) is int and accepted>=0 and
                          ((reviewed is None and rd is None) or (type(reviewed) is int and reviewed>=accepted and relay.hex64(rd))),
                          'invalid relay metadata')
            ids.add(ident); duplicate=duplicate or seq in sequences; sequences.add(seq); maximum=max(maximum,seq)
            if reviewed is None:
                pending+=1; oldest=accepted if oldest is None else min(oldest,accepted)
        selected={}
        for ident in sorted(set(wanted)&ids):
            row=conn.execute('SELECT * FROM batches WHERE id=?',(ident,)).fetchone()
            selected[ident]=relay._row(row,binding)
        return {'count':len(rows),'local_cli_unreviewed':pending,'oldest_local_cli_unreviewed_at':oldest,
                'max_sequence':maximum,'duplicate_sequences':duplicate,'selected':selected,'sequences':sorted(sequences),
                'full_payload_inventory_verified':False}


def assess(binding, state, heartbeat, received, *, now, backlog_limit, backlog_age):
    relay.require(type(now) is int and now>=0 and type(backlog_limit) is int and 1<=backlog_limit<=relay.MAX_BATCHES
                  and type(backlog_age) is int and 60<=backlog_age<=31*86400,'invalid health policy')
    issues=[]
    def issue(code):
        if code not in issues: issues.append(code)
    if state is None or binding is None:
        issue('outbox_or_binding_unavailable')
    if heartbeat is None:
        issue('heartbeat_unavailable')
    else:
        for code in heartbeat['issues']: issue('heartbeat_'+code)
        if binding and heartbeat['state']['policy']['binding']!=binding['digest']:
            issue('heartbeat_binding_mismatch')
    if received is None:
        issue('relay_unavailable')
    checkpoint_day=None; pending_state=None; sequence=None; last_receipt_state=None
    if state is not None:
        sequence=state['sequence']; pending=state['pending']; ack=state['last_ack']
        if state['checkpoint'] is None:
            issue('checkpoint_not_initialized')
        else:
            checkpoint_day=state['checkpoint']['feed']['as_of']
            today=datetime.fromtimestamp(now,timezone.utc).date()
            day=date.fromisoformat(checkpoint_day)
            if day<today: issue('checkpoint_date_older_than_utc_today')
            if day>today: issue('checkpoint_date_in_future')
        if pending is not None:
            issue('pending_outbox_attempt')
            pending_state='unknown' if received is None else 'not_accepted'
            if received is not None and pending['digest'] in received['selected']:
                doc=received['selected'][pending['digest']]
                batch=pending['batch']; feed=batch['next_checkpoint']['feed']
                expected={'schema':'monitor-alert-delivery/1','attempt_digest':pending['digest'],
                          'monitor_id':feed['monitor_id'],'plan_digest':feed['plan_digest'],'as_of':feed['as_of'],
                          'sequence':pending['sequence'],'events':batch['events']}
                pending_state='accepted' if doc==expected and batch['delivery_required'] else 'conflict'
                if pending_state=='conflict': issue('pending_relay_payload_conflict')
        if received is not None:
            if received['max_sequence']>sequence+(pending is not None): issue('relay_sequence_ahead_of_outbox')
            if pending is not None and received['max_sequence']==sequence+1 and pending_state!='accepted':
                issue('relay_next_sequence_not_exact_pending')
            if received['duplicate_sequences']: issue('relay_duplicate_sequences')
            if ack is not None:
                doc=received['selected'].get(ack['attempt_digest'])
                if ack['delivery_attested']:
                    last_receipt_state='matching_metadata' if doc and doc['sequence']==sequence and doc['as_of']==checkpoint_day else 'missing_or_mismatched'
                    if last_receipt_state!='matching_metadata': issue('last_ack_relay_missing_or_mismatched')
                else:
                    last_receipt_state='not_required'
                    if doc is not None or sequence in received['sequences']: issue('event_free_ack_has_relay_record')
    if received is not None:
        if received['count']>=relay.MAX_BATCHES: issue('relay_capacity_exhausted')
        if received['local_cli_unreviewed']>=backlog_limit: issue('local_cli_backlog_limit')
        oldest=received['oldest_local_cli_unreviewed_at']
        if oldest is not None:
            if oldest>now: issue('relay_acceptance_time_in_future')
            elif now-oldest>=backlog_age: issue('local_cli_backlog_overdue')
    return {'schema':'monitor-pipeline-health/1','as_of':now,'issues':issues,'attention_required':bool(issues),
            'scope':state['scope'] if state else None,'sequence':sequence,'checkpoint_date':checkpoint_day,
            'pending_state':pending_state,'last_receipt_state':last_receipt_state,
            'relay':{k:v for k,v in received.items() if k not in ('selected','sequences')} if received else None,
            'live_source_polled':False,'institutional_review_backlog_checked':False,
            'atomic_cross_store_snapshot':False,'human_delivery_verified':False,'clinical_safety_established':False}


def check(outbox_path, heartbeat_path, relay_path, approval, *, backlog_limit, backlog_age):
    from . import monitor_alert_worker as worker, monitor_alert_health as health
    binding=state=heartbeat=received=None
    try: binding,state=worker._read_binding(outbox_path,approval)
    except ERRORS: pass
    try: heartbeat=health.status(heartbeat_path)
    except ERRORS: pass
    if state is not None:
        wanted=[x['attempt_digest'] for x in [state['last_ack']] if x]
        if state['pending']: wanted.append(state['pending']['digest'])
        try: received=relay_snapshot(relay_path,state['scope'],wanted)
        except ERRORS: pass
    return assess(binding,state,heartbeat,received,now=int(time.time()),backlog_limit=backlog_limit,backlog_age=backlog_age)


def configure(parser):
    parser.add_argument('outbox');parser.add_argument('heartbeat');parser.add_argument('relay')
    parser.add_argument('--approved-digest',required=True)
    parser.add_argument('--backlog-limit',required=True,type=int)
    parser.add_argument('--backlog-age',required=True,type=int,help='local CLI review backlog age in seconds')
    parser.set_defaults(func=cli)


def cli(args):
    try:
        result=check(args.outbox,args.heartbeat,args.relay,args.approved_digest,
                     backlog_limit=args.backlog_limit,backlog_age=args.backlog_age)
        print(relay.canonical(result))
    except ERRORS:
        raise SystemExit('Monitoring pipeline health check refused.')
    if result['attention_required']: raise SystemExit(1)
