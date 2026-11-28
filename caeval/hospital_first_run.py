"""Authenticated loopback first-run setup for the existing local workbench."""
import argparse
import base64
import hashlib
import hmac
from html import escape
import json
import secrets
import sys
from threading import Lock
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server, WSGIRequestHandler

from . import hospital_launcher as launcher


def checkpoint(status):
    return hashlib.sha256(json.dumps(status, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class Setup:
    def __init__(self, root):
        self.root = launcher.private(root)
        self.password = secrets.token_urlsafe(32)
        self.auth = 'Basic '+base64.b64encode(('setup:'+self.password).encode('ascii')).decode('ascii')
        self.csrf = secrets.token_hex(32)
        self.host = None
        self.launch_requested = False
        self.lock = Lock()

    def page(self, status):
        ready = all(s != 'unsafe_or_unavailable' for s in status['directories'].values())
        body = ['<h1>Clinical AI Eval · first-run setup</h1>',
                '<p>Local research and procurement workbench. This setup does not establish clinical readiness.</p>',
                '<h2>Private directory layout</h2><table><tr><th>Directory</th><th>Status</th></tr>']
        for name, state in status['directories'].items():
            body.append('<tr><td>'+escape(name)+'</td><td>'+escape(state.replace('_',' '))+'</td></tr>')
        body.append('</table><p>Institutional accounts, gateway configuration and permissions are provisioned separately by an administrator. This setup never creates or starts that service.</p>')
        if ready:
            body.append('<form method="post" action="/start"><input type="hidden" name="csrf" value="'+self.csrf+'"><input type="hidden" name="layout" value="'+checkpoint(status)+'"><p><label><input type="checkbox" name="confirm" value="yes" required> I approve initializing or reusing the three local directories and starting the local workbench.</label></p><button>Initialize and launch local workbench</button></form>')
        else:
            body.append('<p>Unsafe or unavailable directory. Correct the layout outside the browser and reload. No permission changes are made here.</p>')
        body.append('<p>Use approved filesystem access controls. On Windows, directory diagnostics do not verify ACLs. Existing evidence is not migrated or overwritten.</p>')
        return self.html(''.join(body))

    @staticmethod
    def html(body):
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Clinical AI Eval setup</title><style>body{font:16px system-ui;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.55}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.6rem;border-bottom:1px solid #888}button{padding:.75rem;font:inherit}h1{font-size:1.7rem}</style></head><body>'+body+'</body></html>').encode('utf-8')

    def __call__(self, env, start):
        def respond(status, body=b'', mime='text/plain; charset=utf-8', extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra])
            return [body]
        if not self.host or env.get('HTTP_HOST') != self.host or env.get('REMOTE_ADDR') != '127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host):
            return respond('403 Forbidden')
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth):
            return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local first-run setup"')])
        if env.get('QUERY_STRING'): return respond('400 Bad Request')
        path, method = env.get('PATH_INFO'), env.get('REQUEST_METHOD')
        if path not in ('/','/start'): return respond('404 Not Found')
        if (path,method) not in (('/','GET'),('/start','POST')):
            return respond('405 Method Not Allowed',extra=[('Allow','GET' if path=='/' else 'POST')])
        with self.lock:
            if self.launch_requested: return respond('409 Conflict',b'Setup session is finished.')
            if method=='GET':
                try: return respond('200 OK',self.page(launcher.diagnose(self.root)),'text/html; charset=utf-8')
                except (OSError,ValueError): return respond('409 Conflict',b'Private root unavailable.')
            try:
                n=int(env.get('CONTENT_LENGTH',''))
                launcher.require(1<=n<=1024 and env.get('CONTENT_TYPE')=='application/x-www-form-urlencoded')
                raw=env['wsgi.input'].read(n); launcher.require(len(raw)==n)
                pairs=parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=3,errors='strict')
                fields=dict(pairs)
                launcher.require(len(pairs)==len(fields) and set(fields)=={'csrf','layout','confirm'})
                launcher.require(fields['csrf'].isascii() and hmac.compare_digest(fields['csrf'],self.csrf) and fields['confirm']=='yes')
            except (ValueError,TypeError,KeyError,UnicodeError): return respond('403 Forbidden')
            try:
                status=launcher.diagnose(self.root)
                launcher.require(fields['layout']==checkpoint(status))
                launcher.init_local(self.root)
                launcher.check(self.root,'local')
                self.launch_requested=True
                return respond('200 OK',self.html('<h1>Setup complete</h1><p>The setup session is closing. Open the new local workbench address printed in the terminal and use its new operator credentials. The setup password will not work there.</p>'),'text/html; charset=utf-8')
            except (OSError,ValueError): return respond('409 Conflict',b'Layout changed or initialization refused. Inspect the directories and reload.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,help='Existing private parent directory')
    a=parser.parse_args(argv)
    try:
        app=Setup(a.root)
        with make_server('127.0.0.1',0,app,handler_class=QuietHandler) as server:
            app.host='127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+'/ in your browser. Username: setup',flush=True)
            print('One-time setup session password: '+app.password,flush=True)
            while not app.launch_requested:
                server.handle_request()
        # The setup listener is closed before the workbench starts. Its session
        # password, port and CSRF token are not handed to the native application.
        return launcher.launch_local(app.root)
    except KeyboardInterrupt: return 0
    except (OSError,ValueError,TypeError,ImportError):
        print('Setup or launch refused. Inspect the private layout and installed application.',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
