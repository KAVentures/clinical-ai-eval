"""Prospective two-reader binary reference labels for a frozen predictive study."""
import argparse
from fractions import Fraction
import sys

from . import predictive_research as pred, risk_probability as risk

SCHEMA = 'predictive-reference-panel/1'
PRIMARY = ('reader_a', 'reader_b')
ROLES = (*PRIMARY, 'adjudicator')
MAX_UNITS = 10000


def require(ok):
    if not ok:
        raise ValueError('reference panel scope, packet or checkpoint mismatch')


def freeze(plan, expected_plan, spec):
    study = pred._plan(plan, expected_plan)
    pred.fields(spec, 'coordinator reader_a reader_b adjudicator source_inventory')
    actors = {k:spec[k] for k in ('coordinator', *ROLES)}
    require(all(risk._token(x) for x in actors.values()) and len(set(actors.values())) == 4)
    inventory = spec['source_inventory']
    require(isinstance(inventory, dict) and len(inventory) <= MAX_UNITS and set(inventory) == set(study['unit_ids']))
    require(all(pred.hex64(x) for x in inventory.values()))
    body = {'schema':SCHEMA,'plan_sha256':expected_plan,'actors':actors,'source_inventory':dict(inventory),
            'reference_protocol_sha256':study['reference_protocol_sha256'],'endpoint':study['endpoint'],'horizon':study['horizon']}
    return {**body,'panel_sha256':pred.digest(body)}


def _panel(plan, expected_plan, panel, expected_panel):
    require(pred.hex64(expected_panel) and panel['panel_sha256'] == expected_panel)
    rebuilt = freeze(plan, expected_plan, {**panel['actors'],'source_inventory':panel['source_inventory']})
    require(pred.canonical(rebuilt) == pred.canonical(panel))
    return rebuilt


def _packet(panel, role, units, primary_digests=None):
    body = {'schema':SCHEMA,'panel_sha256':panel['panel_sha256'],'role':role,'reader':panel['actors'][role],
            'reference_protocol_sha256':panel['reference_protocol_sha256'],'endpoint':panel['endpoint'],'horizon':panel['horizon'],
            'items':[{'unit_id':u,'source_sha256':panel['source_inventory'][u]} for u in sorted(units)]}
    if primary_digests is not None:
        body['primary_digests'] = primary_digests
    return {**body,'packet_sha256':pred.digest(body)}


def primary_packet(plan, expected_plan, panel, expected_panel, role):
    panel = _panel(plan, expected_plan, panel, expected_panel)
    require(role in PRIMARY)
    return _packet(panel, role, panel['source_inventory'])


def seal(packet, expected_packet, reader, rows):
    """Produce a declared-reader submission; caller must use an authentic packet."""
    require(pred.hex64(expected_packet) and packet['packet_sha256'] == expected_packet)
    require(pred.digest({k:v for k,v in packet.items() if k!='packet_sha256'}) == expected_packet)
    require(packet['schema']==SCHEMA and packet['role'] in ROLES and reader==packet['reader'])
    expected = {r['unit_id']:r['source_sha256'] for r in packet['items']}
    require(isinstance(rows,list) and len(rows)<=len(expected))
    indexed = {}
    for r in rows:
        pred.fields(r,'unit_id source_sha256 label')
        u = r['unit_id']
        require(risk._token(u) and u in expected and u not in indexed and r['source_sha256']==expected[u])
        require(r['label'] is None or type(r['label']) is bool)
        indexed[u] = dict(r)
    body = {'schema':SCHEMA,'packet_sha256':expected_packet,'reader':reader,'rows':[indexed[u] for u in sorted(indexed)]}
    return {**body,'submission_sha256':pred.digest(body)}


def _submission(packet, submission, expected):
    require(pred.hex64(expected) and submission['submission_sha256']==expected)
    rebuilt = seal(packet,packet['packet_sha256'],submission['reader'],submission['rows'])
    require(pred.canonical(rebuilt)==pred.canonical(submission))
    return {r['unit_id']:r['label'] for r in rebuilt['rows']}


def _primary(panel, submissions, expected):
    pred.fields(submissions,'reader_a reader_b'); pred.fields(expected,'reader_a reader_b')
    return {role:_submission(_packet(panel,role,panel['source_inventory']),submissions[role],expected[role]) for role in PRIMARY}


def _conflicts(panel, ratings):
    a,b = (ratings[r] for r in PRIMARY)
    return [u for u in sorted(panel['source_inventory']) if a.get(u) is not None and b.get(u) is not None and a[u]!=b[u]]


def adjudication_packet(plan, expected_plan, panel, expected_panel, submissions, expected):
    panel = _panel(plan,expected_plan,panel,expected_panel)
    ratings = _primary(panel,submissions,expected)
    return _packet(panel,'adjudicator',_conflicts(panel,ratings),dict(expected))


def _ratio(x):
    return {'numerator':x.numerator,'denominator':x.denominator}


