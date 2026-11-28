"""Explicitly approved loopback execution of a frozen predictive study arm."""
import argparse
import http.client
import json
import math
import os
import sys
import time

from . import predictive_research as pred, risk_probability as risk

SCHEMA = 'predictive-local-execution/1'
MAX_CALLS = 1500
MAX_RESPONSE = 64 * 1024
MAX_FEATURES = 128
STATUSES = ('ok', 'transport_error', 'http_error', 'invalid_response', 'not_attempted_budget')


def require(ok):
    if not ok:
        raise ValueError('invalid predictive execution scope or checkpoint')


def _numeric(v):
    return v is None or type(v) is bool or (type(v) in (int, float) and abs(v) <= 1e15 and math.isfinite(v))


def _inventory(spec, names, rows):
    require(isinstance(names, list) and 1 <= len(names) <= MAX_FEATURES and
            all(risk._token(n) for n in names) and len(set(names)) == len(names))
    require(isinstance(rows, list) and len(rows) == len(spec['unit_ids']))
    indexed = {}
    for r in rows:
        pred.fields(r, 'unit_id features')
        u, f = r['unit_id'], r['features']
        require(risk._token(u) and u not in indexed and isinstance(f, dict) and set(f) == set(names))
        require(all(_numeric(v) for v in f.values()))
        indexed[u] = r
    require(set(indexed) == set(spec['unit_ids']))
    return [indexed[u] for u in spec['unit_ids']]


def preview(plan, expected_plan, options, rows):
    spec = pred._plan(plan, expected_plan)
    pred.fields(options, 'role port feature_names feature_protocol_sha256 max_calls timeout_seconds max_seconds')
    role = options['role']
    require(role in ('baseline', 'candidate') and pred.hex64(options['feature_protocol_sha256']))
    for k, upper in (('port',65535), ('max_calls',min(MAX_CALLS,len(spec['unit_ids']))),
                     ('timeout_seconds',30), ('max_seconds',900)):
        require(type(options[k]) is int and 1 <= options[k] <= upper)
    inventory = _inventory(spec, options['feature_names'], rows)
    body = {'schema': SCHEMA, 'plan_sha256': expected_plan,
            'options': {**options, 'feature_names': sorted(options['feature_names'])},
            'version': spec[role+'_version'], 'endpoint': spec['endpoint'], 'horizon': spec['horizon'],
            'input_sha256': pred.digest(inventory), 'n_planned': len(inventory),
            'transport': 'literal_ipv4_loopback_http_predict'}
    return {**body, 'execution_sha256': pred.digest(body)}


def _check(plan, expected_plan, execution, expected_execution, rows):
    require(pred.hex64(expected_execution) and execution['execution_sha256'] == expected_execution)
    rebuilt = preview(plan, expected_plan, execution['options'], rows)
    require(pred.canonical(rebuilt) == pred.canonical(execution))
    return _inventory(plan['spec'], execution['options']['feature_names'], rows)


def _call(execution, row):
    options = execution['options']
    payload = {'schema': SCHEMA, 'unit_id': row['unit_id'], 'model_version': execution['version'],
               'endpoint': execution['endpoint'], 'horizon': execution['horizon'], 'features': row['features']}
    request_sha = pred.digest(payload)
    body = pred.canonical({**payload, 'request_sha256': request_sha})
    conn = http.client.HTTPConnection('127.0.0.1', options['port'], timeout=options['timeout_seconds'])
    try:
        conn.request('POST','/predict',body=body,headers={'Content-Type':'application/json','Accept':'application/json','Connection':'close'})
        res = conn.getresponse()
        if res.status != 200:
            return 'http_error', None
        if res.getheader('Content-Type','').split(';',1)[0].strip().lower() != 'application/json':
            return 'invalid_response', None
        raw = res.read(MAX_RESPONSE+1)
    except (OSError, http.client.HTTPException):
        return 'transport_error', None
    finally:
        conn.close()
    try:
        require(len(raw) <= MAX_RESPONSE)
        answer = json.loads(raw, object_pairs_hook=pred._unique)
        pred.fields(answer, 'request_sha256 unit_id model_version probability_bp')
        require(answer['request_sha256'] == request_sha and answer['unit_id'] == row['unit_id'] and
                answer['model_version'] == execution['version'] and risk._bp(answer['probability_bp']))
        return 'ok', answer['probability_bp']
    except (ValueError,TypeError,RecursionError):
        return 'invalid_response', None


