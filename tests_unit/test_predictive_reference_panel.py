import copy
import json
from itertools import product

import pytest
from caeval import predictive_reference_panel as w, predictive_research as p
from test_predictive_research import spec, inputs, digests


def scope():
    plan=p.freeze(spec())
    s=dict(coordinator='coord',reader_a='doctor-a',reader_b='doctor-b',adjudicator='doctor-c',source_inventory={u:'d'*64 for u in plan['spec']['unit_ids']})
    panel=w.freeze(plan,plan['plan_sha256'],s)
    return plan,plan['plan_sha256'],panel,panel['panel_sha256']


def packet_rows(packet, labels):
    return [{**r,'label':labels[r['unit_id']]} for r in packet['items'] if r['unit_id'] in labels]


def submitted(packet,labels):
    return w.seal(packet,packet['packet_sha256'],packet['reader'],packet_rows(packet,labels))


def primary(args, a,b):
    ss={r:submitted(w.primary_packet(*args,r),v) for r,v in zip(w.PRIMARY,(a,b))}
    return ss,{r:s['submission_sha256'] for r,s in ss.items()}


def test_independent_packets_and_handoff():
    args=scope(); a,b=w.PRIMARY
    packets=[w.primary_packet(*args,r) for r in (a,b)]
    assert packets[0]['packet_sha256']!=packets[1]['packet_sha256']
    assert 'v1' not in json.dumps(packets) and 'v2' not in json.dumps(packets) and 'doctor-b' not in json.dumps(packets[0])
    ss,ds=primary(args,{'a':True,'b':True,'c':False},{'a':True,'b':False})
    third=w.adjudication_packet(*args,ss,ds); assert [r['unit_id'] for r in third['items']]==['b']
    adjud=submitted(third,{'b':False})
    package=w.finalize(*args,ss,ds,adjud,adjud['submission_sha256']); receipt=package['receipt']
    assert receipt['resolution']==dict(consensus=1,adjudicated=1,disagreement_unresolved=0,primary_incomplete=1)
    assert receipt['primary_agreement']==dict(numerator=1,denominator=2)
    assert receipt['cohen_kappa']==dict(numerator=0,denominator=1)
    assert receipt['rows']==[{'unit_id':'a','label':True},{'unit_id':'b','label':False},{'unit_id':'c','label':None}]
    assert not receipt['decision_grade'] and not receipt['external_blinding_verified']
    bundles=inputs(args[0]); bundles['reference']=package['bundle']
    report=p.analyze(args[0],args[1],bundles,digests(bundles)); assert report['coverage']['reference']['n_unknown']==1
    assert w.verify(*args,ss,ds,package,receipt['receipt_sha256'],adjudication=adjud,expected_adjudication=adjud['submission_sha256'])


def test_all_missing_and_degenerate_kappa():
    args=scope(); ss,ds=primary(args,{},{}); r=w.finalize(*args,ss,ds)['receipt']
    assert r['n_paired_known']==0 and r['cohen_kappa'] is None and r['primary_agreement'] is None
    ss,ds=primary(args,{'a':True},{'a':True}); r=w.finalize(*args,ss,ds)['receipt']
    assert r['cohen_kappa'] is None and r['primary_agreement']==dict(numerator=1,denominator=1)


def test_exhaustive_single_unit_resolution():
    args=scope()
    for x,y,z in product((None,False,True),repeat=3):
        ss,ds=primary(args,{'a':x},{'a':y}); t=w.adjudication_packet(*args,ss,ds)
        conflict=x is not None and y is not None and x!=y
        third=submitted(t,{'a':z} if conflict else {})
        r=w.finalize(*args,ss,ds,third,third['submission_sha256'])['receipt']
        want=None if x is None or y is None else z if conflict else x
        assert r['rows'][0]['label'] is want
        assert r['n_planned']==3


