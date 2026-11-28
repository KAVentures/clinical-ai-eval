"""Private staged browser provisioning for the local assessment workbench."""
import argparse
import base64
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from threading import Lock
from wsgiref.simple_server import make_server

from . import assessment_workbench as work, assessment_workbench_web as web
from . import predictive_research as pred, prospective_research as binary
from . import vendor_submission as sub, vendor_workbench as vendor

PLAN_FILES = ('request-spec', 'actors', 'predictive-spec', 'binary-spec', 'manifest', 'memberships')
COLLECTION_FILES = ('collection', 'baseline-rows', 'candidate-rows', 'reference-rows', 'outcomes', 'reference-counts', 'target-counts')
LIMIT = 16 * 1024 * 1024


def require(ok):
    if not ok:
        raise ValueError('setup scope, staged file or checkpoint mismatch')


def private_directory(path):
    p = Path(path).absolute()
    require(p.is_dir() and p.resolve() == p and not p.is_symlink())
    if os.name == 'posix':
        s = p.stat()
        require(s.st_uid == os.getuid() and not s.st_mode & 0o077)
    return p


def read_local(directory, name, limit=LIMIT):
    p = directory / name
    require(not p.is_symlink() and p.is_file())
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(p, flags), 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1)
        if os.name == 'posix':
            require(s.st_uid == os.getuid() and not s.st_mode & 0o077)
        raw = f.read(limit+1)
    require(len(raw) <= limit)
    return raw


def staged(directory, names):
    values = {}
    for name in names:
        values[name] = json.loads(read_local(directory, name+'.json'), object_pairs_hook=pred._unique)
    require(len(pred.canonical(values)) <= LIMIT)
    return values


def prepare(values):
    actors = values['actors']
    pred.fields(actors, 'requester reviewer representative')
    request = sub.freeze(values['request-spec'])
    scope = vendor._scope(request, request['request_sha256'], **actors)
    root = vendor._root(request, scope)
    roster = binary._rosters(binary.data.validate(values['manifest']))
    key = secrets.token_bytes(32)
    site_keys = {s: secrets.token_bytes(32) for s in roster}
    bp = binary.freeze(values['binary-spec'], values['manifest'], values['memberships'], key, site_keys)
    pp = pred.freeze(values['predictive-spec'])
    plan = work.freeze(request, request['request_sha256'], root, pp, bp)
    return {'request': request, 'predictive-plan': pp, 'binary-plan': bp, 'assessment-plan': plan,
            'manifest': values['manifest'], 'keys': {'audit': key.hex(), 'sites': {s:k.hex() for s,k in site_keys.items()}}}