def execute(plan, expected_plan, execution, expected_execution, rows, *, confirmed=False):
    require(confirmed is True)
    inventory = _check(plan,expected_plan,execution,expected_execution,rows)
    options = execution['options']
    end = time.monotonic() + options['max_seconds']
    results = []
    for i, row in enumerate(inventory):
        status, value = ('not_attempted_budget',None) if i >= options['max_calls'] or time.monotonic() >= end else _call(execution,row)
        results.append({'unit_id':row['unit_id'],'status':status,'probability_bp':value})
    body = {'schema':SCHEMA,'execution_sha256':expected_execution,'results':results,
            'counts':{s:sum(r['status']==s for r in results) for s in STATUSES},
            'independent_model_identity_verified':False,'decision_grade':False,'publication_safe':False}
    return {**body,'receipt_sha256':pred.digest(body)}


def seal(plan, expected_plan, execution, expected_execution, rows, receipt, expected_receipt):
    """Replay receipt structure and create a native prediction bundle without inference."""
    inventory = _check(plan,expected_plan,execution,expected_execution,rows)
    pred.fields(receipt,'schema execution_sha256 results counts independent_model_identity_verified decision_grade publication_safe receipt_sha256')
    require(pred.hex64(expected_receipt) and receipt['receipt_sha256'] == expected_receipt and
            pred.digest({k:v for k,v in receipt.items() if k!='receipt_sha256'}) == expected_receipt)
    require(receipt['schema']==SCHEMA and receipt['execution_sha256']==expected_execution and
            all(receipt[k] is False for k in ('independent_model_identity_verified','decision_grade','publication_safe')))
    results = receipt['results']
    require(isinstance(results,list) and len(results)==len(inventory))
    stopped = False
    for i,(r, row) in enumerate(zip(results,inventory)):
        pred.fields(r,'unit_id status probability_bp')
        require(r['unit_id']==row['unit_id'] and r['status'] in STATUSES)
        if r['status']=='not_attempted_budget': stopped=True
        else: require(not stopped and i < execution['options']['max_calls'])
        require(risk._bp(r['probability_bp']) if r['status']=='ok' else r['probability_bp'] is None)
    require(pred.canonical(receipt['counts'])==pred.canonical({s:sum(r['status']==s for r in results) for s in STATUSES}))
    return pred.seal(plan,expected_plan,execution['options']['role'],expected_receipt,
                     [{'unit_id':r['unit_id'],'probability_bp':r['probability_bp']} for r in results])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('preview','run','seal'):
        cmd=sub.add_parser(name)
        for k in ('plan','expected-plan','rows','output'): cmd.add_argument('--'+k,required=True)
        if name=='preview': cmd.add_argument('--options',required=True)
        else:
            for k in ('execution','expected-execution'): cmd.add_argument('--'+k,required=True)
        if name=='run': cmd.add_argument('--confirm-loopback',action='store_true')
        if name=='seal':
            for k in ('receipt','expected-receipt'): cmd.add_argument('--'+k,required=True)
    a=parser.parse_args(argv)
    try:
        require(not os.path.lexists(a.output))
        args=(pred._read(a.plan),a.expected_plan)
        rows=pred._read(a.rows)
        if a.command=='preview': result=preview(*args,pred._read(a.options),rows)
        else:
            args=(*args,pred._read(a.execution),a.expected_execution,rows)
            if a.command=='run': result=execute(*args,confirmed=a.confirm_loopback)
            else: result=seal(*args,pred._read(a.receipt),a.expected_receipt)
        pred._write(a.output,result)
        print(result[{'preview':'execution_sha256','run':'receipt_sha256','seal':'bundle_sha256'}[a.command]])
        return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Local execution refused. Check approved scope, inputs and checkpoints.',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
