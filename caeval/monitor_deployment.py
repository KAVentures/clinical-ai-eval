"""Generate reviewable Linux systemd units for an exact local monitor pipeline.

No services are installed, enabled, or started by this module.
"""
import json
import os
import re
import sqlite3
from pathlib import Path
from . import monitor_alert_relay as relay, monitor_alert_worker as worker
from . import monitor_alert_health as health, monitor_relay_bridge as bridge


FIELDS = ('instance account executable workspace state_dir credential_dir monitor_id '
          'plan_digest approval source_port sink_port socket_timeout interval watchdog_interval '
          'cycle_deadline max_gap max_run backlog_limit backlog_age')
ERRORS = (ValueError, TypeError, KeyError, AttributeError, OSError, sqlite3.Error, RecursionError)


def _path(value):
    relay.require(isinstance(value, str) and len(value) <= 512 and
                  re.fullmatch(r'/[A-Za-z0-9._/-]+', value) is not None and
                  all(p not in ('', '.', '..') for p in value.split('/')[1:]),
                  'absolute normalized systemd-safe path required')
    return Path(value)


def _under(child, parent):
    return child == parent or parent in child.parents


def _dedicated(value, roots):
    path = _path(value)
    relay.require(any(root in path.parents for root in map(Path, roots)), 'dedicated service directory required')
    return path


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _paths(spec):
    ws = _dedicated(spec['workspace'], ('/srv', '/var/lib'))
    state = _dedicated(spec['state_dir'], ('/srv', '/var/lib'))
    creds = _dedicated(spec['credential_dir'], ('/etc', '/srv'))
    executable = _path(spec['executable'])
    relay.require(any(root in executable.parents for root in map(Path, ('/opt','/usr/local','/usr/bin'))),
                  'approved installed executable path required')
    dirs = (ws, state, creds)
    relay.require(all(not _under(a,b) for a in dirs for b in dirs if a != b) and len(set(dirs)) == 3,
                  'workspace, worker state and credentials must be separate directories')
    return {'workspace':ws, 'state':state, 'credentials':creds, 'executable':executable,
            'outbox':state/'alerts.sqlite3', 'heartbeat':state/'health.sqlite3',
            'relay':ws/'institution'/'notification-relays'/(spec['monitor_id']+'.sqlite3'),
            'source_secret':creds/'check-token', 'sink_secret':creds/'relay-token'}


def _validate(spec):
    relay.keys(spec, FIELDS)
    relay.require(isinstance(spec['instance'],str) and re.fullmatch(r'[a-z][a-z0-9-]{0,30}', spec['instance']) and
                  isinstance(spec['account'],str) and re.fullmatch(r'[a-z_][a-z0-9_-]{0,30}', spec['account']) and
                  spec['account'] != 'root' and relay.token(spec['monitor_id']) and
                  relay.hex64(spec['plan_digest']) and relay.hex64(spec['approval']), 'invalid instance, account or scope')
    worker._config(spec['source_port'], spec['sink_port'], spec['socket_timeout'])
    health._policy(spec['approval'], spec['max_gap'], spec['max_run'])
    relay.require(_integer(spec['interval'], 15, 3600) and _integer(spec['watchdog_interval'], 15, 3600)
                  and _integer(spec['cycle_deadline'], 5, spec['max_run'])
                  and spec['cycle_deadline'] > 2*spec['socket_timeout']
                  and spec['interval'] + spec['cycle_deadline'] < spec['max_gap']
                  and spec['watchdog_interval'] <= spec['max_gap']
                  and _integer(spec['backlog_limit'],1,relay.MAX_BATCHES)
                  and _integer(spec['backlog_age'],60,31*86400), 'inconsistent operational timing or backlog policy')
    return _paths(spec)


