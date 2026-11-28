"""Private source-bound clinician decisions for the randomized assisted trial."""
import argparse
import base64
import hmac
import json
import secrets
from threading import Lock
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server

from . import predictive_assisted_trial as trial, predictive_research as pred
from . import predictive_reference_review_web as ref, assessment_setup_web as storage
from . import assessment_workbench_web as web, risk_probability as risk

ACTIONS = {'act': True, 'do_not_act': False, 'unknown': None}


def require(ok):
    if not ok:
        raise ValueError('assisted reader scope or source mismatch')


def _json(directory, name):
    return json.loads(storage.read_local(directory, name), object_pairs_hook=pred._unique)


def _texts(values, inventory):
    require(isinstance(values, dict) and set(values) == set(inventory))
    for unit, text in values.items():
        require(isinstance(text, str))
        raw = text.encode('utf-8')
        require(0 < len(raw) <= ref.MAX_TEXT and ref.sha(raw) == inventory[unit])
    return values


def load(directory, expected, clinician):
    d = storage.private_directory(directory)
    packet = _json(d, 'packet.json')
    pred.fields(packet, 'schema allocation_sha256 clinician decision_protocol_sha256 assistance_protocol_sha256 items packet_sha256')
    require(packet['schema'] == trial.SCHEMA and risk._token(clinician) and packet['clinician'] == clinician)
    require(all(pred.hex64(packet[k]) for k in ('allocation_sha256','decision_protocol_sha256','assistance_protocol_sha256')))
    require(pred.hex64(expected) and packet['packet_sha256'] == expected and pred.digest({k:v for k,v in packet.items() if k != 'packet_sha256'}) == expected)
    items = packet['items']; require(isinstance(items, list) and 1 <= len(items) <= risk.MAX_UNITS)
    decisions, assistance = {}, {}
    for row in items:
        pred.fields(row, 'unit_id arm input_sha256 assistance_sha256')
        u = row['unit_id']; require(risk._token(u) and u not in decisions and pred.hex64(row['input_sha256']))
        decisions[u] = row['input_sha256']
        require(row['arm'] in ('assisted','unassisted'))
        if row['arm'] == 'assisted':
            require(pred.hex64(row['assistance_sha256'])); assistance[u] = row['assistance_sha256']
        else:
            require(row['assistance_sha256'] is None)
    require(list(decisions) == sorted(decisions) and 0 < len(assistance) < len(decisions))
    protocols = []
    for name in ('decision','assistance'):
        raw = storage.read_local(d, name+'-protocol.txt', ref.MAX_TEXT)
        require(bool(raw) and ref.sha(raw) == packet[name+'_protocol_sha256'])
        protocols.append(raw.decode('utf-8'))
    return (packet, *protocols, _texts(_json(d,'decisions.json'), decisions), _texts(_json(d,'assistance.json'), assistance))