def test_tampering_role_and_adjudication_replay():
    args=scope(); ss,ds=primary(args,{'a':True},{'a':False}); third=w.adjudication_packet(*args,ss,ds)
    adjud=submitted(third,{'a':True}); d=adjud['submission_sha256']
    new,nd=primary(args,{'a':False},{'a':True})
    with pytest.raises(ValueError): w.finalize(*args,new,nd,adjud,d)
    swapped={**ss,'reader_a':ss['reader_b']}; sd={**ds,'reader_a':ds['reader_b']}
    with pytest.raises(ValueError): w.finalize(*args,swapped,sd)
    altered=copy.deepcopy(ss); altered['reader_a']['rows'][0]['label']=False
    with pytest.raises(ValueError): w.finalize(*args,altered,ds)
    with pytest.raises(ValueError): w.finalize(*args,ss,ds,adjud)
    p2=p.freeze({**spec(),'candidate_version':'v3'})
    with pytest.raises(ValueError): w.primary_packet(p2,p2['plan_sha256'],args[2],args[3],'reader_a')


@pytest.mark.parametrize('bad',[{'unit_id':'a','source_sha256':'e'*64,'label':True},{'unit_id':'a','source_sha256':'d'*64,'label':1},{'unit_id':'foreign','source_sha256':'d'*64,'label':False}])
def test_invalid_ratings(bad):
    t=w.primary_packet(*scope(),'reader_a')
    with pytest.raises(ValueError): w.seal(t,t['packet_sha256'],t['reader'],[bad])
    with pytest.raises(ValueError): w.seal(t,t['packet_sha256'],'doctor-b',[])


def test_distinct_actors_and_full_roster():
    plan,d,panel,_=scope(); s={**panel['actors'],'source_inventory':panel['source_inventory']}
    with pytest.raises(ValueError): w.freeze(plan,d,{**s,'reader_b':'doctor-a'})
    with pytest.raises(ValueError): w.freeze(plan,d,{**s,'source_inventory':{'a':'d'*64}})
    t=w.primary_packet(*scope(),'reader_a'); rows=packet_rows(t,{'a':True})
    with pytest.raises(ValueError): w.seal(t,t['packet_sha256'],t['reader'],rows*2)


def test_cli_roundtrip(tmp_path,capsys):
    plan,d,panel,h=scope()
    def put(name,obj):
        f=tmp_path/name; f.write_text(json.dumps(obj)); return str(f)
    common=['--plan',put('plan',plan),'--expected-plan',d]
    panelpath=str(tmp_path/'panel'); specpath=put('spec',{**panel['actors'],'source_inventory':panel['source_inventory']})
    assert w.main(['freeze',*common,'--spec',specpath,'--output',panelpath])==0
    assert capsys.readouterr().out.strip()==h
    common+=['--panel',panelpath,'--expected-panel',h]
    primary_args=[]
    for role,label in zip(w.PRIMARY,(True,False)):
        packetpath=str(tmp_path/(role+'-packet')); sp=str(tmp_path/role)
        assert w.main(['packet',*common,'--role',role,'--output',packetpath])==0
        pd=capsys.readouterr().out.strip(); packet=json.load(open(packetpath))
        assert w.main(['seal','--packet',packetpath,'--expected-packet',pd,'--reader',packet['reader'],'--rows',put(role+'-rows',packet_rows(packet,{'a':label})),'--output',sp])==0
        sd=capsys.readouterr().out.strip(); k=role.replace('_','-'); primary_args.extend(['--'+k,sp,'--expected-'+k,sd])
    tp=str(tmp_path/'third'); assert w.main(['adjudication-packet',*common,*primary_args,'--output',tp])==0
    pd=capsys.readouterr().out.strip(); t=json.load(open(tp)); ad=str(tmp_path/'adjudication')
    assert w.main(['seal','--packet',tp,'--expected-packet',pd,'--reader',t['reader'],'--rows',put('third-rows',packet_rows(t,{'a':True})),'--output',ad])==0
    ad_sha=capsys.readouterr().out.strip(); out=str(tmp_path/'result')
    argv=['finalize',*common,*primary_args,'--adjudication',ad,'--expected-adjudication',ad_sha,'--output',out]
    assert w.main(argv)==0 and w.main(argv)==2
    assert json.load(open(out))['receipt']['resolution']['adjudicated']==1
