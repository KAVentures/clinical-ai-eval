"""Centrally deployed institution app factory and offline deployment preflight.

No workstation installation or cloud account is required by the app. The
institution supplies its approved reverse proxy, gateway and WSGI server.
"""
from __future__ import annotations
import argparse
import getpass
import json
import os
import secrets
import stat
import sys
from pathlib import Path

from . import buyer_agent as buyer, institution_access as access
from . import institution_federation as federation, institution_web as web


def _private_workspace(path):
    root=Path(path)
    if root.is_symlink() or not root.is_dir():
        raise access.AccessDenied("private existing workspace required")
    if os.name=="posix" and stat.S_IMODE(root.stat().st_mode)&0o077:
        raise access.AccessDenied("workspace directory must not be group/world accessible")
    buyer.load(root)
    return root.resolve()


def settings(env):
    # Explicit mode is mandatory; accidental omission cannot enable passwords.
    mode=env.get("CAEVAL_AUTH_MODE")
    if mode not in ("local","gateway"):
        raise access.AccessDenied("explicit CAEVAL_AUTH_MODE local or gateway required")
    root=_private_workspace(env["CAEVAL_WORKSPACE"])
    origin=env["CAEVAL_HTTPS_ORIGIN"]
    identity=None
    if mode=="gateway":
        secret_path=Path(env["CAEVAL_PROXY_SECRET_FILE"])
        if not secret_path.is_absolute():
            raise access.AccessDenied("absolute protected gateway secret path required")
        # A gateway credential must not be copied into workspace exports or
        # stopped-service backups.
        try:
            secret_path.resolve().relative_to(root)
        except ValueError:
            pass
        else:
            raise access.AccessDenied("gateway secret must be outside the workspace")
        identity=federation.GatewayIdentity(env["CAEVAL_FEDERATION_ISSUER"],
                                            federation.read_secret(secret_path))
    elif any(env.get(k) for k in ("CAEVAL_PROXY_SECRET_FILE","CAEVAL_FEDERATION_ISSUER")):
        raise access.AccessDenied("local mode must not contain federation settings")
    return root,origin,identity,mode


def create_app(env=None):
    root,origin,identity,_=settings(os.environ if env is None else env)
    return web.InstitutionApp(root,origin,identity=identity)


def preflight(env=None):
    root,origin,identity,mode=settings(os.environ if env is None else env)
    web.InstitutionApp(root,origin,identity=identity)
    with access.db(root) as conn:
        if conn.execute("PRAGMA quick_check").fetchall()[0][0]!="ok":
            raise access.AccessDenied("access database integrity check failed")
        if identity is None:
            admins=conn.execute("""SELECT count(*) FROM users u WHERE role='admin'
                AND enabled=1 AND NOT EXISTS(SELECT 1 FROM federated_identities f WHERE f.user_id=u.id)""").fetchone()[0]
        else:
            admins=conn.execute("""SELECT count(*) FROM users u JOIN federated_identities f
                ON u.id=f.user_id WHERE u.role='admin' AND u.enabled=1 AND f.issuer=?""",
                (identity.issuer,)).fetchone()[0]
    if not admins:
        raise access.AccessDenied("provision an active administrator for the selected authentication mode")
    return {"configuration":"valid","auth_mode":mode,"administrator_present":True,
            "gateway_connectivity_tested":False,"mfa_policy_tested":False,
            "production_approved":False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    subs=p.add_subparsers(dest="action",required=True)
    subs.add_parser("preflight")
    a=subs.add_parser("federated-account-create")
    a.add_argument("workspace")
    a.add_argument("--user-id",required=True)
    a.add_argument("--role",choices=["admin","operator","observer","reviewer"],required=True)
    a.add_argument("--issuer",required=True)
    a.add_argument("--subject-file",help="protected file with one stable upstream subject; otherwise prompt")
    s=subs.add_parser("gateway-secret-create")
    s.add_argument("--out",required=True)
    args=p.parse_args(argv)
    try:
        if args.action=="preflight":
            result=preflight()
        elif args.action=="gateway-secret-create":
            fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"w",encoding="ascii") as f:
                f.write(secrets.token_hex(32)+"\n")
            result={"created":True}
        else:
            if args.subject_file:
                file=Path(args.subject_file)
                if file.is_symlink() or not file.is_file() or (os.name=="posix" and stat.S_IMODE(file.stat().st_mode)&0o077):
                    raise access.AccessDenied("protected subject file required")
                with file.open("r",encoding="ascii") as f:
                    subject=f.read(512).strip()
            else:
                subject=getpass.getpass("Stable upstream subject (not email): ")
            result=federation.provision(_private_workspace(args.workspace),args.user_id,args.role,args.issuer,subject)
        print(json.dumps({"ok":True,"result":result},indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        print(json.dumps({"ok":False,"error":"Deployment operation refused; verify protected configuration and account mapping."}),file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
