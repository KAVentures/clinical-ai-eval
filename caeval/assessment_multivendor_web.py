"""Private multi-product comparison browser using the native matched engine."""
import argparse
import hmac
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server

from . import assessment_dossier_web as parent, assessment_multivendor as multi
from . import assessment_workspace_web as selector, assessment_resume_web as resume
from . import assessment_setup_web as storage, assessment_workbench_web as web
from . import predictive_research as pred, vendor_workbench as vendor

SLOTS = ('workspace','plan','head','policy','report','dossier','trial')
KEYS = {'csrf','action','name','comparison','approval'} | {k+str(i) for i in range(1,6) for k in SLOTS}


def require(ok):
    if not ok:
        raise ValueError('comparison action refused')


def load(manager, fields, freezing):
    names = selector.inventory(manager.root)
    members, results = {}, {}
    for i in range(1,6):
        row = {k:fields[k+str(i)] for k in SLOTS}
        name = row['workspace']
        if not name:
            require(not any(row.values())); continue
        require(name in names and name not in members)
        require(all(pred.hex64(row[k]) for k in ('plan','head','policy')))
        directory = storage.private_directory(manager.root/name)
        require(not any(resume.present(directory/('procurement.db'+s)) for s in ('-journal','-wal','-shm')))
        if freezing:
            require(not any(row[k] for k in ('report','dossier','trial')))
            require(not any(resume.present(directory/(k+'.json')) for k in (*resume.COLLECTION,parent.RESULT)))
        if row['report']:
            require(not freezing and pred.hex64(row['report']) and pred.hex64(row['dossier']))
            plan, inputs, _ = resume.complete_inputs(directory,row['plan'],row['head'],row['report'])
            staged = storage.private_directory(manager.staging_root/name)
            present = resume.present(staged/(parent.TRIAL+'.json'))
            require(present == bool(row['trial']) and (not present or pred.hex64(row['trial'])))
            results[name] = {'expected_report':row['report'],'expected_dossier':row['dossier'],
                'dossier':resume.read(directory,parent.RESULT),'trial_package':resume.read(staged,parent.TRIAL) if present else None,
                'expected_trial':row['trial'] or None}
        else:
            require(not row['dossier'] and not row['trial'] and not resume.present(directory/(parent.RESULT+'.json')))
            plan, inputs = resume.base_inputs(directory,row['plan'],row['head'])
            results[name] = None
        members[name] = {'plan':plan,'expected_plan':row['plan'],'inputs':inputs,
                         'policy':resume.read(directory,parent.POLICY),'expected_policy':row['policy']}
    return members, results


def action(manager, fields):
    name, op, approval = (fields[k] for k in ('name','action','approval'))
    require(selector.name_ok(name))
    root = storage.private_directory(manager.comparison_root)
    design_path, report_path = name+'-design', name+'-report'
    freezing = op in ('preview_design','freeze_design')
    members, results = load(manager,fields,freezing)
    if freezing:
        require(not fields['comparison'] and not any(resume.present(root/(k+'.json')) for k in (design_path,report_path)))
        result = multi.freeze(members)
        if op=='freeze_design':
            require(approval==result['comparison_sha256']); pred._write(root/(design_path+'.json'),result)
        else: require(not approval)
        return result
    design = resume.read(root,design_path)
    result = multi.analyze(design,fields['comparison'],members,results)
    if op=='preview': require(not approval)
    else:
        require(approval==result['report_sha256'])
        if op=='seal': pred._write(root/(report_path+'.json'),result)
        else:
            require(op in ('verify','download'))
            require(pred.canonical(resume.read(root,report_path))==pred.canonical(result))
    return result


def page(manager, result=None, fields=None):
    fields=fields or {}; e=web.esc
    names=selector.inventory(manager.root)
    out=['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Matched product comparison</title><link rel="stylesheet" href="/style.css"></head><body><nav><a href="/workspaces">Workspace selector</a></nav><h1>Matched product comparison</h1><p class="notice">Restricted local evidence. No ranking or purchasing approval. Select two to five frozen workspaces. Retain all checkpoints independently.</p><form method="post"><input type="hidden" name="csrf" value="'+manager.csrf+'">']
    def field(key,label,value=''):
        return '<p><label>'+label+' <input name="'+key+'" maxlength="64" value="'+e(value)+'" autocomplete="off"></label></p>'
    out.append(field('name','Comparison name',fields.get('name','')))
    for i in range(1,6):
        k='workspace'+str(i); chosen=fields.get(k,'')
        options=''.join('<option value="'+e(n)+'"'+(' selected' if n==chosen else '')+'>'+e(n)+'</option>' for n in names)
        out.append('<fieldset><legend>Product '+str(i)+'</legend><label>Workspace <select name="'+k+'"><option value="">Unused</option>'+options+'</select></label>')
        for k,label in [('plan','Plan digest'),('head','Procurement head'),('policy','Policy digest'),('report','Report digest (if complete)'),('dossier','Dossier digest (if complete)'),('trial','Trial package digest (if staged)')]:
            key=k+str(i); out.append(field(key,label,fields.get(key,'')))
        out.append('</fieldset>')
    out.append(field('comparison','Retained comparison design digest (analysis only)',fields.get('comparison','')))
    out.append(field('approval','Retained approval digest (empty for preview)'))
    for op,label in [('preview_design','Preview design'),('freeze_design','Freeze approved design'),('preview','Preview comparison'),('seal','Seal approved report'),('verify','Verify sealed report'),('download','Verify and download JSON')]:
        out.append('<button name="action" value="'+op+'">'+label+'</button> ')
    out.append('</form>')
    if result:
        key='report_sha256' if 'report_sha256' in result else 'comparison_sha256'
        out.append('<h2>Verified preview</h2><p>Checkpoint: <code>'+e(result[key])+'</code></p>')
        if key=='report_sha256':
            out.append(web.table(['Product','Evidence status','Administrative gaps'],[(n,v['status'],len(v['dossier']['administrative_evidence_gaps']) if v['dossier'] else 'Unknown') for n,v in result['products'].items()]))
            for p in result['predictive_pairwise_contrasts']:
                out.append('<h3>'+e(p['right'])+' minus '+e(p['left'])+'</h3>')
                out.append(web.table(['Metric','Finite-cohort bounds'],[('Brier loss',p['brier_difference']['cohort_brier_delta_bounds'])]+[(str(x['threshold_bp'])+' bp net benefit',x['cohort_delta_bounds']) for x in p['net_benefit_differences']]))
        else: out.append(web.table(['Product','Scope'],[(n,v['scope']) for n,v in result['products'].items()]))
    return (''.join(out)+'</body></html>').encode('utf-8')


