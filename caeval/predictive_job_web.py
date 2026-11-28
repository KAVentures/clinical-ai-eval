"""Local browser control of durable prediction jobs in the selected assessment."""
import argparse
import hmac
import json
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server

from . import assessment_csv_web as parent, assessment_setup_web as setup
from . import assessment_workbench_web as web, assessment_resume_web as resume
from . import predictive_local_execution as local, predictive_execution_job as job
from . import predictive_research as pred, vendor_workbench as storage

ROLES = ('baseline', 'candidate')
BATCH = 3


def require(ok):
    if not ok:
        raise ValueError('job action refused')


def material(child, role):
    require(role in ROLES and child.prepared is not None and child.review is None and not getattr(child, 'collection_committed', False))
    plan, inputs = resume.base_inputs(child.destination, child.prepared['plan_sha256'], child.head)
    pp = inputs['predictive_plan']
    rows = json.loads(setup.read_local(child.staging, role+'-features.json'), object_pairs_hook=pred._unique)
    options = json.loads(setup.read_local(child.staging, role+'-execution-options.json'), object_pairs_hook=pred._unique)
    require(options['role'] == role)
    ex = local.preview(pp, plan['predictive_plan_sha256'], options, rows)
    return (child.destination/(role+'-job.db'), pp, plan['predictive_plan_sha256'], ex, ex['execution_sha256'], rows)


def act(child, role, action, execution, head):
    args = material(child, role)
    require(args[4] == execution)
    if action == 'init':
        require(head == '' and not resume.present(args[0]))
        h = job.init(*args)
        return job.status(*args, h)
    require(pred.hex64(head))
    if action == 'inspect':
        return job.status(*args, head, inspect=True)
    state = job.status(*args, head)
    if action == 'status':
        return state
    if action == 'batch':
        for _ in range(BATCH):
            if state['finished'] is not None:
                break
            state = job.step(*args, state['head'], confirmed=True)
        return state
    if action == 'recover':
        return job.recover(*args, head, confirmed_stopped=True)
    if action == 'finish':
        return job.finish(*args, head)
    if action == 'stage':
        package = job.export(*args, head)
        paths = {child.staging/(role+'-rows.json'): package['bundle']['rows'],
                 child.staging/(role+'-execution-package.json'): package}
        require(not any(resume.present(p) for p in paths))
        created = []
        try:
            for path, value in paths.items():
                pred._write(path, value); created.append(path)
        except BaseException:
            for path in reversed(created): path.unlink()
            raise
        child.collection = None
        return {**state, 'receipt_sha256': package['receipt']['receipt_sha256'], 'bundle_sha256': package['bundle']['bundle_sha256']}
    raise ValueError('unknown action')


def stage_metadata(child, fields):
    pred.fields(fields, 'baseline_head candidate_head as_of reference_sha256')
    require(pred.hex64(fields['reference_sha256']))
    import datetime
    require(datetime.date.fromisoformat(fields['as_of']).isoformat() == fields['as_of'])
    receipts = {'reference': fields['reference_sha256']}
    for role in ROLES:
        args = material(child, role)
        package = job.export(*args, fields[role+'_head'])
        saved = json.loads(setup.read_local(child.staging, role+'-execution-package.json'), object_pairs_hook=pred._unique)
        rows = json.loads(setup.read_local(child.staging, role+'-rows.json'), object_pairs_hook=pred._unique)
        require(pred.canonical(package) == pred.canonical(saved) and pred.canonical(rows) == pred.canonical(package['bundle']['rows']))
        receipts[role] = package['receipt']['receipt_sha256']
    pred._write(child.staging/'collection.json', {'as_of':fields['as_of'], 'source_receipts':receipts})
    child.collection = None
    return receipts


def metadata_page(child, receipts=None):
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Prediction collection metadata</title><link rel="stylesheet" href="/style.css"></head><body><nav><a href="/">Setup</a> | <a href="/workspaces">Workspace selector</a></nav><h1>Link completed prediction jobs</h1><p>Both jobs must be finished and staged. Supply their independently retained final heads and the independently verified reference receipt digest. This stages collection metadata only; separate collection preview and confirmation are required.</p><form method="post"><input type="hidden" name="csrf" value="'+child.csrf+'">']
    for key,label in [('baseline_head','Baseline final head'),('candidate_head','Candidate final head'),('as_of','Administrative as-of date (YYYY-MM-DD)'),('reference_sha256','Reference receipt SHA-256')]:
        out.append('<p><label>'+label+' <input required maxlength="64" name="'+key+'" autocomplete="off"></label></p>')
    out.append('<button>Verify jobs and stage collection metadata</button></form>')
    if receipts:
        out.append(web.table(['Role','Receipt digest'],receipts.items()))
    return (''.join(out)+'</body></html>').encode('utf-8')


