"""Private browser workflow for a replayed aggregate procurement dossier."""
import argparse
import hmac
import re
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server

from . import predictive_worker_web as parent, assessment_research_dossier as dossier
from . import assessment_resume_web as resume, assessment_setup_web as storage
from . import assessment_workbench_web as web, predictive_research as pred
from . import vendor_workbench as vendor

POLICY = 'research-dossier-policy'
RESULT = 'research-dossier'
TRIAL = 'assisted-trial-package'
POLICY_FIELDS = {'baseline', 'candidate', 'reference', 'trial_minimum', 'trial_required'}
BUILD_FIELDS = {'policy', 'head', 'report', 'trial', 'approval'}


def require(ok):
    if not ok:
        raise ValueError('dossier operation refused')


def policy_preview(child, fields):
    require(child.prepared is not None and child.review is None and not any(resume.present(child.destination/(n+'.json')) for n in resume.COLLECTION))
    require(not resume.present(child.destination/(POLICY+'.json')))
    require(all(re.fullmatch(r'[0-9]{1,5}', fields[k]) for k in POLICY_FIELDS-{'trial_required'}))
    require(fields['trial_required'] in ('yes','no'))
    plan, _ = resume.base_inputs(child.destination, child.prepared['plan_sha256'], child.head)
    return dossier.freeze(plan, plan['plan_sha256'], {'minimum_coverage_bp': {r:int(fields[r]) for r in pred.ROLES},
        'require_assisted_trial':fields['trial_required']=='yes','minimum_trial_coverage_bp':int(fields['trial_minimum'])})


def build(child, fields):
    require(child.prepared is not None and child.review is not None)
    require(all(pred.hex64(fields[k]) for k in ('policy','head','report')) and fields['head']==child.head)
    require(not fields['trial'] or pred.hex64(fields['trial']))
    directory = storage.private_directory(child.destination)
    plan, inputs, _ = resume.complete_inputs(directory, child.prepared['plan_sha256'], fields['head'], fields['report'])
    policy = resume.read(directory,POLICY)
    has_trial = resume.present(child.staging/(TRIAL+'.json'))
    require(has_trial == bool(fields['trial']))
    trial = resume.read(child.staging,TRIAL) if has_trial else None
    return dossier.build(plan, plan['plan_sha256'], policy, fields['policy'], fields['report'], inputs,
                         trial_package=trial, expected_trial=fields['trial'] or None)


def action(child, fields):
    name=fields['action']; approval=fields['approval']
    if name in ('preview_policy','freeze_policy'):
        result=policy_preview(child,fields)
        if name=='freeze_policy':
            require(approval==result['policy_sha256'])
            pred._write(child.destination/(POLICY+'.json'),result)
        else: require(not approval)
        return result
    result=build(child,fields)
    if name=='preview':
        require(not approval)
    else:
        require(approval==result['dossier_sha256'])
        if name=='seal':
            pred._write(child.destination/(RESULT+'.json'),result)
        else:
            require(name in ('verify','download'))
            require(pred.canonical(resume.read(child.destination,RESULT))==pred.canonical(result))
    return result