def export(*args, allocation, allocation_digest, clinician, source_directory, output_root):
    """Export only the assigned material; never export the trial or reference files."""
    packet = trial.packet(*args, allocation=allocation, allocation_digest=allocation_digest, clinician=clinician)
    source = storage.private_directory(source_directory); root = storage.private_directory(output_root)
    require(source != root and source not in root.parents and root not in source.parents)
    spec = args[-2]['spec']
    decisions = _texts(_json(source,'decisions.json'), spec['decision_input_sha256'])
    assistance = _texts(_json(source,'assistance.json'), spec['assistance_input_sha256'])
    subset = {r['unit_id']:assistance[r['unit_id']] for r in packet['items'] if r['arm']=='assisted'}
    target = root/('reader-'+clinician)
    target.mkdir(mode=0o700, exist_ok=False)
    try:
        for name, value in [('packet.json',packet),('decisions.json',decisions),('assistance.json',subset)]:
            pred._write(target/name,value)
        import os
        for name in ('decision','assistance'):
            raw = storage.read_local(source,name+'-protocol.txt',ref.MAX_TEXT)
            fd=os.open(target/(name+'-protocol.txt'),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as f: f.write(raw)
        load(target,packet['packet_sha256'],clinician)
    except BaseException:
        for name in ('packet.json','decisions.json','assistance.json','decision-protocol.txt','assistance-protocol.txt'):
            (target/name).unlink(missing_ok=True)
        target.rmdir(); raise
    return {'packet_sha256':packet['packet_sha256'],'n_cases':len(decisions),'n_assisted':len(subset)}


class Reader:
    def __init__(self,directory,expected,clinician):
        self.directory=storage.private_directory(directory); self.expected=expected; self.clinician=clinician
        self.material=load(self.directory,expected,clinician)
        self.output=self.directory/'submission.json'
        require(not self.output.exists() and not self.output.is_symlink())
        self.rows=[]; self.result=None; self.host=None; self.lock=Lock()
        self.password=secrets.token_urlsafe(32); self.csrf=secrets.token_hex(32)
        self.auth='Basic '+base64.b64encode(('reader:'+self.password).encode('ascii')).decode('ascii')

    def unchanged(self):
        require(load(self.directory,self.expected,self.clinician)==self.material)

    def page(self):
        p,dp,ap,dec,assist=self.material; e=web.esc; i=len(self.rows)
        out=['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Assisted trial decisions</title><link rel="stylesheet" href="/style.css"></head><body><h1>Research decision collection</h1><p>Not for clinical care. Do not access reference outcomes, other readers or assistance for control cases. Record unknown when unable to decide.</p><p>'+str(i)+' of '+str(len(p['items']))+' recorded</p>']
        if self.result is not None:
            out.append('<p>Sealed submission: <code>'+e(self.result['submission_sha256'])+'</code></p>')
        else:
            hidden='<input type="hidden" name="csrf" value="'+self.csrf+'"><input type="hidden" name="revision" value="'+str(i)+'">'
            if i<len(p['items']):
                row=p['items'][i]; u=row['unit_id']; out.append('<h2>'+e(u)+' — '+e(row['arm'])+'</h2><h3>Decision protocol</h3><pre>'+e(dp)+'</pre><h3>Decision-time source</h3><pre>'+e(dec[u])+'</pre>')
                if row['arm']=='assisted': out.append('<h3>Assistance protocol</h3><pre>'+e(ap)+'</pre><h3>Assigned AI assistance</h3><pre>'+e(assist[u])+'</pre>')
                out.append('<form method="post" action="/rate">'+hidden)
                for key,label in [('act','Perform protocol action'),('do_not_act','Do not perform action'),('unknown','Unknown')]: out.append('<button name="action" value="'+key+'">'+label+'</button> ')
                out.append('</form>')
            else: out.append('<p>All assigned decisions recorded. Sealing is final.</p><form method="post" action="/seal">'+hidden+'<button>Seal decisions</button></form>')
        return (''.join(out)+'</body></html>').encode('utf-8')

    def __call__(self,env,start):
        def respond(status,body=b'',mime='text/plain; charset=utf-8',extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra]); return [body]
        if not self.host or env.get('HTTP_HOST')!=self.host or env.get('REMOTE_ADDR')!='127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host): return respond('403 Forbidden')
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth): return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local trial review"')])
        if env.get('QUERY_STRING'): return respond('400 Bad Request')
        path,method=env.get('PATH_INFO'),env.get('REQUEST_METHOD')
        with self.lock:
            if method=='GET' and path=='/style.css': return respond('200 OK',(web.CSS+'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}').encode(),'text/css; charset=utf-8')
            if method=='GET' and path=='/':
                try: self.unchanged(); return respond('200 OK',self.page(),'text/html; charset=utf-8')
                except (OSError,ValueError,TypeError,KeyError,RecursionError): return respond('409 Conflict')
            if path not in ('/rate','/seal'): return respond('404 Not Found')
            if method!='POST': return respond('405 Method Not Allowed',extra=[('Allow','POST')])
            try:
                n=int(env.get('CONTENT_LENGTH','')); require(1<=n<=1024 and env.get('CONTENT_TYPE')=='application/x-www-form-urlencoded')
                raw=env['wsgi.input'].read(n); require(len(raw)==n)
                pairs=parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=3,errors='strict'); fields=dict(pairs); require(len(pairs)==len(fields))
                pred.fields(fields,'csrf revision action' if path=='/rate' else 'csrf revision'); require(fields['csrf'].isascii() and hmac.compare_digest(fields['csrf'],self.csrf))
            except (ValueError,TypeError,UnicodeError): return respond('403 Forbidden')
            try:
                self.unchanged(); p=self.material[0]; i=len(self.rows); require(self.result is None and fields['revision']==str(i))
                if path=='/rate':
                    require(i<len(p['items']) and fields['action'] in ACTIONS); self.rows.append({**p['items'][i],'action':ACTIONS[fields['action']]})
                else:
                    require(i==len(p['items']))
                    receipt=pred.digest(['local-declared-trial-decisions/1',self.expected,self.rows])
                    body={'schema':trial.SCHEMA,'packet_sha256':self.expected,'clinician':self.clinician,'source_receipt_sha256':receipt,'rows':self.rows}
                    result={**body,'submission_sha256':pred.digest(body)}; pred._write(self.output,result); self.result=result
                return respond('303 See Other',extra=[('Location','/')])
            except (OSError,ValueError,TypeError,KeyError,RecursionError): return respond('409 Conflict',b'Action refused. Check approved files and retained packet.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for k in ('directory','expected-packet','clinician'): parser.add_argument('--'+k,required=True)
    a=parser.parse_args(argv)
    try:
        app=Reader(a.directory,a.expected_packet,a.clinician)
        with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
            app.host='127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+' in a local browser. Username: reader',flush=True)
            print('Session password: '+app.password,flush=True); server.serve_forever()
    except KeyboardInterrupt: return 0
    except (OSError,ValueError,TypeError,KeyError,RecursionError): print('Local trial review refused.'); return 2
    return 0


if __name__=='__main__':
    raise SystemExit(main())
