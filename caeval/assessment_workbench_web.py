"""Loopback-only single-operator assessment report browser; no file upload routes."""
import argparse
import base64
import hmac
import html
import secrets
import sys
from threading import Lock
from wsgiref.simple_server import make_server, WSGIRequestHandler

from . import assessment_workbench as work
from . import predictive_research as pred


CSS = """body{font:16px system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;color:#172638;background:#f7f9fc}section{background:white;border:1px solid #d4dce6;border-radius:8px;padding:1rem;margin:1rem 0}h1,h2{line-height:1.2}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:.6rem;border-bottom:1px solid #ddd}button{padding:.65rem 1rem;background:#17456c;color:white;border:0;border-radius:5px;font:inherit;cursor:pointer}form{display:inline-block;margin:.3rem .5rem .3rem 0}code{overflow-wrap:anywhere} .notice{border-left:4px solid #a36913;padding:.8rem;background:#fff7e6}button:disabled{opacity:.5;cursor:default}"""


def esc(x):
    return html.escape(str(x), quote=True)


def table(headers, rows):
    return '<table><thead><tr>' + ''.join('<th scope="col">'+esc(x)+'</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in row)+'</tr>' for row in rows) + '</tbody></table>'


def bounds(x):
    return ' to '.join('unknown' if v is None else format(v, '.4g') for v in x)


class Operator:
    def __init__(self, plan_path, expected_plan, config_path):
        if not pred.hex64(expected_plan):
            raise ValueError('independent plan checkpoint required')
        self.plan_path, self.expected_plan, self.config_path = plan_path, expected_plan, config_path
        self.config_digest = work.digest(pred._read(config_path))
        self.password = secrets.token_urlsafe(32)
        self.auth = 'Basic ' + base64.b64encode(('operator:'+self.password).encode('ascii')).decode('ascii')
        self.csrf = secrets.token_hex(32)
        self.report = None
        self.verified = False
        self.lock = Lock()
        self.host = None

    def inputs(self):
        cfg = pred._read(self.config_path)
        if work.digest(cfg) != self.config_digest:
            raise ValueError('restart required for changed operator config')
        return pred._read(self.plan_path), work._inputs(cfg)

    def page(self, message=''):
        def form(path, label, disabled=False):
            return '<form method="post" action="/'+path+'"><input type="hidden" name="csrf" value="'+self.csrf+'"><button'+(' disabled' if disabled else '')+'>'+label+'</button></form>'
        out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Local assessment workbench</title><link rel="stylesheet" href="/style.css"></head><body>',
               '<h1>Local hospital assessment</h1><p class="notice">Experimental, restricted evidence. Not a clinical decision, deployment approval or publication clearance.</p>',
               '<section><h2>Operator actions</h2>'+form('run','Run assessment')+form('verify','Replay and verify',self.report is None)+form('download','Download verified JSON',not self.verified),
               '<p>Replay checks the configured local inputs. Download may place restricted data in the browser download directory.</p><p role="status">'+esc(message)+'</p></section>']
        r = self.report
        if r is not None:
            s, p, b = r['summary'], r['reports']['predictive'], r['reports']['binary']
            out.extend(['<section><h2>Assessment scope</h2>',table(['Field','Value'],[(k,v) for k,v in r['scope'].items()]+[('Study',r['study']),('Eligible units',r['n_planned']),('Prediction endpoint',r['endpoints']['predictive']),('Adverse outcome endpoint',r['endpoints']['binary'])]),'</section>',
                '<section><h2>Procurement evidence</h2>',table(['Item','Status'],[('Missing mandatory documents',', '.join(s['mandatory_missing']) or 'None recorded'),('Unresolved clarifications',s['unresolved_clarifications']),('Overdue clarifications',s['overdue_clarifications'])]),'<p>Document coverage does not establish evidence sufficiency.</p></section>',
                '<section><h2>Predictive evaluation</h2>',table(['Source','Known','Unknown'],[(k,v['n_known'],v['n_unknown']) for k,v in s['predictive_coverage'].items()]),'<p>Paired candidate minus baseline Brier bounds: '+esc(bounds(p['calibration']['paired']['cohort_brier_delta_bounds']))+'</p>',table(['Threshold (bp)','Paired net benefit bounds'],[(t['threshold_bp'],bounds(t['candidate_minus']['baseline']['cohort_delta_bounds'])) for t in p['decision_curve']['thresholds']]),'</section>',
                '<section><h2>Adverse outcomes and population shift</h2><p>Unit-weighted adverse outcome difference bounds: '+esc(bounds(b['multisite']['unit_weighted']['cohort_delta_bounds']))+'</p>',table(['Feature','Flag'],[(f['feature'],f['status']) for f in s['population_shift_flags']]),'<p>Finite-cohort bounds are not confidence intervals; marginal similarity is not proof of transportability.</p></section>',
                '<section><h2>Evidence checkpoints</h2><p>Replay: '+('verified against configured inputs' if self.verified else 'not verified in this session')+'</p><p>Retain report digest independently: <code>'+esc(r['report_sha256'])+'</code></p></section>'])
        out.append('</body></html>')
        return ''.join(out).encode('utf-8')

    def __call__(self, env, start):
        def respond(status, body=b'', mime='text/plain; charset=utf-8', extra=()):
            start(status, [('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('Pragma','no-cache'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra])
            return [body]
        if not self.host or env.get('HTTP_HOST') != self.host or env.get('REMOTE_ADDR') != '127.0.0.1':
            return respond('403 Forbidden')
        if env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host):
            return respond('403 Forbidden')
        supplied = env.get('HTTP_AUTHORIZATION','')
        if not isinstance(supplied,str) or not supplied.isascii() or not hmac.compare_digest(supplied,self.auth):
            return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local assessment"')])
        path, method = env.get('PATH_INFO'),env.get('REQUEST_METHOD')
        if env.get('QUERY_STRING'):
            return respond('400 Bad Request')
        with self.lock:
            if method == 'GET' and path == '/':
                return respond('200 OK',self.page(),'text/html; charset=utf-8')
            if method == 'GET' and path == '/style.css':
                return respond('200 OK',CSS.encode(),'text/css; charset=utf-8')
            if path not in ('/run','/verify','/download'):
                return respond('404 Not Found')
            if method != 'POST':
                return respond('405 Method Not Allowed',extra=[('Allow','POST')])
            expected_body = ('csrf='+self.csrf).encode('ascii')
            if env.get('CONTENT_TYPE') != 'application/x-www-form-urlencoded' or env.get('CONTENT_LENGTH') != str(len(expected_body)) or not hmac.compare_digest(env['wsgi.input'].read(len(expected_body)),expected_body):
                return respond('403 Forbidden')
            if path == '/download':
                if not self.verified or self.report is None:
                    return respond('409 Conflict',b'Replay verification required.')
                try:
                    plan, inputs = self.inputs()
                    if not work.verify(plan,self.expected_plan,self.report,self.report['report_sha256'],**inputs):
                        raise ValueError('replay mismatch')
                    return respond('200 OK',pred.canonical(self.report)+b'\n','application/json', [('Content-Disposition','attachment; filename="assessment-report.json"')])
                except Exception:
                    self.verified = False
                    self.report = None
                    return respond('409 Conflict',self.page('Operation refused. Check local files and retained checkpoints.'),'text/html; charset=utf-8')
            self.verified = False
            try:
                plan, inputs = self.inputs()
                if path == '/run':
                    self.report = None
                    result = work.analyze(plan,self.expected_plan,**inputs)
                    if len(pred.canonical(result)) >= pred.LIMIT:
                        raise ValueError('report exceeds limit')
                    self.report = result
                    message = 'Assessment generated. Replay before downloading.'
                else:
                    if self.report is None or not work.verify(plan,self.expected_plan,self.report,self.report['report_sha256'],**inputs):
                        raise ValueError('replay mismatch')
                    self.verified = True
                    message = 'Replay verified against configured inputs. Not independent source verification.'
                return respond('200 OK',self.page(message),'text/html; charset=utf-8')
            except Exception:
                # Local file paths, source content and credentials must not reach the browser or logs.
                self.report = None
                return respond('409 Conflict',self.page('Operation refused. Check local files and retained checkpoints.'),'text/html; charset=utf-8')


class QuietHandler(WSGIRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, format, *args):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('plan','expected-plan','config'):
        parser.add_argument('--'+key,required=True)
    a = parser.parse_args(argv)
    try:
        app = Operator(a.plan,a.expected_plan,a.config)
        with make_server('127.0.0.1',0,app,handler_class=QuietHandler) as server:
            app.host = '127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+' in a local browser. Username: operator',flush=True)
            print('Session password: '+app.password,flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Local operator startup refused.',file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
