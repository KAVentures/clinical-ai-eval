"""Trusted IT broker management and restricted client MCP launcher."""
import argparse
import json
import re
import secrets
import sys

from . import agent_grants as grants, agent_live as live

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    g=sub.add_parser("generation-create")
    g.add_argument("--out",required=True)
    for name in ("issue","revoke","inventory","serve"):
        q=sub.add_parser(name)
        q.add_argument("workspace")
        if name in ("issue","serve"):
            q.add_argument("--generation-file",required=True)
        if name=="issue":
            q.add_argument("--owner",required=True)
            q.add_argument("--scope",choices=grants.TOOLS,action="append",required=True)
            q.add_argument("--ttl",type=int,required=True)
            q.add_argument("--calls",type=int,required=True)
            q.add_argument("--out",required=True)
            q.add_argument("--confirm-sharing",action="store_true")
        elif name=="revoke":
            q.add_argument("--grant",required=True)
        elif name=="serve":
            q.add_argument("--socket",required=True)
    r=sub.add_parser("mcp")
    r.add_argument("--socket",required=True)
    r.add_argument("--token-file",required=True)
    args=p.parse_args(argv)
    try:
        if args.action=="mcp":
            live.relay(args.socket,args.token_file)
            return 0
        if args.action=="serve":
            live.serve(args.workspace,args.socket,args.generation_file)
            return 0
        if args.action=="generation-create":
            grants.write_private(args.out,secrets.token_hex(32))
            result={"created":True}
        elif args.action=="issue":
            for path in (args.out,args.generation_file):
                grants.outside_workspace(args.workspace,path)
            epoch=grants.read_private(args.generation_file,re.compile(r"[0-9a-f]{64}"))
            result=grants.issue(args.workspace,args.owner,args.scope,args.ttl,args.calls,epoch,
                                confirmed=args.confirm_sharing)
            token=result.pop("token")
            try:
                grants.write_private(args.out,token)
            except BaseException:
                grants.revoke(args.workspace,result["grant_id"])
                raise
        elif args.action=="revoke":
            grants.revoke(args.workspace,args.grant)
            result={"revoked":True}
        else:
            result=grants.inventory(args.workspace)
        print(json.dumps({"ok":True,"result":result},indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        print("Agent operation refused; check approved configuration and grant.",file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