class Manager(parent.Manager):
    def __init__(self,root,staging_root,comparison_root):
        super().__init__(root,staging_root)
        self.comparison_root=storage.private_directory(comparison_root)
        require(all(self.comparison_root!=p and self.comparison_root not in p.parents and p not in self.comparison_root.parents for p in (self.root,self.staging_root)))

    def page(self,message=''):
        return super().page(message).replace(b'</body>',b'<p><a href="/comparisons">Matched product comparisons</a></p></body>',1)

    def _dispatch(self,env,start):
        if env.get('PATH_INFO')!='/comparisons': return super()._dispatch(env,start)
        def respond(status,body=b'',mime='text/plain; charset=utf-8',extra=()):
            start(status,[('Content-Type',mime),('Content-Length',str(len(body))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','no-referrer'),('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),*extra]); return [body]
        if not self.host or env.get('HTTP_HOST')!=self.host or env.get('REMOTE_ADDR')!='127.0.0.1' or env.get('HTTP_SEC_FETCH_SITE') in ('cross-site','same-site') or env.get('HTTP_ORIGIN') not in (None,'','http://'+self.host): return respond('403 Forbidden')
        auth=env.get('HTTP_AUTHORIZATION','')
        if not isinstance(auth,str) or not auth.isascii() or not hmac.compare_digest(auth,self.auth): return respond('401 Unauthorized',extra=[('WWW-Authenticate','Basic realm="Local assessment"')])
        if env.get('QUERY_STRING'): return respond('400 Bad Request')
        if self.worker.busy(): return respond('409 Conflict',b'Stop active workers before comparing products.')
        method=env.get('REQUEST_METHOD')
        if method=='GET':
            try: return respond('200 OK',page(self),'text/html; charset=utf-8')
            except (OSError,ValueError): return respond('409 Conflict')
        if method!='POST': return respond('405 Method Not Allowed',extra=[('Allow','GET, POST')])
        try:
            n=int(env.get('CONTENT_LENGTH','')); require(1<=n<=8192 and env.get('CONTENT_TYPE')=='application/x-www-form-urlencoded')
            raw=env['wsgi.input'].read(n); require(len(raw)==n)
            pairs=parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=len(KEYS),errors='strict')
            fields=dict(pairs); require(len(pairs)==len(fields) and set(fields)==KEYS)
            require(fields['csrf'].isascii() and hmac.compare_digest(fields['csrf'],self.csrf))
            require(fields['action'] in ('preview_design','freeze_design','preview','seal','verify','download'))
        except (ValueError,TypeError,UnicodeError,KeyError): return respond('403 Forbidden')
        try:
            result=action(self,fields)
            if fields['action']=='download': return respond('200 OK',pred.canonical(result)+b'\n','application/json',extra=[('Content-Disposition','attachment; filename="comparison-report.json"')])
            return respond('200 OK',page(self,result,fields),'text/html; charset=utf-8')
        except (OSError,ValueError,TypeError,KeyError,RecursionError,vendor.sqlite3.Error):
            return respond('409 Conflict',b'Comparison refused. Check frozen conditions, private files and retained checkpoints.')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for k in ('root','staging-root','comparison-root'): parser.add_argument('--'+k,required=True)
    a=parser.parse_args(argv); app=None
    try:
        app=Manager(a.root,a.staging_root,a.comparison_root)
        with make_server('127.0.0.1',0,app,handler_class=web.QuietHandler) as server:
            app.host='127.0.0.1:'+str(server.server_port)
            print('Open http://'+app.host+'/workspaces in a local browser. Username: operator',flush=True)
            print('Session password: '+app.password,flush=True); server.serve_forever()
    except KeyboardInterrupt: return 0
    except (OSError,ValueError,TypeError): print('Local comparison browser startup refused.'); return 2
    finally:
        if app is not None: app.worker.shutdown()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
