"""Strict staged CSV import for the local assessment workspace browser."""
import argparse
import csv
import hashlib
import hmac
import io
import re
from wsgiref.simple_server import make_server

from . import assessment_protocol_web as protocol, assessment_setup_web as setup
from . import assessment_workbench_web as web, predictive_research as pred
from . import prospective_research as binary, vendor_workbench as vendor

MANIFEST = ('record_id','partition','patient_key','lineage_key','content_sha256','site_key','observed_at')
COHORT = {'manifest':MANIFEST, 'memberships':('unit_id','group')}
COLLECTION = {'collection':('as_of','baseline_sha256','candidate_sha256','reference_sha256'),
              'baseline-rows':('unit_id','probability_bp'), 'candidate-rows':('unit_id','probability_bp'),
              'reference-rows':('unit_id','label'), 'outcomes':('unit_id','baseline','candidate'),
              'reference-counts':('feature','level','count'), 'target-counts':('feature','level','count')}
MAX_ROWS = 100000
LIMIT = 16 * 1024 * 1024


def require(ok):
    if not ok:
        raise ValueError('invalid CSV inventory or approval')


def parse(raw, columns):
    reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
    require(next(reader, None) == list(columns))
    result = []
    for row in reader:
        require(len(result) < MAX_ROWS and len(row) == len(columns))
        require(all(len(v) <= 256 and v == v.strip() and all(32 <= ord(c) != 127 for c in v) for v in row))
        result.append(dict(zip(columns,row)))
    return result


def boolean(v):
    require(v in ('','true','false'))
    return None if not v else v == 'true'


def number(v):
    require(re.fullmatch(r'[0-9]{1,9}',v) is not None)
    return int(v)


def counts(rows):
    result = {}
    for r in rows:
        require(r['feature'] and r['level'])
        f = result.setdefault(r['feature'],{})
        require(r['level'] not in f)
        f[r['level']] = number(r['count'])
    return result


def convert(kind, tables):
    if kind == 'cohort':
        manifest = binary.data.validate([{k:v or None for k,v in row.items()} for row in tables['manifest']])
        groups = {}
        for r in tables['memberships']:
            u,g = r['unit_id'],r['group']
            require(binary.subgroup._identifier(u) and u not in groups and (not g or binary.subgroup._identifier(g)))
            groups[u] = g or None
        require(set(groups) == {r['record_id'] for r in manifest if r['partition']=='test'})
        return {'manifest':manifest,'memberships':groups}
    require(kind=='collection')
    c = tables['collection']; require(len(c)==1)
    result = {'collection':{'as_of':c[0]['as_of'],'source_receipts':{r:c[0][r+'_sha256'] for r in pred.ROLES}}}
    for r in ('baseline','candidate'):
        name=r+'-rows'
        result[name]=[{'unit_id':x['unit_id'],'probability_bp':number(x['probability_bp']) if x['probability_bp'] else None} for x in tables[name]]
    result['reference-rows']=[{'unit_id':x['unit_id'],'label':boolean(x['label'])} for x in tables['reference-rows']]
    result['outcomes']=[{'unit_id':x['unit_id'],'baseline':boolean(x['baseline']),'candidate':boolean(x['candidate'])} for x in tables['outcomes']]
    for name in ('reference-counts','target-counts'):
        result[name]=counts(tables[name])
    return result


def preview(child, kind):
    schema = COHORT if kind=='cohort' else COLLECTION
    if kind=='cohort':
        require(child.prepared is None)
    else:
        require(child.prepared is not None and child.review is None and not getattr(child,'collection_committed',False))
    require(not any(protocol.selector.resume.present(child.staging/(k+'.json')) for k in schema))
    tables, hashes, total = {}, {}, 0
    for k,columns in schema.items():
        raw=setup.read_local(child.staging,k+'.csv',LIMIT-total)
        total+=len(raw); require(total<=LIMIT)
        tables[k]=parse(raw,columns); hashes[k]=hashlib.sha256(raw).hexdigest()
    values=convert(kind,tables)
    require(len(pred.canonical(values))<=setup.LIMIT)
    report=None
    if kind=='collection':
        _,_,r=child._collection(values)
        report=r['report_sha256']
    digest=protocol.setup.work.digest([kind,hashes,child.prepared,child.head,report])
    return digest,values,{k:len(v) for k,v in tables.items()}


