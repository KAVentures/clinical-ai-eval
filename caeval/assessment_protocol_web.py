"""Native prospective protocol forms for the private workspace selector."""
import argparse
import hmac
import re
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server

from . import assessment_workspace_web as selector, assessment_setup_web as setup
from . import assessment_workbench_web as web, predictive_research as pred
from . import prospective_research as binary

FIELDS = {
    'request_id': 'Buyer request ID', 'buyer': 'Buyer', 'vendor': 'Vendor',
    'product': 'Product', 'candidate_version': 'Candidate version',
    'requester': 'Buyer operator token', 'reviewer': 'Buyer reviewer token',
    'representative': 'Vendor representative token', 'study': 'Study ID',
    'baseline_version': 'Baseline version', 'eligibility_sha256': 'Eligibility protocol SHA-256',
    'reference_protocol_sha256': 'Reference protocol SHA-256',
    'prediction_endpoint': 'Prediction endpoint', 'prediction_horizon': 'Prediction horizon',
    'action': 'Clinical action token', 'bin_edges_bp': 'Calibration bin edges (comma-separated bp)',
    'thresholds_bp': 'Decision thresholds (comma-separated bp)', 'binary_endpoint': 'Adverse-event endpoint',
    'binary_horizon': 'Adverse-event horizon', 'reference_protocol': 'Binary reference protocol token',
    'holdout_design': 'Holdout design', 'subgroup_levels': 'Subgroup tokens (comma-separated)',
    'minimum_group': 'Minimum planned group size', 'coding_version': 'Population coding version',
    'requirements': 'Evidence requirements: one line ID | mandatory/optional | description',
    'shift_features': 'Population features: one line ID | warning bp | comma-separated categories',
    'iid': 'Independent fixed-sample Bernoulli design', 'unacceptable_rate': 'Unacceptable event probability',
    'anticipated_rate': 'Anticipated event probability', 'alpha': 'One-sided alpha',
}
MAX_BODY = 128 * 1024
WRITTEN = ('request-spec', 'actors', 'predictive-spec', 'binary-spec')


def require(ok):
    if not ok:
        raise ValueError('invalid protocol form or staged inventory')


def items(value):
    values = [x.strip() for x in value.split(',')]
    require(all(values))
    return values


def integer(value):
    require(re.fullmatch(r'[0-9]{1,9}', value) is not None)
    return int(value)


def rows(value):
    result = []
    for line in value.splitlines():
        if line.strip():
            parts = [x.strip() for x in line.split('|')]
            require(len(parts) == 3 and all(parts))
            result.append(parts)
    require(bool(result))
    return result


def build(fields, manifest, memberships):
    require(set(fields) == set(FIELDS) and all(isinstance(v, str) and len(v) <= 50000 for v in fields.values()))
    f = {k:v.strip() for k,v in fields.items()}
    reqs = []
    for name, status, desc in rows(f['requirements']):
        require(status in ('mandatory','optional'))
        reqs.append({'id':name,'mandatory':status=='mandatory','description':desc})
    common = {k:f[k] for k in ('study','baseline_version','candidate_version')}
    ids = sorted(r['record_id'] for r in binary.data.validate(manifest) if r['partition']=='test')
    require(f['iid'] in ('no','yes'))
    rates = ('unacceptable_rate','anticipated_rate','alpha')
    d = None
    if f['iid']=='yes':
        require(all(re.fullmatch(r'(?:0(?:\.[0-9]{1,12})?|1(?:\.0{1,12})?)',f[k]) for k in rates))
        d = {'iid_assumed':True, **{k:float(f[k]) for k in rates}}
    else:
        require(not any(f[k] for k in rates))
    values = {'request-spec':{**{k:f[k] for k in ('request_id','buyer','vendor','product')},'version':f['candidate_version'],'requirements':reqs},
              'actors':{k:f[k] for k in ('requester','reviewer','representative')},
              'predictive-spec':{**common, **{k:f[k] for k in ('eligibility_sha256','reference_protocol_sha256','action')},
                  'endpoint':f['prediction_endpoint'],'horizon':f['prediction_horizon'],'unit_ids':ids,
                  'bin_edges_bp':[integer(x) for x in items(f['bin_edges_bp'])],'thresholds_bp':[integer(x) for x in items(f['thresholds_bp'])]},
              'binary-spec':{**common, **{k:f[k] for k in ('reference_protocol','holdout_design','coding_version')},
                  'endpoint':f['binary_endpoint'],'horizon':f['binary_horizon'],'subgroup_levels':items(f['subgroup_levels']),
                  'minimum_group':integer(f['minimum_group']),'shift_features':[{'feature':name,'warning_bp':integer(bp),'levels':items(levels)} for name,bp,levels in rows(f['shift_features'])], 'binary_design':d},
              'manifest':manifest,'memberships':memberships}
    # Validate with the actual prospective engines; discard temporary keys and plans.
    setup.prepare(values)
    return values