def render(spec):
    p = _validate(spec)
    prefix = 'caeval-monitor-'+spec['instance']
    ingest, sink, cycle, watchdog = (prefix+'-'+k for k in ('ingest','relay','cycle','watchdog'))
    exe = str(p['executable'])
    hardening = ['User='+spec['account'], 'UMask=0077', 'NoNewPrivileges=yes', 'ProtectSystem=strict',
                 'ProtectHome=yes', 'PrivateDevices=yes', 'RestrictAddressFamilies=AF_UNIX AF_INET',
                 'IPAddressDeny=any', 'IPAddressAllow=localhost', 'KillMode=control-group', 'TimeoutStopSec=15s']
    def service(description, command, *, daemon=False, writable=(), after=(), deadline=None):
        lines=['[Unit]', 'Description='+description]
        if after:
            deps=' '.join(a+'.service' for a in after)
            lines += ['Wants='+deps, 'After='+deps]
        lines += ['', '[Service]', 'Type='+('simple' if daemon else 'oneshot'), *hardening]
        if writable:
            lines += ['ReadWritePaths='+' '.join(map(str,writable))]
        lines += ['ExecStart='+exe+' '+' '.join(map(str,command))]
        if daemon:
            lines += ['Restart=on-failure','RestartSec=5s','','[Install]','WantedBy=multi-user.target']
        else:
            lines += ['TimeoutStartSec='+str(deadline)+'s']
        return '\n'.join(lines)+'\n'
    def timer(name, seconds):
        return ('[Unit]\nDescription=Scheduled local monitoring '+name+'\n\n[Timer]\n'
                'OnBootSec='+str(seconds)+'s\nOnUnitInactiveSec='+str(seconds)+'s\nAccuracySec=1s\n'
                'Unit='+name+'.service\n\n[Install]\nWantedBy=timers.target\n')
    units = {
        ingest+'.service':service('Local aggregate monitoring source', ['serve-monitor-ingestion',p['workspace'],'--port',spec['source_port']],daemon=True,writable=(p['workspace'],)),
        sink+'.service':service('Local durable notification relay', ['monitor-alert-relay','serve',p['relay'],'--port',spec['sink_port'],'--secret-file',p['sink_secret']],daemon=True,writable=(p['relay'].parent,)),
        cycle+'.service':service('Guarded local monitoring cycle', ['monitor-alert-health','cycle',p['heartbeat'],p['outbox'],'--approved-digest',spec['approval'],'--source-secret-file',p['source_secret'],'--sink-secret-file',p['sink_secret']],writable=(p['state'],),after=(ingest,sink),deadline=spec['cycle_deadline']),
        cycle+'.timer':timer(cycle,spec['interval']),
        watchdog+'.service':service('Local monitoring pipeline watchdog', ['monitor-pipeline-health',p['outbox'],p['heartbeat'],p['relay'],'--approved-digest',spec['approval'],'--backlog-limit',spec['backlog_limit'],'--backlog-age',spec['backlog_age']],writable=(p['state'],),deadline=45),
        watchdog+'.timer':timer(watchdog,spec['watchdog_interval']),
    }
    doc={'schema':'monitor-systemd-plan/1','spec':dict(spec),'units':units,
         'installs_services':False,'starts_services':False,'clinical_safety_established':False}
    return {**doc,'digest':relay.digest(doc)}


def _check_dir(path):
    relay.require(path.is_dir() and not path.is_symlink() and not any(a.is_symlink() for a in path.parents), 'directory missing or linked')
    if os.name == 'posix':
        relay.require(not path.stat().st_mode & 0o077, 'service directory must be private')


def check(spec):
    """Offline prerequisites, not a liveness test or permission simulation."""
    p = _validate(spec)
    if os.name != "posix":
        raise ValueError("Linux service host required for prerequisite inspection")
    import pwd
    relay.require(pwd.getpwnam(spec["account"]).pw_uid != 0, "non-root service account required")
    for key in ('workspace','state','credentials'):
        _check_dir(p[key])
    _check_dir(p['relay'].parent)
    relay.require(p['executable'].is_file() and os.access(p['executable'],os.X_OK), 'executable unavailable')
    binding,state = worker._read_binding(p['outbox'],spec['approval'])
    expected={'monitor_id':spec['monitor_id'],'plan_digest':spec['plan_digest']}
    relay.require(state['scope']==expected and binding['config']==worker._config(spec['source_port'],spec['sink_port'],spec['socket_timeout']), 'outbox scope or endpoints mismatch')
    with relay.db(p['heartbeat']) as conn:
        hb=health._load(conn)
        relay.require(hb['policy']==health._policy(spec['approval'],spec['max_gap'],spec['max_run']), 'heartbeat policy mismatch')
        relay.require(not hb['run'] or hb['run']['state']!='running', 'active or interrupted run; stop and investigate first')
    with bridge.read_db(p['relay'],spec['monitor_id'],spec['plan_digest']):
        pass
    relay.require(worker._secret(p['source_secret'])!=worker._secret(p['sink_secret']), 'distinct credentials required')
    return render(spec)


def export(spec, approval, output, *, confirmed=False):
    relay.require(confirmed is True, 'explicit configuration export required')
    doc=check(spec)
    relay.require(doc['digest']==approval, 'approved configuration differs')
    dest=_path(output)
    relay.require(not any(a.is_symlink() for a in dest.parents), 'linked export path refused')
    dest.mkdir(mode=0o700)  # exclusive; no overwrite or partial auto-reset
    for name, content in {**doc['units'],'manifest.json':json.dumps(doc,sort_keys=True,indent=2)+'\n'}.items():
        fd=os.open(dest/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
            f.write(content)
    return doc


def configure(parser):
    sub=parser.add_subparsers(dest='deployment_action',required=True)
    for action in ('render','check','export'):
        p=sub.add_parser(action);p.add_argument('spec')
        if action=='export':
            p.add_argument('output');p.add_argument('--approved-digest',required=True);p.add_argument('--confirm-export',action='store_true')
    parser.set_defaults(func=cli)


def cli(args):
    try:
        from . import monitor_alert_outbox as outbox
        spec=outbox._read(args.spec)
        if args.deployment_action=='render': result=render(spec)
        elif args.deployment_action=='check': result=check(spec)
        else: result=export(spec,args.approved_digest,args.output,confirmed=args.confirm_export)
        print(relay.canonical(result))
    except ERRORS:
        raise SystemExit('Monitoring deployment configuration refused; no services installed.')