def stage(child, kind, expected):
    digest,values,_=preview(child,kind)
    require(digest==expected)
    created=[]
    try:
        for k,v in values.items():
            path=child.staging/(k+'.json'); pred._write(path,v); created.append(path)
    except BaseException:
        for path in reversed(created): path.unlink()
        raise
    child.preview=child.collection=None


def page(child, kind, pending):
    schema=COHORT if kind=='cohort' else COLLECTION
    out=['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>CSV import</title><link rel="stylesheet" href="/style.css"></head><body><nav><a href="/">Setup</a> | <a href="/workspaces">Workspace selector</a></nav><h1>Import '+kind+' CSV</h1><p class="notice">Use approved pseudonymous exports in the private staging directory. No raw patient records.</p>',web.table(['Filename','Required header'],[(k+'.csv',','.join(v)) for k,v in schema.items()]),'<p>Blank optional values mean unknown. Use true/false for outcomes and integer basis points for probabilities. Preview checks files; confirm stages JSON without freezing or sealing the study.</p>']
    def form(action, label, approval=''):
        return '<form method="post"><input type="hidden" name="csrf" value="'+child.csrf+'"><input type="hidden" name="action" value="'+action+'"><input type="hidden" name="approval" value="'+approval+'"><button>'+label+'</button></form>'
    out.append(form('preview','Preview staged CSV'))
    if pending:
        digest,summary=pending
        out.extend([web.table(['Input','Rows'],summary.items()),'<p>Preview digest: <code>'+digest+'</code></p>',form('commit','Confirm and stage JSON',digest)])
    return (''.join(out)+'</body></html>').encode('utf-8')


class Manager(protocol.Manager):
    def page(self, message=''):
        body=super().page(message)
        if self.active and self.active.review is None:
            kind='cohort' if self.active.prepared is None else 'collection'
            body=body.replace(b'</body>',('<p><a href="/csv-'+kind+'">Import '+kind+' CSV</a></p></body>').encode(),1)
        return body

    def __call__(self,env,start):
        path=env.get('PATH_INFO')
        if path not in ('/csv-cohort','/csv-collection'):
            return super().__call__(env,start)
        kind=path[5:]
        def respond(status,body=b'',mime='text/plain; charset=utf-8',extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra])
            return [body]
        if not self.host or env.get('HTTP_HOST')!=self.host or env.get('REMOTE_ADDR')!='127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host):
            return respond('403 Forbidden')
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth):
            return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local assessment"')])
        if env.get('QUERY_STRING'): return respond('400 Bad Request')
        with self.lock:
            child=self.active
            if child is None or child.review is not None or (kind=='cohort')!=(child.prepared is None):
                return respond('409 Conflict',b'Incorrect workspace phase.')
            pending=getattr(child,'csv_pending',{}).get(kind)
            method=env.get('REQUEST_METHOD')
            if method=='GET': return respond('200 OK',page(child,kind,pending),'text/html; charset=utf-8')
            if method!='POST': return respond('405 Method Not Allowed',extra=[('Allow','GET, POST')])
            # Exact form body; do not parse paths or arbitrary values from requests.
            pre=('csrf='+child.csrf+'&action=preview&approval=').encode()
            commit=('csrf='+child.csrf+'&action=commit&approval='+pending[0]).encode() if pending else b''
            allowed=[pre]+([commit] if commit else [])
            try:
                n=int(env.get('CONTENT_LENGTH',''))
                require(n in {len(x) for x in allowed} and env.get('CONTENT_TYPE')=='application/x-www-form-urlencoded')
                raw=env['wsgi.input'].read(n)
                require(any(hmac.compare_digest(raw,x) for x in allowed))
            except (ValueError,TypeError): return respond('403 Forbidden')
            child.csv_pending={}
            try:
                if raw==pre:
                    digest,_,summary=preview(child,kind)
                    child.csv_pending={kind:(digest,summary)}
                    return respond('200 OK',page(child,kind,child.csv_pending[kind]),'text/html; charset=utf-8')
                stage(child,kind,pending[0])
                return respond('303 See Other',extra=[('Location','/')])
            except (OSError,ValueError,TypeError,KeyError,RecursionError,csv.Error,vendor.sqlite3.Error):
                return respond('409 Conflict',b'CSV import refused. Check staged files, phase and retained checkpoints.')


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
        print('Local CSV browser startup refused.'); return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