def stage(child, fields):
    require(child.prepared is None and not selector.resume.present(child.destination))
    child.preview = None
    require(not any(selector.resume.present(child.staging/(k+'.json')) for k in WRITTEN))
    cohort = setup.staged(child.staging, ('manifest','memberships'))
    values = build(fields, cohort['manifest'], cohort['memberships'])
    created = []
    try:
        for k in WRITTEN:
            path = child.staging/(k+'.json')
            pred._write(path, values[k]); created.append(path)
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise


def page(child):
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Study protocol</title><link rel="stylesheet" href="/style.css"></head><body><nav><a href="/workspaces">Workspace selector</a> | <a href="/">Setup</a></nav><h1>Configure prospective study</h1><p class="notice">Stage manifest.json and memberships.json first. Do not inspect outcomes before freezing. This form stages specifications only; review and freeze separately.</p><form method="post" action="/protocol"><input type="hidden" name="csrf" value="'+child.csrf+'">']
    for k,label in FIELDS.items():
        out.append('<p><label>'+web.esc(label)+' ')
        if k in ('requirements','shift_features'):
            out.append('<textarea name="'+k+'" rows="5" cols="60" required></textarea>')
        elif k in ('holdout_design','iid'):
            options = ('grouped','temporal','site','temporal_site') if k=='holdout_design' else ('no','yes')
            out.append('<select name="'+k+'">'+''.join('<option>'+v+'</option>' for v in options)+'</select>')
        else:
            out.append('<input name="'+k+'" maxlength="1024"'+('' if k in ('unacceptable_rate','anticipated_rate','alpha') else ' required')+'>')
        out.append('</label></p>')
    out.append('<p>Leave probability fields empty unless the IID design is explicitly justified. No suggested clinical thresholds are supplied.</p><button>Validate and stage specifications</button></form></body></html>')
    return ''.join(out).encode('utf-8')


class Manager(selector.Manager):
    def page(self, message=''):
        body = super().page(message)
        if self.active is not None and self.active.prepared is None:
            body = body.replace(b'</body>',b'<p><a href="/protocol">Configure prospective protocol in browser</a></p></body>',1)
        return body

    def __call__(self, env, start):
        if env.get('PATH_INFO') != '/protocol':
            return super().__call__(env,start)
        def respond(status, body=b'', mime='text/plain; charset=utf-8', extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra])
            return [body]
        if not self.host or env.get('HTTP_HOST') != self.host or env.get('REMOTE_ADDR') != '127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host):
            return respond('403 Forbidden')
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth):
            return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local assessment"')])
        if env.get('QUERY_STRING'):
            return respond('400 Bad Request')
        with self.lock:
            child=self.active
            if child is None or child.prepared is not None:
                return respond('409 Conflict',b'Choose a new, unfrozen workspace.')
            if env.get('REQUEST_METHOD')=='GET':
                return respond('200 OK',page(child),'text/html; charset=utf-8')
            if env.get('REQUEST_METHOD')!='POST':
                return respond('405 Method Not Allowed',extra=[('Allow','GET, POST')])
            try:
                n=int(env.get('CONTENT_LENGTH',''))
                require(1<=n<=MAX_BODY and env.get('CONTENT_TYPE')=='application/x-www-form-urlencoded')
                raw=env['wsgi.input'].read(n); require(len(raw)==n)
                pairs=parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=len(FIELDS)+1,errors='strict')
                fields=dict(pairs); require(len(pairs)==len(fields)==len(FIELDS)+1 and set(fields)==set(FIELDS)|{'csrf'})
                token=fields.pop('csrf'); require(token.isascii() and hmac.compare_digest(token,child.csrf))
            except (ValueError,TypeError,UnicodeError):
                return respond('403 Forbidden')
            try:
                stage(child,fields)
            except (OSError,ValueError,TypeError,KeyError,RecursionError):
                return respond('409 Conflict',b'Protocol refused. Check staged cohort, input fields and existing specifications.')
            return respond('303 See Other',extra=[('Location','/')])


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
    except KeyboardInterrupt:
        return 0
    except (OSError,ValueError,TypeError):
        print('Local protocol browser startup refused.')
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
