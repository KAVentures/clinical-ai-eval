"""Bounded, explicitly approved local background prediction batches."""
import argparse
import copy
import hmac
import re
import secrets
from threading import Event, Lock, Thread
from urllib.parse import parse_qsl
from wsgiref.simple_server import make_server

from . import predictive_job_web as parent, predictive_execution_job as job
from . import predictive_research as pred, assessment_workbench_web as web
from . import vendor_workbench as storage

MAX_BATCH = 100
BUSY = ('running', 'pausing')


def require(ok):
    if not ok:
        raise ValueError('worker operation refused')


class Worker:
    def __init__(self):
        self.lock = Lock()
        self.stop = Event()
        self.thread = None
        self.state = None

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def busy(self):
        state = self.snapshot()
        return state is not None and state['state'] in BUSY

    def start(self, child, name, role, execution, head, limit):
        require(type(limit) is int and 1 <= limit <= MAX_BATCH)
        require(pred.hex64(execution) and pred.hex64(head))
        with self.lock:
            require(self.thread is None or not self.thread.is_alive())
            args = parent.material(child, role)
            require(args[4] == execution)
            state = job.status(*args, head)
            require(state['pending'] is None and state['finished'] is None)
            self.stop = Event()
            self.state = {'run_id': secrets.token_hex(16), 'workspace': name, 'role': role,
                          'execution': execution, 'approved_head': head, 'limit': limit,
                          'steps': 0, 'progress': state, 'state': 'running'}
            self.thread = Thread(target=self._run, args=(child, role, execution, head, limit), daemon=True)
            try:
                self.thread.start()
            except BaseException:
                self.state['state'] = 'error'
                raise

    def _run(self, child, role, execution, head, limit):
        ending = 'limit_reached'
        try:
            for _ in range(limit):
                if self.stop.is_set():
                    ending = 'paused'; break
                # Revalidate approved inputs and procurement checkpoint before every step.
                args = parent.material(child, role)
                require(args[4] == execution)
                if self.stop.is_set():
                    ending = 'paused'; break
                state = job.step(*args, head, confirmed=True)
                head = state['head']
                with self.lock:
                    self.state['progress'] = state
                    self.state['steps'] += 1
                if state['finished'] is not None:
                    ending = 'finished'; break
                if self.stop.is_set():
                    ending = 'paused'; break
                opt = args[3]['options']
                if (state['n_recorded'] == state['n_planned'] or state['n_recorded'] >= opt['max_calls']
                        or state['elapsed_ms'] >= 1000 * opt['max_seconds']):
                    state = job.finish(*args, head)
                    with self.lock:
                        self.state['progress'] = state
                    ending = 'finished'; break
        except BaseException:
            # Never adopt a newer head on error. An interrupted claim must be reconciled.
            ending = 'error'
        finally:
            with self.lock:
                self.state['state'] = ending

    def pause(self, run_id):
        with self.lock:
            require(self.state is not None and self.state['run_id'] == run_id and self.state['state'] in BUSY)
            self.stop.set()
            self.state['state'] = 'pausing'

    def shutdown(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=1)


