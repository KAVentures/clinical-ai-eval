import copy
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from caeval import (monitor_deployment as deployment, monitor_alert_store as store,
                    monitor_alert_worker as worker, monitor_alert_health as health,
                    monitor_alert_relay as relay)


def spec():
    return dict(instance='demo',account='nobody',executable='/usr/bin/true',workspace='/srv/caeval-demo',
                state_dir='/var/lib/caeval-demo',credential_dir='/etc/caeval-demo',monitor_id='m',
                plan_digest='a'*64,approval='b'*64,source_port=8772,sink_port=8773,socket_timeout=10,
                interval=60,watchdog_interval=60,cycle_deadline=45,max_gap=180,max_run=60,
                backlog_limit=20,backlog_age=3600)


class DeploymentTests(unittest.TestCase):
    def test_render_deterministic_and_no_secret_values(self):
        s=spec();doc=deployment.render(s)
        self.assertEqual(doc,deployment.render(copy.deepcopy(s)))
        self.assertEqual(len(doc['units']),6)
        self.assertFalse(doc['installs_services'])
        text='\n'.join(doc['units'].values())
        self.assertIn('monitor-alert-health cycle',text)
        self.assertIn('monitor-pipeline-health',text)
        self.assertIn('OnUnitInactiveSec=60s',text)
        self.assertIn('IPAddressDeny=any',text)
        self.assertIn('TimeoutStartSec=45s',text)
        self.assertNotIn('Bearer ',text)
        self.assertNotIn('WantedBy=multi-user.target',doc['units']['caeval-monitor-demo-cycle.service'])

    def test_injection_and_invalid_policy_refused(self):
        for change in ({'instance':'x\n[Service]'},{'account':'root'},{'workspace':'/srv/a/../b'},
                       {'credential_dir':'/srv/caeval-demo/secrets'},{'state_dir':'/var/lib'},
                       {'executable':'/usr/bin/true %n'},{'sink_port':8772},{'cycle_deadline':20},
                       {'max_gap':100},{'interval':True}):
            with self.subTest(change=change), self.assertRaises(ValueError): deployment.render({**spec(),**change})

    @unittest.skipUnless(shutil.which('systemd-analyze') and Path('/usr/bin/true').exists(),'systemd tooling unavailable')
    def test_systemd_syntax(self):
        with tempfile.TemporaryDirectory() as root:
            files=[]
            for name,body in deployment.render(spec())['units'].items():
                p=Path(root)/name;p.write_text(body);files.append(str(p))
            r=subprocess.run(['systemd-analyze','verify',*files],capture_output=True,text=True,timeout=15)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    @unittest.skipUnless(os.name=='posix','POSIX prerequisite test')
    def test_real_store_preflight_and_exclusive_export(self):
        import pwd
        try: account=pwd.getpwnam('nobody')
        except KeyError: self.skipTest('non-root account fixture unavailable')
        if account.pw_uid==0: self.skipTest('non-root account fixture unavailable')
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);s=spec()
            ws,state,creds=(root/x for x in ('workspace','state','credentials'))
            for p in (ws,state,creds): p.mkdir(mode=0o700)
            rdir=ws/'institution'/'notification-relays';rdir.mkdir(mode=0o700,parents=True)
            paths={'workspace':ws,'state':state,'credentials':creds,'executable':Path('/usr/bin/true'),
                   'outbox':state/'alerts.sqlite3','heartbeat':state/'health.sqlite3','relay':rdir/'m.sqlite3',
                   'source_secret':creds/'check-token','sink_secret':creds/'relay-token'}
            for k,char in (('source_secret','c'),('sink_secret','d')):
                fd=os.open(paths[k],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as f:f.write(char*64+'\n')
            store.initialize(paths['outbox'],'m',s['plan_digest'])
            config=worker._config(s['source_port'],s['sink_port'],s['socket_timeout'])
            s['approval']=worker.preview(paths['outbox'],**config)['digest']
            worker.bind(paths['outbox'],config,s['approval'],confirmed=True)
            health.initialize(paths['heartbeat'],s['approval'],s['max_gap'],s['max_run'])
            relay.initialize(paths['relay'],'m',s['plan_digest'])
            with patch.object(deployment,'_paths',return_value=paths):
                doc=deployment.check(s)
                self.assertEqual(doc,deployment.render(s))
                dest=root/'bundle'
                with self.assertRaises(ValueError): deployment.export(s,'0'*64,str(dest),confirmed=True)
                self.assertFalse(dest.exists())
                deployment.export(s,doc['digest'],str(dest),confirmed=True)
                self.assertEqual(len(list(dest.iterdir())),7)
                with self.assertRaises(FileExistsError): deployment.export(s,doc['digest'],str(dest),confirmed=True)
                with self.assertRaises(ValueError): deployment.check({**s,'plan_digest':'e'*64})
                run=health.start(paths['heartbeat'],s['approval'])
                with self.assertRaises(ValueError): deployment.check(s)
                health.finish(paths['heartbeat'],run,'interrupted',process_stopped=True)
                self.assertEqual(deployment.check(s),doc)


if __name__=='__main__': unittest.main()
