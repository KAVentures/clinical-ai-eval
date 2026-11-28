import json
import os
from types import SimpleNamespace

import pytest

from caeval import hospital_launcher as h


@pytest.fixture
def root(tmp_path):
    p=tmp_path/'private'; p.mkdir(mode=0o700)
    return p


def test_local_initialization_is_idempotent(root):
    assert set(h.init_local(root))==set(h.LOCAL)
    assert h.init_local(root)==h.check(root,'local')
    assert not (root/'institution').exists()
    status=h.diagnose(root)
    assert status['local_layout_ready'] and not status['institution_layout_ready']
    assert not status['institution_accounts_verified']


def test_existing_evidence_not_modified(root):
    h.init_local(root)
    evidence=root/'workspaces'/'sentinel'
    evidence.write_bytes(b'original')
    h.init_local(root)
    assert evidence.read_bytes()==b'original'


def test_symlink_refused_before_creation(root,tmp_path):
    outside=tmp_path/'outside'; outside.mkdir(mode=0o700)
    try: (root/'comparisons').symlink_to(outside,target_is_directory=True)
    except (OSError,NotImplementedError): pytest.skip('symlink not available')
    with pytest.raises(ValueError): h.init_local(root)
    assert not (root/'workspaces').exists()
    assert h.diagnose(root)['directories']['comparisons']=='unsafe_or_unavailable'
    assert not h.diagnose(root)['local_layout_ready']


@pytest.mark.skipif(os.name!='posix',reason='POSIX mode check')
def test_insecure_directory_rejected(root):
    (root/'staging').mkdir(mode=0o755)
    (root/'staging').chmod(0o755)
    with pytest.raises(ValueError): h.init_local(root)
    assert not (root/'workspaces').exists()


def test_local_dispatch(root,monkeypatch):
    paths=h.init_local(root)
    seen=[]
    def module(name,package):
        assert name=='.assessment_multivendor_web'
        return SimpleNamespace(main=lambda args:seen.append(args) or 7)
    monkeypatch.setattr(h.importlib,'import_module',module)
    assert h.launch_local(root)==7
    assert seen==[['--root',paths['workspaces'],'--staging-root',paths['staging'],
                   '--comparison-root',paths['comparisons']]]


def test_institution_requires_existing_directory_and_no_local_init(root,monkeypatch):
    seen=[]
    def module(name,package):
        assert name=='.institution_web'
        return SimpleNamespace(serve=lambda *args:seen.append(args))
    monkeypatch.setattr(h.importlib,'import_module',module)
    with pytest.raises(ValueError): h.launch_institution(root,'https://hospital.example',8767)
    (root/'institution').mkdir(mode=0o700)
    assert h.launch_institution(root,'https://hospital.example',8767)==0
    assert seen==[(str(root/'institution'),'https://hospital.example',8767)]
    assert not (root/'workspaces').exists()


@pytest.mark.parametrize('bad',['http://hospital.example','https://hospital.example/',
    'https://hospital.example/a','https://user@hospital.example','https://hospital.example?x=1',
    'https://hospital.example#','https://hospital.example:99999','https://',
    'https://hospital.example\\other','https://hospital.example\n'])
def test_bad_origins(bad):
    with pytest.raises(ValueError): h.https_origin(bad)


def test_cli_diagnosis(root,capsys):
    assert h.main(['doctor','--root',str(root)])==0
    assert not json.loads(capsys.readouterr().out)['local_layout_ready']
    assert h.main(['init-local','--root',str(root)])==0
    assert json.loads(capsys.readouterr().out)['local_layout_ready']
    assert h.main(['institution','--root',str(root),'--origin','https://hospital.example'])==2