def finalize(plan, expected_plan, panel, expected_panel, submissions, expected, adjudication=None, expected_adjudication=None):
    panel = _panel(plan,expected_plan,panel,expected_panel)
    ratings = _primary(panel,submissions,expected)
    conflicts = _conflicts(panel,ratings)
    require((adjudication is None)==(expected_adjudication is None))
    third = {} if adjudication is None else _submission(_packet(panel,'adjudicator',conflicts,dict(expected)),adjudication,expected_adjudication)
    a,b = (ratings[r] for r in PRIMARY)
    rows, counts = [], {k:0 for k in ('consensus','adjudicated','disagreement_unresolved','primary_incomplete')}
    pairs = {k:0 for k in ('00','01','10','11')}
    for u in sorted(panel['source_inventory']):
        x,y = a.get(u),b.get(u)
        if x is None or y is None:
            label,state = None,'primary_incomplete'
        else:
            pairs[str(int(x))+str(int(y))] += 1
            if x==y:
                label,state = x,'consensus'
            else:
                label = third.get(u)
                state = 'adjudicated' if label is not None else 'disagreement_unresolved'
        counts[state] += 1
        rows.append({'unit_id':u,'label':label})
    n = sum(pairs.values())
    agreement = Fraction(pairs['00']+pairs['11'],n) if n else None
    chance = Fraction((pairs['00']+pairs['01'])*(pairs['00']+pairs['10'])+(pairs['10']+pairs['11'])*(pairs['01']+pairs['11']),n*n) if n else None
    kappa = (agreement-chance)/(1-chance) if chance is not None and chance!=1 else None
    body = {'schema':SCHEMA,'panel_sha256':expected_panel,'primary_digests':dict(expected),'adjudication_sha256':expected_adjudication,
            'n_planned':len(rows),'n_paired_known':n,'primary_pairs':pairs,'resolution':counts,
            'primary_agreement':_ratio(agreement) if agreement is not None else None,'cohen_kappa':_ratio(kappa) if kappa is not None else None,
            'rows':rows,'independent_identity_verified':False,'external_blinding_verified':False,'source_records_reverified':False,
            'decision_grade':False,'publication_safe':False}
    receipt = {**body,'receipt_sha256':pred.digest(body)}
    bundle = pred.seal(plan,expected_plan,'reference',receipt['receipt_sha256'],rows)
    return {'receipt':receipt,'bundle':bundle}


def verify(plan, expected_plan, panel, expected_panel, submissions, expected, package, expected_receipt, *, adjudication=None, expected_adjudication=None):
    require(pred.hex64(expected_receipt))
    rebuilt = finalize(plan,expected_plan,panel,expected_panel,submissions,expected,adjudication,expected_adjudication)
    return rebuilt['receipt']['receipt_sha256']==expected_receipt and pred.canonical(package)==pred.canonical(rebuilt)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('freeze','packet','seal','adjudication-packet','finalize'):
        cmd=sub.add_parser(name); cmd.add_argument('--output',required=True)
        if name=='seal':
            for k in ('packet','expected-packet','reader','rows'): cmd.add_argument('--'+k,required=True)
        else:
            for k in ('plan','expected-plan'): cmd.add_argument('--'+k,required=True)
            if name=='freeze': cmd.add_argument('--spec',required=True)
            else:
                for k in ('panel','expected-panel'): cmd.add_argument('--'+k,required=True)
                if name=='packet': cmd.add_argument('--role',choices=PRIMARY,required=True)
                else:
                    for role in ('reader-a','reader-b'):
                        cmd.add_argument('--'+role,required=True); cmd.add_argument('--expected-'+role,required=True)
                    if name=='finalize':
                        cmd.add_argument('--adjudication'); cmd.add_argument('--expected-adjudication')
    a=parser.parse_args(argv)
    try:
        if a.command=='seal':
            result=seal(pred._read(a.packet),a.expected_packet,a.reader,pred._read(a.rows)); checkpoint=result['submission_sha256']
        else:
            args=(pred._read(a.plan),a.expected_plan)
            if a.command=='freeze': result=freeze(*args,pred._read(a.spec)); checkpoint=result['panel_sha256']
            else:
                args=(*args,pred._read(a.panel),a.expected_panel)
                if a.command=='packet': result=primary_packet(*args,a.role); checkpoint=result['packet_sha256']
                else:
                    args=(*args,{'reader_a':pred._read(a.reader_a),'reader_b':pred._read(a.reader_b)}, {'reader_a':a.expected_reader_a,'reader_b':a.expected_reader_b})
                    if a.command=='adjudication-packet': result=adjudication_packet(*args); checkpoint=result['packet_sha256']
                    else:
                        result=finalize(*args,pred._read(a.adjudication) if a.adjudication else None,a.expected_adjudication); checkpoint=result['receipt']['receipt_sha256']
        pred._write(a.output,result); print(checkpoint); return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Reference panel operation refused. Check scope, inputs and retained checkpoints.',file=sys.stderr); return 2


if __name__=='__main__':
    sys.exit(main())
