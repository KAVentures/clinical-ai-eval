"""Exercise the installed local hospital entrypoint in a disposable directory."""
import argparse
import base64
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
from queue import Queue, Empty
import re
import subprocess
import sys
import tempfile
from threading import Thread
from time import monotonic
from urllib.parse import urlencode

MAX_BODY = 1024 * 1024
ADDRESS = re.compile(r'Open http://127\.0\.0\.1:([0-9]{1,5})(/|/workspaces) in (?:your|a local) browser\. Username: (setup|operator)')
PASSWORD = re.compile(r'(?:One-time setup session password|Session password): ([A-Za-z0-9_-]{32,128})')


def require(ok):
    if not ok:
        raise ValueError('local launch acceptance check failed')


class Hidden(HTMLParser):
    def __init__(self):
        super().__init__(); self.fields = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('type') == 'hidden' and a.get('name') in ('csrf','layout'):
            name = a['name']; require(name not in self.fields)
            self.fields[name] = a.get('value','')


def setup_form(raw):
    p = Hidden(); p.feed(raw.decode('utf-8')); p.close()
    require(set(p.fields)=={'csrf','layout'} and all(re.fullmatch(r'[a-f0-9]{64}',v) for v in p.fields.values()))
    return urlencode({**p.fields,'confirm':'yes'}).encode('ascii')


class Child:
    def __init__(self, python, root, cwd):
        keys=('SYSTEMROOT','WINDIR','TEMP','TMP','TMPDIR','LANG','LC_ALL')
        env={k:os.environ[k] for k in keys if k in os.environ}
        self.proc=subprocess.Popen([str(python),'-I','-u','-m','caeval.hospital_first_run','--root',str(root)],
            cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        self.queue=Queue()
        self.thread=Thread(target=self._read,daemon=True); self.thread.start()

    def _read(self):
        try:
            for _ in range(32):
                raw=self.proc.stdout.readline(4097)
                if not raw or len(raw)>4096: break
                self.queue.put(raw.decode('ascii').rstrip('\r\n'))
        except (OSError,UnicodeError): pass
        finally: self.queue.put(None)

    def credentials(self, role, timeout):
        deadline=monotonic()+timeout; port=None
        while monotonic()<deadline:
            try: line=self.queue.get(timeout=max(0,deadline-monotonic()))
            except Empty: break
            require(line is not None)
            a=ADDRESS.fullmatch(line); p=PASSWORD.fullmatch(line)
            if a:
                require(port is None and a[3]==role and a[2]==('/' if role=='setup' else '/workspaces'))
                port=int(a[1]); require(1<=port<=65535)
            elif p:
                require(port is not None)
                return port,p[1]
        raise ValueError('local launch startup failed or timed out')

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try: self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=5)
        finally:
            self.proc.stdout.close(); self.thread.join(timeout=2)


def request(port, path, *, role=None, password=None, body=None):
    h={'Host':'127.0.0.1:'+str(port)}
    if role is not None:
        h['Authorization']='Basic '+base64.b64encode((role+':'+password).encode('ascii')).decode('ascii')
    if body is not None:
        h.update({'Content-Type':'application/x-www-form-urlencoded','Origin':'http://'+h['Host']})
    conn=HTTPConnection('127.0.0.1',port,timeout=5)
    try:
        conn.request('GET' if body is None else 'POST',path,body,headers=h)
        response=conn.getresponse(); raw=response.read(MAX_BODY+1)
        require(len(raw)<=MAX_BODY and response.getheader('Cache-Control')=='no-store')
        return response.status,raw
    finally: conn.close()


def rehearse(*, python=sys.executable, startup_timeout=30):
    require(type(startup_timeout) is int and 1<=startup_timeout<=120)
    # The caller runs from the approved installed interpreter. Never accept a
    # live evidence directory, credentials, or arbitrary web destination.
    with tempfile.TemporaryDirectory(prefix='caeval-launch-acceptance-') as tmp:
        cwd=Path(tmp).resolve(); root=cwd/'private'; root.mkdir(mode=0o700)
        child=Child(python,root,cwd)
        try:
            port,pw=child.credentials('setup',startup_timeout)
            require(request(port,'/')[0]==401)
            status,raw=request(port,'/',role='setup',password=pw)
            require(status==200 and b'first-run setup' in raw and not list(root.iterdir()))
            form=setup_form(raw)
            require(request(port,'/start',body=form)[0]==401 and not list(root.iterdir()))
            status,raw=request(port,'/start',role='setup',password=pw,body=form)
            require(status==200 and b'Setup complete' in raw)
            native,native_pw=child.credentials('operator',startup_timeout)
            require(native_pw!=pw)
            require(request(native,'/workspaces')[0]==401)
            require(request(native,'/workspaces',role='operator',password=pw)[0]==401)
            status,raw=request(native,'/workspaces',role='operator',password=native_pw)
            require(status==200 and b'Assessment workspaces' in raw and b'/comparisons' in raw)
            status,raw=request(native,'/comparisons',role='operator',password=native_pw)
            require(status==200 and b'Matched product comparison' in raw)
            require({p.name for p in root.iterdir()}=={'workspaces','staging','comparisons'})
            require(all(p.is_dir() and not p.is_symlink() and not list(p.iterdir()) for p in root.iterdir()))
        finally: child.close()
    return {'schema':'hospital-launch-acceptance/1','first_run_http':'passed','workbench_handoff':'passed',
            'comparison_route':'passed','credential_separation':'passed','disposable_layout':'passed',
            'institution_started':False,'clinical_validation_established':False,'production_approved':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--startup-timeout',type=int,default=30)
    a=p.parse_args(argv)
    try:
        print(json.dumps(rehearse(startup_timeout=a.startup_timeout),sort_keys=True)); return 0
    except (OSError,ValueError,TypeError,HTTPException,subprocess.SubprocessError):
        print('Local launch acceptance failed. Check the installed release and local loopback policy.',file=sys.stderr); return 2


if __name__=='__main__':
    sys.exit(main())
