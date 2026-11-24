"""Trusted operator CLI for offline formal review handoff.

Run via python -m caeval.formal_review_cli. No browser, model or network
permissions are granted. Exports contain confidential clinical case material.
"""
import argparse
import json
import sys
from pathlib import Path

from . import buyer_agent as buyer, buyer_manual as manual, formal_review as review


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="action",required=True)
    p=commands.add_parser("prepare")
    p.add_argument("workspace")
    p.add_argument("--job",required=True)
    p.add_argument("--family",required=True)
    p.add_argument("--reviewer",action="append",required=True)
    p.add_argument("--validity-reviewer",required=True)
    p.add_argument("--operator",required=True)
    p.add_argument("--confirm-roster",action="store_true")
    for name in ("export","submit","status","analyze","consensus-export","consensus-submit"):
        p=commands.add_parser(name)
        p.add_argument("workspace")
        p.add_argument("--handoff",required=True)
        if name in ("export","submit"):
            p.add_argument("--reviewer",required=True)
        if name in ("export","consensus-export"):
            p.add_argument("--out",required=True)
        if name in ("submit","consensus-submit"):
            p.add_argument("--file",required=True)
            p.add_argument("--operator",required=True)
            p.add_argument("--confirm-identity",action="store_true")
            p.add_argument("--confirm-consensus",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.action=="prepare":
            result=review.prepare(args.workspace,args.job,args.family,args.reviewer,
                                  args.validity_reviewer,args.operator,confirm_roster=args.confirm_roster)
        elif args.action in ("export","consensus-export"):
            document=(review.template(args.workspace,args.handoff,args.reviewer) if args.action=="export"
                      else review.consensus_template(args.workspace,args.handoff))
            raw=(json.dumps(document,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
            if len(raw)>manual.MAX_REQUEST_BYTES:
                raise buyer.BuyerError("packet exceeds supported import size")
            # Explicit export to approved local path, never overwrite.
            import os
            fd=os.open(args.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"wb") as f:
                f.write(raw)
            result={"exported":True,"kind":args.action}
        elif args.action=="submit":
            document=manual._parse(review._read(Path(args.file),manual.MAX_REQUEST_BYTES))
            result=review.submit(args.workspace,args.handoff,args.reviewer,document,
                                 args.operator,confirm_identity=args.confirm_identity)
        elif args.action=="consensus-submit":
            document=manual._parse(review._read(Path(args.file),manual.MAX_REQUEST_BYTES))
            result=review.submit_consensus(args.workspace,args.handoff,document,args.operator,
                                           confirm_consensus=args.confirm_consensus)
        elif args.action=="status":
            result=review.status(args.workspace,args.handoff)
        else:
            result=review.analyze(args.workspace,args.handoff)
        print(json.dumps({"ok":True,"result":result},ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError):
        # Do not echo untrusted packet contents, clinical text or credentials.
        print(json.dumps({"ok":False,"error":"Formal review operation refused; check source, roster, packet and permissions."}),
              file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