def page(child, result=None, fields=None):
    e=web.esc
    fields=fields or {}
    def field(key,label,value='',kind='text'):
        return '<p><label>'+label+' <input name="'+key+'" type="'+kind+'" maxlength="64" value="'+e(value)+'" autocomplete="off"></label></p>'
    out=['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Procurement dossier</title><link rel="stylesheet" href="/style.css"></head><body><nav><a href="/workspaces">Workspace selector</a></nav><h1>Research evidence dossier</h1><p class="notice">Restricted administrative evidence summary. No clinical or purchasing approval. Independently retain policy, report and dossier checkpoints.</p>']
    token='<input type="hidden" name="csrf" value="'+child.csrf+'">'
    if child.review is None:
        if resume.present(child.destination/(POLICY+'.json')):
            out.append('<p>Policy file exists. Retain its independently approved digest. Complete and seal the assessment before building a dossier.</p>')
        else:
            out.append('<h2>Freeze evidence coverage policy before collection sealing</h2><p>Enter independently approved coverage minima in basis points (0–10000). No suggested clinical thresholds are supplied.</p><form method="post">'+token)
            for k,label in [('baseline','Baseline minimum'),('candidate','Candidate minimum'),('reference','Reference minimum'),('trial_minimum','Assisted trial minimum')]:
                out.append(field(k,label,fields.get(k,''),'number'))
            yes=' selected' if fields.get('trial_required')=='yes' else ''
            out.append('<p><label>Require assisted trial <select name="trial_required"><option value="no">No</option><option value="yes"'+yes+'>Yes</option></select></label></p>')
            approval=result['policy_sha256'] if result else ''
            if approval: out.append('<p>Policy preview digest: <code>'+e(approval)+'</code></p>')
            out.append(field('approval','Retained approval digest (empty for preview)'))
            out.append('<button name="action" value="preview_policy">Preview policy</button> <button name="action" value="freeze_policy">Freeze exact approved policy</button></form>')
        if result: out.append('<p>Policy checkpoint: <code>'+e(result['policy_sha256'])+'</code></p>')
    else:
        out.append('<h2>Replay and seal aggregate evidence</h2><p>If an assisted-trial package is staged, its independently retained digest is mandatory. No package is silently omitted.</p><form method="post">'+token)
        for k,label in [('policy','Retained policy digest'),('head','Retained procurement head'),('report','Retained assessment report digest'),('trial','Retained trial package digest (if staged)'),('approval','Retained dossier digest (empty for preview)')]:
            out.append(field(k,label,'' if k=='approval' else fields.get(k,'')))
        out.append('<button name="action" value="preview">Preview dossier</button> <button name="action" value="seal">Seal exact approved dossier</button> <button name="action" value="verify">Verify sealed dossier</button> <button name="action" value="download">Verify and download JSON</button></form>')
        if result:
            out.extend(['<h2>Aggregate evidence</h2><p>Dossier digest: <code>'+e(result['dossier_sha256'])+'</code></p>',
                web.table(['Role','Known','Planned','Minimum bp','Status'],[(k,v['known'],v['planned'],v['minimum_bp'],v['status']) for k,v in result['predictive_coverage'].items()]),
                web.table(['Documentary evidence','Count'],result['documentary_evidence'].items()),
                '<h2>Administrative gaps</h2>',web.table(['Gap'],[(x,) for x in result['administrative_evidence_gaps']]),
                '<p>Assisted trial: '+e(result['assisted_trial_status'])+'</p>',
                web.table(['Metric','Bounds'], [('Paired Brier difference',result['paired_brier_delta_bounds']),('Adverse outcome difference',result['binary_adverse_outcome_delta_bounds'])]),
                web.table(['Threshold bp','Candidate minus baseline net benefit'],[(r['threshold_bp'],r['candidate_minus_baseline']) for r in result['net_benefit_delta_bounds']]),
                web.table(['Population shift status','Count'],result['population_shift_status_counts'].items())])
            if result['assisted_trial'] is not None:
                t=result['assisted_trial']; out.append(web.table(['Assisted trial','Value'],[(k,t[k]) for k in ('coverage','assisted_minus_unassisted_equal_cluster_bounds','reference_panel_resolution','reference_primary_agreement','reference_cohen_kappa','conditional_sharp_null_test')]))
    return (''.join(out)+'</body></html>').encode('utf-8')


class Manager(parent.Manager):
    def page(self, message=''):
        body=super().page(message)
        if self.active and self.active.prepared is not None:
            body=body.replace(b'</body>',b'<p><a href="/dossier">Research evidence dossier</a></p></body>',1)
        return body

    def _dispatch(self, env, start):
        if env.get('PATH_INFO')!='/dossier': return super()._dispatch(env,start)
        def respond(status,body=b'',mime='text/plain; charset=utf-8',extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra])
            return [body]
        if not self.host or env.get('HTTP_HOST')!=self.host or env.get('REMOTE_ADDR')!='127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host): return respond('403 Forbidden')
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth): return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local assessment"')])
        if env.get('QUERY_STRING'): return respond('400 Bad Request')
        child=self.active; method=env.get('REQUEST_METHOD')
        if child is None or child.prepared is None or self.worker.busy(): return respond('409 Conflict',b'Choose a frozen workspace and stop active workers.')
        if method=='GET': return respond('200 OK',page(child),'text/html; charset=utf-8')
        if method!='POST': return respond('405 Method Not Allowed',extra=[('Allow','GET, POST')])
        try:
            n=int(env.get('CONTENT_LENGTH','')); require(1<=n<=4096 and env.get('CONTENT_TYPE')=='application/x-www-form-urlencoded')
            raw=env['wsgi.input'].read(n); require(len(raw)==n)
            pairs=parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=8,errors='strict')
            fields=dict(pairs); require(len(pairs)==len(fields))
            name=fields.get('action')
            require(name in ('preview_policy','freeze_policy','preview','seal','verify','download'))
            keys=POLICY_FIELDS|{'approval'} if name.endswith('_policy') else BUILD_FIELDS
            require(set(fields)==keys|{'csrf','action'} and fields['csrf'].isascii() and hmac.compare_digest(fields['csrf'],child.csrf))
        except (ValueError,TypeError,UnicodeError,KeyError): return respond('403 Forbidden')
        try:
            result=action(child,fields)
            if name=='download': return respond('200 OK',pred.canonical(result)+b'\n','application/json',extra=[('Content-Disposition','attachment; filename="research-dossier.json"')])
            return respond('200 OK',page(child,result,fields),'text/html; charset=utf-8')
        except (OSError,ValueError,TypeError,KeyError,RecursionError,vendor.sqlite3.Error):
            return respond('409 Conflict',b'Dossier operation refused. Check approved files, study phase and retained checkpoints.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True); parser.add_argument('--staging-root',required=True)
    a=parser.parse_args(argv); app=None
    try:
        app=Manager(a.root,a.staging_root)
        with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
            app.host='127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+'/workspaces in a local browser. Username: operator',flush=True)
            print('Session password: '+app.password,flush=True)
            server.serve_forever()
    except KeyboardInterrupt: return 0
    except (OSError,ValueError,TypeError):
        print('Local dossier browser startup refused.'); return 2
    finally:
        if app is not None: app.worker.shutdown()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
