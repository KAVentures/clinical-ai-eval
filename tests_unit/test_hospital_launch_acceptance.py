import json
from pathlib import Path
import shutil
import sys
import venv

import pytest
from caeval import hospital_launch_acceptance as a


# The actual first-run and launcher modules run in a fresh venv. Only the
# unavailable full workbench is represented by a minimal loopback test double.
STUB = '''import argparse, base64, secrets
from wsgiref.simple_server import make_server

def main(argv):
    p=argparse.ArgumentParser()
    for key in ('root','staging-root','comparison-root'): p.add_argument('--'+key,required=True)
    p.parse_args(argv)
    pw=secrets.token_urlsafe(32)
    auth='Basic '+base64.b64encode(('operator:'+pw).encode()).decode()
    def app(env,start):
        ok=env.get('HTTP_AUTHORIZATION')==auth
        raw=(b'Assessment workspaces <a href="/comparisons">Compare</a>' if env['PATH_INFO']=='/workspaces' else b'Matched product comparison') if ok else b''
        start('200 OK' if ok else '401 Unauthorized',[('Content-Length',str(len(raw))),('Cache-Control','no-store')]); return [raw]
    with make_server('127.0.0.1',0,app) as s:
        print('Open http://127.0.0.1:'+str(s.server_port)+'/workspaces in a local browser. Username: operator',flush=True)
        print('Session password: '+pw,flush=True)
        s.serve_forever()
'''


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    root=tmp_path_factory.mktemp('installed'); env=root/'env'
    venv.EnvBuilder(with_pip=False).create(env)
    python=env/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    import subprocess
    lib=Path(subprocess.check_output([str(python),'-I','-c','import sysconfig; print(sysconfig.get_path("purelib"))'],text=True).strip())
    package=lib/'caeval'; package.mkdir()
    (package/'__init__.py').touch()
    for name in ('hospital_launcher.py','hospital_first_run.py'):
        shutil.copyfile(Path(a.__file__).parent/name,package/name)
    (package/'assessment_multivendor_web.py').write_text(STUB,encoding='utf-8')
    return python,package


def test_real_setup_and_stub_handoff(installed):
    python,_=installed
    result=a.rehearse(python=python,startup_timeout=10)
    assert result['credential_separation']=='passed' and result['comparison_route']=='passed'
    assert not result['institution_started'] and not result['production_approved']


def test_bad_native_handoff(installed):
    python,package=installed; stub=package/'assessment_multivendor_web.py'
    stub.write_text(STUB.replace('Matched product comparison','Unexpected page'),encoding='utf-8')
    try:
        with pytest.raises(ValueError): a.rehearse(python=python,startup_timeout=10)
    finally: stub.write_text(STUB,encoding='utf-8')


def test_hidden_fields():
    raw=('<input name="csrf" type="hidden" value="'+'a'*64+'"><input type="hidden" name="layout" value="'+'b'*64+'">').encode()
    assert a.setup_form(raw)==('csrf='+'a'*64+'&layout='+'b'*64+'&confirm=yes').encode()
    for bad in (b'',raw+raw,raw.replace(b'a'*64,b'bad')):
        with pytest.raises(ValueError): a.setup_form(bad)


def test_timeout_validation():
    for value in (0,121,True):
        with pytest.raises(ValueError): a.rehearse(startup_timeout=value)


def test_cli_safe_failure(monkeypatch,capsys):
    def fail(**kw): raise ValueError('sensitive child output')
    monkeypatch.setattr(a,'rehearse',fail)
    assert a.main([])==2 and 'sensitive' not in capsys.readouterr().err
    monkeypatch.setattr(a,'rehearse',lambda **kw:{'schema':'test'})
    assert a.main([])==0 and json.loads(capsys.readouterr().out)=={'schema':'test'}