def page(child, role, state=None, message=''):
    args = material(child, role)
    ex = args[3]; opt = ex['options']
    e = web.esc
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Prediction job</title><link rel="stylesheet" href="/style.css"></head><body><nav><a href="/">Setup</a> | <a href="/workspaces">Workspace selector</a></nav><h1>'+e(role)+' prediction job</h1><p class="notice">Approved local numeric features only. No reference labels are sent. Retain checkpoints independently. Do not run competing workers.</p><p role="status">'+e(message)+'</p>',
           web.table(['Scope','Value'],[(k,ex[k]) for k in ('version','endpoint','horizon','n_planned')]+[(k,opt[k]) for k in ('port','feature_names','feature_protocol_sha256','max_calls','timeout_seconds','max_seconds')]),
           '<p>Execution approval digest: <code>'+e(args[4])+'</code></p>']
    if state is not None:
        out.append(web.table(['Progress','Value'],[(k,v) for k,v in state.items() if k != 'counts']+list(state['counts'].items())))
        out.append('<progress max="'+str(state['n_planned'])+'" value="'+str(state['n_recorded'])+'"></progress>')
    out.append('<form method="post"><input type="hidden" name="csrf" value="'+child.csrf+'"><label>Independently approved execution digest <input name="execution" required maxlength="64" autocomplete="off"></label><p><label>Retained job head (empty only for creation) <input name="head" maxlength="64" autocomplete="off"></label></p>')
    for key,label in [('init','Create job'),('status','Verify progress'),('inspect','Inspect newer history (reconcile separately)'),('batch','Approve up to 3 model calls'),('recover','Confirm worker stopped; mark pending attempt unknown'),('finish','Finish job'),('stage','Stage sealed predictions and receipt')]:
        out.append('<button name="action" value="'+key+'">'+label+'</button> ')
    out.append('</form><p>Batch runs synchronously. If interrupted, inspect using the last retained head and reconcile before continuing. For collection metadata use the staged receipt digest; never substitute reference labels.</p></body></html>')
    return ''.join(out).encode('utf-8')


class Manager(parent.Manager):
    def page(self, message=''):
        body = super().page(message)
        if self.active and self.active.prepared is not None and self.active.review is None:
            body = body.replace(b'</body>', b'<p><a href="/job-baseline">Baseline prediction job</a> | <a href="/job-candidate">Candidate prediction job</a> | <a href="/job-collection">Link completed prediction jobs</a></p></body>',1)
        return body

    def __call__(self, env, start):
        path = env.get('PATH_INFO')
        if path not in ('/job-baseline','/job-candidate','/job-collection'):
            return super().__call__(env,start)
        def respond(status, body=b'', mime='text/plain; charset=utf-8', extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra])
            return [body]
        if not self.host or env.get('HTTP_HOST') != self.host or env.get('REMOTE_ADDR') != '127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host):
            return respond('403 Forbidden')
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth):
            return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local assessment"')])
        if env.get('QUERY_STRING'): return respond('400 Bad Request')
        role=path[5:]
        with self.lock:
            child=self.active
            if child is None or child.prepared is None or child.review is not None or getattr(child,'collection_committed',False): return respond('409 Conflict')
            method=env.get('REQUEST_METHOD')
            if method not in ('GET','POST'): return respond('405 Method Not Allowed',extra=[('Allow','GET, POST')])
            state=None
            keys = {'csrf','baseline_head','candidate_head','as_of','reference_sha256'} if role=='collection' else {'csrf','execution','head','action'}
            if method=='POST':
                try:
                    n=int(env.get('CONTENT_LENGTH','')); require(1<=n<=4096 and env.get('CONTENT_TYPE')=='application/x-www-form-urlencoded')
                    raw=env['wsgi.input'].read(n); require(len(raw)==n)
                    pairs=parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=len(keys),errors='strict')
                    fields=dict(pairs); require(len(pairs)==len(fields)==len(keys) and set(fields)==keys)
                    require(fields['csrf'].isascii() and hmac.compare_digest(fields['csrf'],child.csrf))
                except (ValueError,TypeError,UnicodeError): return respond('403 Forbidden')
                try:
                    if role=='collection':
                        fields.pop('csrf'); state=stage_metadata(child,fields)
                    else:
                        state=act(child,role,fields['action'],fields['execution'],fields['head'])
                except (OSError,ValueError,TypeError,KeyError,RecursionError,storage.sqlite3.Error):
                    return respond('409 Conflict',b'Job action refused. Check inputs and reconcile retained checkpoints; do not retry an uncertain call.')
            try:
                return respond('200 OK',metadata_page(child,state) if role=='collection' else page(child,role,state),'text/html; charset=utf-8')
            except (OSError,ValueError,TypeError,KeyError,RecursionError,storage.sqlite3.Error):
                return respond('409 Conflict',b'Job view unavailable. Inspect and reconcile retained checkpoints.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True); parser.add_argument('--staging-root',required=True)
    a=parser.parse_args(argv)
    try:
        app=Manager(a.root,a.staging_root)
        with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
            app.host='127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+'/workspaces in a local browser. Username: operator',flush=True)
            print('Session password: '+app.password,flush=True)
            server.serve_forever()
    except KeyboardInterrupt: return 0
    except (OSError,ValueError,TypeError):
        print('Local job browser startup refused.'); return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
