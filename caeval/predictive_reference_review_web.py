"""Private source-bound browser for one declared predictive reference reader."""
import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from threading import Lock
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server

from . import predictive_reference_panel as panel, predictive_research as pred
from . import assessment_setup_web as storage, assessment_workbench_web as web
from . import risk_probability as risk

LIMIT = 16 * 1024 * 1024
MAX_TEXT = 64 * 1024
LABELS = {'present': True, 'absent': False, 'unknown': None}


def require(ok):
    if not ok:
        raise ValueError('review packet, source or checkpoint mismatch')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(directory, expected_packet, reader):
    p = json.loads(storage.read_local(directory, 'packet.json', LIMIT), object_pairs_hook=pred._unique)
    keys = 'schema panel_sha256 role reader reference_protocol_sha256 endpoint horizon items packet_sha256'
    if p.get('role') == 'adjudicator':
        keys += ' primary_digests'
        pred.fields(p.get('primary_digests'), 'reader_a reader_b')
        require(all(pred.hex64(v) for v in p['primary_digests'].values()))
    pred.fields(p, keys)
    require(p['schema'] == panel.SCHEMA and p['role'] in panel.ROLES and risk._token(reader) and p['reader'] == reader)
    require(all(pred.hex64(p[k]) for k in ('panel_sha256','reference_protocol_sha256','packet_sha256')))
    require(risk._token(p['endpoint']) and risk._token(p['horizon']))
    require(pred.hex64(expected_packet) and p['packet_sha256'] == expected_packet and pred.digest({k:v for k,v in p.items() if k != 'packet_sha256'}) == expected_packet)
    require(isinstance(p['items'], list) and len(p['items']) <= panel.MAX_UNITS)
    ids = []
    for r in p['items']:
        pred.fields(r, 'unit_id source_sha256')
        require(risk._token(r['unit_id']) and pred.hex64(r['source_sha256']))
        ids.append(r['unit_id'])
    require(ids == sorted(set(ids)) and (bool(ids) or p['role'] == 'adjudicator'))
    protocol = storage.read_local(directory, 'protocol.txt', MAX_TEXT)
    require(bool(protocol) and sha(protocol) == p['reference_protocol_sha256'])
    sources = json.loads(storage.read_local(directory, 'sources.json', LIMIT), object_pairs_hook=pred._unique)
    require(isinstance(sources, dict) and set(sources) == set(ids))
    for item in p['items']:
        text = sources[item['unit_id']]
        require(isinstance(text, str))
        raw = text.encode('utf-8')
        require(0 < len(raw) <= MAX_TEXT and sha(raw) == item['source_sha256'])
    return p, protocol.decode('utf-8'), sources