class Setup:
    def __init__(self, staging, destination):
        self.staging = private_directory(staging)
        self.destination = Path(destination).absolute()
        private_directory(self.destination.parent)
        require(not self.destination.exists() and not self.destination.is_symlink() and self.staging != self.destination)
        self.password = secrets.token_urlsafe(32)
        self.auth = 'Basic '+base64.b64encode(('operator:'+self.password).encode('ascii')).decode('ascii')
        self.csrf = secrets.token_hex(32)
        self.host = None
        self.lock = Lock()
        self.preview = None
        self.prepared = None
        self.head = None
        self.collection = None
        self.review = None

    def preview_plan(self):
        require(self.prepared is None)
        self.preview = None
        values = staged(self.staging, PLAN_FILES)
        self.preview = (work.digest(values), prepare(values))

    def commit_plan(self):
        require(self.prepared is None and self.preview is not None)
        expected, files = self.preview
        values = staged(self.staging, PLAN_FILES)
        require(work.digest(values) == expected)
        self.destination.mkdir(mode=0o700, exist_ok=False)
        created = []
        try:
            for name, value in files.items():
                path = self.destination/(name+'.json')
                pred._write(path,value)
                created.append(path)
            db = self.destination/'procurement.db'
            request = files['request']
            root = vendor.init(db,request,request['request_sha256'],**values['actors'])
            created.append(db)
            require(root == files['assessment-plan']['vendor_root'])
        except BaseException:
            for path in reversed(created):
                path.unlink()
            self.destination.rmdir()
            raise
        self.prepared = files['assessment-plan']
        self.head = root
        self.preview = None

    def _plan(self):
        require(self.prepared is not None)
        plan = json.loads(read_local(self.destination,'assessment-plan.json'), object_pairs_hook=pred._unique)
        require(plan == self.prepared)
        return plan

    def import_vendor(self):
        require(self.review is None)
        plan = self._plan()
        self.head = vendor.import_submission(self.destination/'procurement.db', plan['vendor_root'], self.head,
                                             read_local(self.staging,'submission.zip',sub.MAX_ARCHIVE))
        self.collection = None

    def clarify(self):
        require(self.review is None)
        plan = self._plan()
        body = json.loads(read_local(self.staging,'clarification.json'), object_pairs_hook=pred._unique)
        self.head = vendor.record_clarification(self.destination/'procurement.db', plan['vendor_root'], self.head,
                                                body, read_local(self.staging,'clarification.bin',vendor.MAX_NOTE))
        self.collection = None

    def _collection(self, values):
        plan = self._plan()
        c = values['collection']
        pred.fields(c,'as_of source_receipts')
        pred.fields(c['source_receipts'],'baseline candidate reference')
        pp = pred._read(self.destination/'predictive-plan.json')
        bundles = {r:pred.seal(pp,plan['predictive_plan_sha256'],r,c['source_receipts'][r],values[r+'-rows']) for r in pred.ROLES}
        cfg = {'db':str(self.destination/'procurement.db'),'head':self.head,'as_of':c['as_of'],
               'predictive_plan':str(self.destination/'predictive-plan.json'), 'binary_plan':str(self.destination/'binary-plan.json'),
               'manifest':str(self.destination/'manifest.json'),'keys':str(self.destination/'keys.json'),
               'bundles':{r:str(self.destination/(r+'.json')) for r in pred.ROLES},
               'expected_bundles':{r:bundles[r]['bundle_sha256'] for r in pred.ROLES},
               **{k.replace('-','_'):str(self.destination/(k+'.json')) for k in ('outcomes','reference-counts','target-counts')}}
        key, sites = binary._read_keys(cfg['keys'])
        report = work.analyze(plan,plan['plan_sha256'],db=cfg['db'],head=self.head,as_of=c['as_of'],
            predictive_plan=pp,bundles=bundles,expected_bundles=cfg['expected_bundles'],
            binary_plan=pred._read(cfg['binary_plan']),manifest=pred._read(cfg['manifest']),audit_key=key,site_keys=sites,
            outcomes=values['outcomes'],reference_counts=values['reference-counts'],target_counts=values['target-counts'])
        return cfg, {**bundles, **{k:values[k] for k in ('outcomes','reference-counts','target-counts')}}, report

    def preview_collection(self):
        require(self.review is None)
        self.collection = None
        values = staged(self.staging,COLLECTION_FILES)
        cfg, files, report = self._collection(values)
        self.collection = (work.digest(values), report['report_sha256'], cfg['expected_bundles'])

    def commit_collection(self):
        require(self.review is None and self.collection is not None)
        values = staged(self.staging,COLLECTION_FILES)
        require(work.digest(values) == self.collection[0])
        cfg, files, report = self._collection(values)
        require(report['report_sha256'] == self.collection[1])
        created = []
        try:
            for name,value in {**files,'config':cfg}.items():
                path = self.destination/(name+'.json')
                pred._write(path,value)
                created.append(path)
            app = web.Operator(str(self.destination/'assessment-plan.json'),self.prepared['plan_sha256'],str(self.destination/'config.json'))
            app.host, app.auth, app.csrf = self.host, self.auth, self.csrf
            self.review = app
        except BaseException:
            for p in reversed(created):
                p.unlink()
            raise
        self.collection = None

    def page(self, message=''):
        def form(path,label,enabled=True,approval=None):
            field = '' if approval is None else '<input type="hidden" name="approval" value="'+web.esc(approval)+'">'
            return '<form method="post" action="/'+path+'"><input type="hidden" name="csrf" value="'+self.csrf+'">'+field+'<button'+('' if enabled else ' disabled')+'>'+label+'</button></form>'
        e = web.esc
        out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Assessment setup</title><link rel="stylesheet" href="/style.css"></head><body>',
               '<h1>Hospital assessment setup</h1><p class="notice">Private local operator only. Freeze before inspecting outcomes. No clinical approval.</p><p role="status">'+e(message)+'</p>',
               '<section><h2>1. Prospective study setup</h2><p>Stage these JSON files: '+e(', '.join(x+'.json' for x in PLAN_FILES))+'.</p>',form('preview-plan','Preview prospective plan',self.prepared is None),form('commit-plan','Confirm and freeze',self.preview is not None and self.prepared is None,self.preview[1]['assessment-plan']['plan_sha256'] if self.preview else None)]
        if self.preview:
            p = self.preview[1]['assessment-plan']
            pp, bp = self.preview[1]['predictive-plan']['spec'], self.preview[1]['binary-plan']['spec']
            out.extend([web.table(['Scope','Value'],[(k,v) for k,v in p['scope'].items()]+[('Study',p['study']),('Eligible units',p['n_planned']),('Baseline version',p['baseline_version']),('Prediction endpoint',pp['endpoint']),('Prediction horizon',pp['horizon']),('Action',pp['action']),('Thresholds (bp)',pp['thresholds_bp']),('Calibration bins (bp)',pp['bin_edges_bp']),('Adverse outcome endpoint',bp['endpoint']),('Adverse outcome horizon',bp['horizon']),('Holdout design',bp['holdout_design']),('IID design requested',bp['binary_design'] is not None)]),'<p>Plan digest: <code>'+e(p['plan_sha256'])+'</code></p>'])
        if self.prepared:
            out.append('<p>Retain independently: assessment plan <code>'+e(self.prepared['plan_sha256'])+'</code>; buyer request <code>'+e(self.prepared['request_sha256'])+'</code>; procurement root <code>'+e(self.prepared['vendor_root'])+'</code>; current head <code>'+e(self.head)+'</code>.</p>')
        out.extend(['</section><section><h2>2. Vendor evidence</h2><p>Authenticate sender and quarantine the archive before staging submission.zip. Import records opaque bytes; it does not establish evidence sufficiency.</p>',form('import-vendor','Import staged vendor submission',self.prepared is not None and self.review is None),'<p>For a question, response or review, stage clarification.json and clarification.bin with its exact evidence digest.</p>',form('clarify','Record staged clarification',self.prepared is not None and self.review is None),'</section>',
                    '<section><h2>3. Collect and seal research inputs</h2><p>Only after freezing, stage: '+e(', '.join(x+'.json' for x in COLLECTION_FILES))+'.</p>',form('preview-collection','Validate staged collection',self.prepared is not None and self.review is None),form('commit-collection','Confirm and save collection',self.collection is not None and self.review is None,self.collection[1] if self.collection else None)])
        if self.collection:
            out.append('<p>Retain the independently reviewed source checkpoints:</p>'+web.table(['Role','Bundle digest'],self.collection[2].items())+'<p>Preview report digest: <code>'+e(self.collection[1])+'</code></p>')
        if self.review:
            out.append('<p>Configuration saved. <a href="/review">Open assessment review dashboard</a>.</p>')
        out.append('</section></body></html>')
        return ''.join(out).encode('utf-8')

    def __call__(self, env, start):
        def respond(status,body=b'',mime='text/plain; charset=utf-8',extra=()):
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
            if self.review and path in ('/review','/run','/verify','/download'):
                return self.review({**env,'PATH_INFO':'/' if path=='/review' else path},start)
            if method == 'GET' and path == '/':
                return respond('200 OK',self.page(),'text/html; charset=utf-8')
            if method == 'GET' and path == '/style.css':
                return respond('200 OK',web.CSS.encode(),'text/css; charset=utf-8')
            actions = {'/preview-plan':self.preview_plan,'/commit-plan':self.commit_plan,'/import-vendor':self.import_vendor,'/clarify':self.clarify,'/preview-collection':self.preview_collection,'/commit-collection':self.commit_collection}
            if path not in actions:
                return respond('404 Not Found')
            if method != 'POST':
                return respond('405 Method Not Allowed',extra=[('Allow','POST')])
            approval = None
            if path == '/commit-plan' and self.preview is not None:
                approval = self.preview[1]['assessment-plan']['plan_sha256']
            elif path == '/commit-collection' and self.collection is not None:
                approval = self.collection[1]
            body = ('csrf='+self.csrf+('' if approval is None else '&approval='+approval)).encode('ascii')
            if env.get('CONTENT_TYPE') != 'application/x-www-form-urlencoded' or env.get('CONTENT_LENGTH') != str(len(body)) or not hmac.compare_digest(env['wsgi.input'].read(len(body)),body):
                return respond('403 Forbidden')
            try:
                actions[path]()
                return respond('200 OK',self.page('Operation completed. Retain the displayed checkpoints separately.'),'text/html; charset=utf-8')
            except Exception:
                self.preview = None
                self.collection = None
                return respond('409 Conflict',self.page('Operation refused. Check staged inputs and retained checkpoints.'),'text/html; charset=utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging',required=True)
    parser.add_argument('--destination',required=True)
    a = parser.parse_args(argv)
    try:
        app = Setup(a.staging,a.destination)
        with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
            app.host = '127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+' in a local browser. Username: operator',flush=True)
            print('Session password: '+app.password,flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError,ValueError,TypeError):
        print('Local setup startup refused.',file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
