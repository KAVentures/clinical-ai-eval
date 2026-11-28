"""Explicit local and institutional launch profiles; never share their trust boundaries."""
import argparse
import importlib
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import urlsplit

LOCAL = ('workspaces','staging','comparisons')
INSTITUTION = 'institution'
SCHEMA = 'hospital-launcher/1'


def require(ok):
    if not ok:
        raise ValueError('invalid private launch layout or configuration')


def private(path):
    p = Path(path).absolute()
    require(p.is_dir() and not p.is_symlink() and p.resolve()==p)
    if os.name=='posix':
        st=p.stat()
        require(st.st_uid==os.getuid() and stat.S_IMODE(st.st_mode)&0o077==0)
    return p


def layout(root):
    root=private(root)
    return {name:root/name for name in (*LOCAL,INSTITUTION)}


def init_local(root):
    """Create only the three local workbench directories; never an institution."""
    paths=layout(root)
    # Check every existing path before creating anything.
    for name in (*LOCAL,INSTITUTION):
        p=paths[name]
        if p.exists() or p.is_symlink(): private(p)
    for name in LOCAL:
        p=paths[name]
        if not p.exists(): p.mkdir(mode=0o700,exist_ok=False)
        private(p)
    return {name:str(paths[name]) for name in LOCAL}


def check(root, profile):
    require(profile in ('local','institution'))
    paths=layout(root)
    # Reject redirected siblings even when launching the other profile.
    for p in paths.values():
        if p.exists() or p.is_symlink(): private(p)
    required=LOCAL if profile=='local' else (INSTITUTION,)
    return {name:str(private(paths[name])) for name in required}


def diagnose(root):
    """Path checks only; no loading clinical files, credentials, or databases."""
    paths=layout(root); status={}
    for name,p in paths.items():
        if not p.exists() and not p.is_symlink(): status[name]='missing'
        else:
            try: private(p); status[name]='private_directory'
            except (OSError,ValueError): status[name]='unsafe_or_unavailable'
    safe=all(v!='unsafe_or_unavailable' for v in status.values())
    return {'schema':SCHEMA,'directories':status,
            'local_layout_ready':safe and all(status[n]=='private_directory' for n in LOCAL),
            'institution_layout_ready':safe and status[INSTITUTION]=='private_directory',
            'institution_accounts_verified':False,'source_records_verified':False,
            'clinical_validation_established':False}


def https_origin(value):
    require(isinstance(value,str) and bool(value) and value.isascii())
    require(not any(c.isspace() or ord(c)<32 for c in value) and '\\' not in value)
    parsed=urlsplit(value)
    require(parsed.scheme=='https' and bool(parsed.hostname) and parsed.netloc and
            not parsed.username and not parsed.password and parsed.username is None and
            parsed.password is None and not parsed.path and not parsed.query and not parsed.fragment)
    require(value=='https://'+parsed.netloc and '#' not in value and '?' not in value)
    require(parsed.port is None or 1<=parsed.port<=65535)
    return value


def launch_local(root):
    paths=check(root,'local')
    module=importlib.import_module('.assessment_multivendor_web',__package__)
    return module.main(['--root',paths['workspaces'],'--staging-root',paths['staging'],
                        '--comparison-root',paths['comparisons']])


def launch_institution(root, origin, port):
    paths=check(root,'institution')
    origin=https_origin(origin)
    require(type(port) is int and 1<=port<=65535)
    # Preserve the existing institutional gateway, account checks and HTTPS
    # reverse-proxy requirements. Never proxy local workbench requests here.
    module=importlib.import_module('.institution_web',__package__)
    module.serve(paths[INSTITUTION],origin,port)
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('init-local','doctor','local','institution'):
        p=sub.add_parser(name); p.add_argument('--root',required=True)
        if name=='institution':
            p.add_argument('--origin',required=True,help='Exact approved HTTPS reverse-proxy origin')
            p.add_argument('--port',type=int,default=8767)
    a=parser.parse_args(argv)
    try:
        if a.command=='init-local':
            init_local(a.root); print(json.dumps(diagnose(a.root),sort_keys=True)); return 0
        if a.command=='doctor':
            print(json.dumps(diagnose(a.root),sort_keys=True)); return 0
        if a.command=='local': return launch_local(a.root)
        return launch_institution(a.root,a.origin,a.port)
    except KeyboardInterrupt:
        return 0
    except (OSError,ValueError,TypeError,ImportError):
        print('Launch refused: check private layout, installed modules and approved configuration.',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
