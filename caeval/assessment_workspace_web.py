"""Local workspace selector with explicit independent checkpoint entry."""
import argparse
import base64
import hmac
import itertools
import re
import secrets
import sys
from threading import Lock
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server

from . import assessment_resume_web as resume, assessment_setup_web as setup
from . import assessment_workbench_web as web, predictive_research as pred
from . import vendor_workbench as vendor

MAX_DIRS = 200
MAX_BODY = 4096


def require(ok):
    if not ok:
        raise ValueError('workspace selection refused')


def name_ok(name):
    return isinstance(name, str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,47}', name) is not None


def inventory(root):
    """List names only; never load evidence or adopt a discovered checkpoint."""
    with_entries = list(itertools.islice(root.iterdir(), MAX_DIRS + 1))
    require(len(with_entries) <= MAX_DIRS)
    return sorted(p.name for p in with_entries if name_ok(p.name) and not p.is_symlink() and p.is_dir())


class Navigation:
    def page(self, message=''):
        return super().page(message).replace(b'<body>', b'<body><nav><a href="/workspaces">Workspace selector</a></nav>', 1)


class NewWorkspace(Navigation, setup.Setup):
    pass


class ExistingWorkspace(Navigation, resume.Resume):
    pass


class Manager:
    def __init__(self, root, staging_root):
        self.root = setup.private_directory(root)
        self.staging_root = setup.private_directory(staging_root)
        require(self.root != self.staging_root and self.root not in self.staging_root.parents and self.staging_root not in self.root.parents)
        self.password = secrets.token_urlsafe(32)
        self.auth = 'Basic '+base64.b64encode(('operator:'+self.password).encode('ascii')).decode('ascii')
        self.csrf = secrets.token_hex(32)
        self.host = None
        self.active = None
        self.active_name = None
        self.lock = Lock()

    def select(self, fields):
        name, action = fields['name'], fields['action']
        require(name_ok(name) and action in ('new','resume'))
        names = inventory(self.root)
        destination, staging = self.root/name, self.staging_root/name
        require(not staging.is_symlink())
        if action == 'new':
            require(len(names) < MAX_DIRS and not resume.present(destination))
            require(not any(fields[k] for k in ('plan','head','report')))
            if not staging.exists():
                staging.mkdir(mode=0o700, exist_ok=False)
            child = NewWorkspace(staging,destination)
        else:
            require(name in names and pred.hex64(fields['plan']) and pred.hex64(fields['head']))
            require(not fields['report'] or pred.hex64(fields['report']))
            child = ExistingWorkspace(staging,destination,fields['plan'],fields['head'],fields['report'] or None)
        child.host, child.auth = self.host, self.auth
        if child.review:
            child.review.host, child.review.auth = self.host, self.auth
        self.active, self.active_name = child, name
        self.csrf = secrets.token_hex(32)

    def page(self, message=''):
        names = inventory(self.root)
        e = web.esc
        choices = ''.join('<option value="'+e(n)+'">'+e(n)+'</option>' for n in names)
        common = '<input type="hidden" name="csrf" value="'+self.csrf+'">'
        status = 'Active workspace: '+self.active_name if self.active else 'No active workspace.'
        link = '<p><a href="/">Return to active workspace</a></p>' if self.active else ''
        return ('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Assessment workspaces</title><link rel="stylesheet" href="/style.css"></head><body><h1>Assessment workspaces</h1><p class="notice">Private local operator. Switching discards uncommitted previews, not saved evidence. Retain all checkpoints separately.</p><p role="status">'''+e(message)+'</p><p>'+e(status)+'</p>'+link+
                '<section><h2>Create a workspace</h2><form method="post" action="/select-workspace">'+common+'<input type="hidden" name="action" value="new"><input type="hidden" name="plan" value=""><input type="hidden" name="head" value=""><input type="hidden" name="report" value=""><label>Workspace name <input name="name" required maxlength="48" pattern="[a-z0-9][a-z0-9_-]{0,47}"></label><p>Stage files in the matching staging subdirectory after opening.</p><button>Open new setup</button></form></section>'+
                '<section><h2>Resume a saved workspace</h2><p>Names are discovered locally; no checkpoint is trusted from the workspace itself.</p><form method="post" action="/select-workspace">'+common+'<input type="hidden" name="action" value="resume"><p><label>Workspace <select name="name" required><option value="">Select</option>'+choices+'</select></label></p><p><label>Retained plan digest <input name="plan" required maxlength="64" pattern="[a-f0-9]{64}" autocomplete="off"></label></p><p><label>Retained procurement head <input name="head" required maxlength="64" pattern="[a-f0-9]{64}" autocomplete="off"></label></p><p><label>Retained report digest (completed collections only) <input name="report" maxlength="64" pattern="[a-f0-9]{64}" autocomplete="off"></label></p><button>Resume selected workspace</button></form></section></body></html>').encode('utf-8')

    def __call__(self, env, start):
        def respond(status, body=b'', mime='text/plain; charset=utf-8', extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra])
            return [body]
        if not self.host or env.get('HTTP_HOST') != self.host or env.get('REMOTE_ADDR') != '127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host):
            return respond('403 Forbidden')
        auth = env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth):
            return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local assessment"')])
        if env.get('QUERY_STRING'):
            return respond('400 Bad Request')
        path, method = env.get('PATH_INFO'),env.get('REQUEST_METHOD')
        with self.lock:
            if path == '/style.css' and method == 'GET':
                return respond('200 OK',web.CSS.encode(),'text/css; charset=utf-8')
            if path == '/workspaces' or (path == '/' and self.active is None):
                if method != 'GET':
                    return respond('405 Method Not Allowed',extra=[('Allow','GET')])
                try:
                    return respond('200 OK',self.page(),'text/html; charset=utf-8')
                except (OSError,ValueError):
                    return respond('409 Conflict',b'Workspace inventory unavailable.')
            if path == '/select-workspace':
                if method != 'POST':
                    return respond('405 Method Not Allowed',extra=[('Allow','POST')])
                try:
                    size = int(env.get('CONTENT_LENGTH',''))
                    require(1 <= size <= MAX_BODY and env.get('CONTENT_TYPE') == 'application/x-www-form-urlencoded')
                    raw = env['wsgi.input'].read(size)
                    require(len(raw) == size)
                    pairs = parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=6,errors='strict')
                    fields = dict(pairs)
                    require(len(pairs) == len(fields) == 6 and set(fields) == {'csrf','action','name','plan','head','report'})
                    require(fields['csrf'].isascii() and hmac.compare_digest(fields['csrf'],self.csrf))
                except (ValueError,TypeError,UnicodeError):
                    return respond('403 Forbidden')
                try:
                    self.select(fields)
                except (OSError,ValueError,TypeError,KeyError,RecursionError,vendor.sqlite3.Error):
                    return respond('409 Conflict',b'Workspace selection refused. Check private directories and retained checkpoints.')
                return respond('303 See Other',extra=[('Location','/')])
            if self.active is not None:
                return self.active(env,start)
            return respond('404 Not Found')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--staging-root',required=True)
    a = parser.parse_args(argv)
    try:
        app = Manager(a.root,a.staging_root)
        with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
            app.host = '127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+'/workspaces in a local browser. Username: operator',flush=True)
            print('Session password: '+app.password,flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError,ValueError,TypeError):
        print('Workspace selector startup refused.',file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
