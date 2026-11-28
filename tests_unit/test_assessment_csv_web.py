import csv
import json
from urllib.parse import urlencode

import pytest
from caeval import assessment_csv_web as imp
from test_assessment_workspace_web import call, select, child_post, freeze, selector
from test_assessment_setup_web import app
from test_assessment_protocol_web import fields
from test_prospective_research import inputs, outcomes


def write(path, columns, rows):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=columns); w.writeheader(); w.writerows(rows)
    path.chmod(0o600)


@pytest.fixture
def manager(selector):
    m=imp.Manager(selector.root,selector.staging_root); m.host=selector.host
    for p in (m.staging_root/'first').glob('*.json'): p.unlink()
    assert select(m)[0]=='303 See Other'
    _,manifest,groups=inputs()
    write(m.active.staging/'manifest.csv',imp.MANIFEST,manifest)
    write(m.active.staging/'memberships.csv',imp.COHORT['memberships'],[{'unit_id':u,'group':g} for u,g in groups.items()])
    return m


def action(m,kind,action='preview',approval='',**changes):
    raw=urlencode(dict(csrf=m.active.csrf,action=action,approval=approval)).encode()
    return call(m,'/csv-'+kind,'POST',raw,**changes)


def commit(m,kind):
    assert action(m,kind)[0]=='200 OK'
    assert action(m,kind,'commit',m.active.csv_pending[kind][0])[0]=='303 See Other'


def provision(m):
    commit(m,'cohort')
    raw=urlencode({'csrf':m.active.csrf,**fields()}).encode()
    assert call(m,'/protocol','POST',raw)[0]=='303 See Other'
    freeze(m)


def collection(m):
    values={'collection':[dict(as_of='2026-01-03',baseline_sha256='c'*64,candidate_sha256='c'*64,reference_sha256='c'*64)],
            'baseline-rows':[dict(unit_id='u2',probability_bp=1000)],'candidate-rows':[dict(unit_id='u2',probability_bp=2000)],
            'reference-rows':[dict(unit_id='u2',label='true')],
            'outcomes':[{k: ('true' if v is True else 'false' if v is False else v) for k,v in row.items()} for row in outcomes()],
            'reference-counts':[dict(feature='age',level='a',count=2),dict(feature='age',level='b',count=0)],
            'target-counts':[dict(feature='age',level='a',count=0),dict(feature='age',level='b',count=2)]}
    for k,v in values.items(): write(m.active.staging/(k+'.csv'),imp.COLLECTION[k],v)


def test_end_to_end(manager):
    m=manager; assert b'Import cohort CSV' in call(m,'/workspaces')[2]
    assert action(m,'collection')[0]=='409 Conflict'
    provision(m); collection(m)
    assert action(m,'cohort')[0]=='409 Conflict'
    commit(m,'collection')
    assert child_post(m,'/preview-collection')[0]=='200 OK'
    report=m.active.collection[1]
    assert child_post(m,'/commit-collection',report)[0]=='200 OK'
    for path in ('/run','/verify','/download'):
        result=child_post(m,path); assert result[0]=='200 OK'
    r=json.loads(result[2]); assert r['report_sha256']==report
    assert r['summary']['predictive_coverage']['reference']['n_unknown']==2


@pytest.mark.parametrize('raw',[b'wrong,header\na,b\n',b'unit_id,group\na,b,c\n',b'unit_id,group\na,"unterminated\n',b'unit_id,group\na, b\n',b'unit_id,group\na,\x00\n'])
def test_bad_csv(raw):
    with pytest.raises((ValueError,csv.Error)): imp.parse(raw,imp.COHORT['memberships'])


def test_preview_stale_and_exclusive(manager):
    m=manager; assert action(m,'cohort')[0]=='200 OK'; d=m.active.csv_pending['cohort'][0]
    p=m.active.staging/'memberships.csv'; p.write_bytes(p.read_bytes().replace(b'g1',b'g2'))
    assert action(m,'cohort','commit',d)[0]=='409 Conflict'
    assert not (m.active.staging/'manifest.json').exists()
    commit(m,'cohort'); assert action(m,'cohort')[0]=='409 Conflict'


def test_collection_duplicate_and_invalid_values(manager):
    m=manager; provision(m); collection(m)
    p=m.active.staging/'baseline-rows.csv'
    write(p,imp.COLLECTION['baseline-rows'],[dict(unit_id='u2',probability_bp=1000)]*2)
    assert action(m,'collection')[0]=='409 Conflict'
    write(p,imp.COLLECTION['baseline-rows'],[dict(unit_id='u2',probability_bp=10001)])
    assert action(m,'collection')[0]=='409 Conflict'
    assert not (m.active.staging/'collection.json').exists()


def test_auth_and_stale_tabs(manager):
    m=manager; assert action(m,'cohort',HTTP_AUTHORIZATION='')[0]=='401 Unauthorized'
    assert action(m,'cohort',HTTP_ORIGIN='https://evil.test')[0]=='403 Forbidden'
    assert action(m,'cohort')[0]=='200 OK'
    raw=urlencode(dict(csrf=m.active.csrf,action='commit',approval=m.active.csv_pending['cohort'][0])).encode()
    assert select(m,'second')[0]=='303 See Other'
    assert call(m,'/csv-cohort','POST',raw)[0]=='403 Forbidden'


def test_null_and_count_contract():
    assert imp.boolean('') is None and imp.boolean('false') is False
    with pytest.raises(ValueError): imp.boolean('0')
    with pytest.raises(ValueError): imp.counts([dict(feature='a',level='b',count='0')]*2)


def test_real_http(manager):
    from threading import Thread
    from http.client import HTTPConnection
    from wsgiref.simple_server import make_server
    from caeval.assessment_workbench_web import QuietHandler
    m=manager
    with make_server('127.0.0.1',0,m,handler_class=QuietHandler) as server:
        m.host=m.active.host='127.0.0.1:'+str(server.server_port)
        worker=Thread(target=server.serve_forever,daemon=True); worker.start()
        conn=HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        headers={'Authorization':m.auth,'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+m.host}
        try:
            for act,status in [('preview',200),('commit',303)]:
                d=m.active.csv_pending['cohort'][0] if act=='commit' else ''
                conn.request('POST','/csv-cohort',urlencode(dict(csrf=m.active.csrf,action=act,approval=d)),headers)
                r=conn.getresponse(); assert r.status==status; r.read()
        finally:
            conn.close(); server.shutdown(); worker.join(timeout=5)