def page(child, state):
    e = web.esc
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Prediction worker</title><link rel="stylesheet" href="/style.css"></head><body><nav><a href="/workspaces">Workspace selector</a> | <a href="/worker">Refresh progress</a></nav><h1>Background prediction worker</h1><p class="notice">Only approved loopback model jobs. One worker per browser process. Progress reflects the last committed result; a request may be in flight. Retain all heads independently.</p>']
    if state is not None:
        progress = state['progress']
        out.extend([web.table(['Worker','Value'],[(k,state[k]) for k in ('workspace','role','state','steps','limit','approved_head','execution')]),
                    web.table(['Last committed progress','Value'],[(k,v) for k,v in progress.items() if k != 'counts']+list(progress['counts'].items())),
                    '<progress max="'+str(progress['n_planned'])+'" value="'+str(progress['n_recorded'])+'"></progress>'])
        if state['state'] in BUSY:
            out.append('<form method="post"><input type="hidden" name="csrf" value="'+child.csrf+'"><input type="hidden" name="action" value="pause"><input type="hidden" name="run_id" value="'+e(state['run_id'])+'"><button>Pause after current attempt</button></form>')
        elif state['state']=='error':
            out.append('<p>Worker stopped on error. Inspect job history using the last retained head and reconcile before resuming; never retry an uncertain call.</p>')
    if state is None or state['state'] not in BUSY:
        out.append('<form method="post"><input type="hidden" name="csrf" value="'+child.csrf+'"><input type="hidden" name="action" value="start"><p><label>Role <select name="role"><option>baseline</option><option>candidate</option></select></label></p><p><label>Approved execution digest <input name="execution" required maxlength="64" autocomplete="off"></label></p><p><label>Retained current job head <input name="head" required maxlength="64" autocomplete="off"></label></p><p><label>Maximum new steps (1–100) <input name="limit" type="number" min="1" max="100" required></label></p><button>Approve and start bounded worker</button></form>')
    out.append('<p>Pause is cooperative between attempts, not cancellation of a request already starting or in flight. No automatic resume or scheduled execution after restarting the application.</p></body></html>')
    return ''.join(out).encode('utf-8')


class Manager(parent.Manager):
    def __init__(self, *args):
        super().__init__(*args)
        self.worker = Worker()
        self.dispatch = Lock()

    def page(self, message=''):
        body = super().page(message)
        if self.active and self.active.prepared is not None and self.active.review is None:
            body = body.replace(b'</body>',b'<p><a href="/worker">Background prediction worker</a></p></body>',1)
        return body

    def __call__(self, env, start):
        # Serialize browser dispatch, not model execution. Block all other writes while
        # the worker is active, including workspace selection and collection sealing.
        with self.dispatch:
            return self._dispatch(env,start)

    def _dispatch(self, env, start):
        path, method = env.get('PATH_INFO'),env.get('REQUEST_METHOD')
        if path != '/worker' and not (self.worker.busy() and method != 'GET'):
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
        if path != '/worker': return respond('409 Conflict',b'Pause the active worker and wait for its current attempt before other writes.')
        child = self.active
        if child is None or child.prepared is None or child.review is not None or getattr(child,'collection_committed',False):
            return respond('409 Conflict',b'Choose a frozen unfinished workspace.')
        if method not in ('GET','POST'): return respond('405 Method Not Allowed',extra=[('Allow','GET, POST')])
        state = self.worker.snapshot()
        if state is not None and state['workspace'] != self.active_name:
            state = None
        if method == 'POST':
            try:
                n=int(env.get('CONTENT_LENGTH','')); require(1<=n<=4096 and env.get('CONTENT_TYPE')=='application/x-www-form-urlencoded')
                raw=env['wsgi.input'].read(n); require(len(raw)==n)
                pairs=parse_qsl(raw.decode('ascii'),keep_blank_values=True,strict_parsing=True,max_num_fields=6,errors='strict')
                fields=dict(pairs); require(len(pairs)==len(fields))
                action=fields.get('action')
                require(set(fields)==({'csrf','action','role','execution','head','limit'} if action=='start' else {'csrf','action','run_id'}))
                require(action in ('start','pause') and fields['csrf'].isascii() and hmac.compare_digest(fields['csrf'],child.csrf))
            except (ValueError,TypeError,UnicodeError,KeyError): return respond('403 Forbidden')
            try:
                if action=='start':
                    require(re.fullmatch(r'[0-9]{1,3}',fields['limit']) is not None)
                    self.worker.start(child,self.active_name,fields['role'],fields['execution'],fields['head'],int(fields['limit']))
                else:
                    require(state is not None)
                    self.worker.pause(fields['run_id'])
            except (OSError,ValueError,TypeError,KeyError,RecursionError,RuntimeError,storage.sqlite3.Error):
                return respond('409 Conflict',b'Worker action refused. Check approved scope and retained checkpoints.')
            return respond('303 See Other',extra=[('Location','/worker')])
        return respond('200 OK',page(child,state),'text/html; charset=utf-8')


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
    except KeyboardInterrupt:
        return 0
    except (OSError,ValueError,TypeError):
        print('Local worker browser startup refused.'); return 2
    finally:
        if app is not None: app.worker.shutdown()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