class Reviewer:
    def __init__(self, directory, expected_packet, reader):
        self.directory = storage.private_directory(directory)
        self.output = self.directory/'submission.json'
        require(not self.output.exists() and not self.output.is_symlink())
        self.packet, self.protocol, self.sources = load(self.directory, expected_packet, reader)
        self.reader = reader
        self.expected_packet = expected_packet
        self.rows = []
        self.result = None
        self.password = secrets.token_urlsafe(32)
        self.auth = 'Basic '+base64.b64encode(('reviewer:'+self.password).encode('ascii')).decode('ascii')
        self.csrf = secrets.token_hex(32)
        self.host = None
        self.lock = Lock()

    def unchanged(self):
        require(load(self.directory, self.expected_packet, self.reader) == (self.packet, self.protocol, self.sources))

    def page(self, message=''):
        e = web.esc
        n = len(self.packet['items']); i = len(self.rows)
        out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Independent reference review</title><link rel="stylesheet" href="/style.css"></head><body><h1>Independent reference review</h1>',
               '<p class="notice">Use the approved protocol and source only. No model predictions or other reader ratings are supplied by this interface. Declare unknown if evidence is insufficient. This is not a clinical care tool.</p>',
               '<p>Reader: '+e(self.reader)+' | Endpoint: '+e(self.packet['endpoint'])+' | Horizon: '+e(self.packet['horizon'])+'</p><p role="status">'+e(message)+'</p><p>Recorded '+str(i)+' of '+str(n)+'</p>']
        if self.result is not None:
            out.append('<p>Submission saved. Retain independently: <code>'+e(self.result['submission_sha256'])+'</code></p>')
        else:
            out.append('<section><h2>Approved reference protocol</h2><pre>'+e(self.protocol)+'</pre></section>')
            hidden = '<input type="hidden" name="csrf" value="'+self.csrf+'"><input type="hidden" name="revision" value="'+str(i)+'">'
            if i < n:
                unit = self.packet['items'][i]['unit_id']
                out.append('<section><h2>Unit '+e(unit)+'</h2><pre>'+e(self.sources[unit])+'</pre><form method="post" action="/rate">'+hidden)
                for key, label in [('present','Outcome present'),('absent','Outcome absent'),('unknown','Insufficient evidence / unknown')]:
                    out.append('<button name="label" value="'+key+'">'+label+'</button> ')
                out.append('</form></section>')
            else:
                out.append(web.table(['Rating','Count'],[(k,sum(r['label'] is v for r in self.rows)) for k,v in LABELS.items()]))
                out.append('<p>Confirm all ratings. Sealing creates an exclusive submission file; it cannot be edited in this session.</p><form method="post" action="/seal">'+hidden+'<button>Seal independent submission</button></form>')
        return (''.join(out)+'</body></html>').encode('utf-8')

    def __call__(self, env, start):
        def respond(status, body=b'', mime='text/plain; charset=utf-8', extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra])
            return [body]
        if not self.host or env.get('HTTP_HOST') != self.host or env.get('REMOTE_ADDR') != '127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host):
            return respond('403 Forbidden')
        auth = env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth):
            return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local reference review"')])
        if env.get('QUERY_STRING'):
            return respond('400 Bad Request')
        path, method = env.get('PATH_INFO'),env.get('REQUEST_METHOD')
        with self.lock:
            if method == 'GET' and path == '/style.css':
                return respond('200 OK',(web.CSS+'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}').encode(),'text/css; charset=utf-8')
            if method == 'GET' and path == '/':
                try:
                    self.unchanged()
                    return respond('200 OK',self.page(),'text/html; charset=utf-8')
                except (OSError,ValueError,TypeError,KeyError,RecursionError):
                    return respond('409 Conflict',b'Approved files changed. Stop review.')
            if path not in ('/rate','/seal'):
                return respond('404 Not Found')
            if method != 'POST':
                return respond('405 Method Not Allowed',extra=[('Allow','POST')])
            try:
                n = int(env.get('CONTENT_LENGTH',''))
                require(1 <= n <= 1024 and env.get('CONTENT_TYPE') == 'application/x-www-form-urlencoded')
                raw = env['wsgi.input'].read(n); require(len(raw)==n)
                pairs = parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=3,errors='strict')
                fields = dict(pairs); require(len(pairs)==len(fields))
                pred.fields(fields,'csrf revision label' if path=='/rate' else 'csrf revision')
                require(fields['csrf'].isascii() and hmac.compare_digest(fields['csrf'],self.csrf))
            except (ValueError,TypeError,UnicodeError):
                return respond('403 Forbidden')
            try:
                self.unchanged()
                require(self.result is None and fields['revision']==str(len(self.rows)))
                if path == '/rate':
                    require(len(self.rows)<len(self.packet['items']) and fields['label'] in LABELS)
                    self.rows.append({**self.packet['items'][len(self.rows)],'label':LABELS[fields['label']]})
                else:
                    require(len(self.rows)==len(self.packet['items']))
                    result = panel.seal(self.packet,self.expected_packet,self.reader,self.rows)
                    pred._write(self.output,result); self.result=result
                return respond('303 See Other',extra=[('Location','/')])
            except (OSError,ValueError,TypeError,KeyError,RecursionError):
                return respond('409 Conflict',b'Review action refused. Check approved files and retained packet.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for k in ('directory','expected-packet','reader'):
        parser.add_argument('--'+k,required=True)
    a=parser.parse_args(argv)
    try:
        app=Reviewer(a.directory,a.expected_packet,a.reader)
        with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
            app.host='127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+' in a local browser. Username: reviewer',flush=True)
            print('Session password: '+app.password,flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        print('Local reference review refused. Check private files and retained packet.')
        return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
