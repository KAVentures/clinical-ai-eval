"""Offline scribe assessment workflow."""
import argparse
import json
import sys
from pathlib import Path

from . import scribe_assessment as scribe, formal_review as formal


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    q=sub.add_parser("plan")
    q.add_argument("--spec",required=True)
    q.add_argument("--out",required=True)
    for name in ("exchange","response-template","inspect-responses","prepare"):
        q=sub.add_parser(name)
        q.add_argument("--plan",required=True)
        q.add_argument("--approve-plan",required=True)
        if name in ("inspect-responses","prepare"):
            q.add_argument("--responses",required=True)
        if name=="prepare":
            q.add_argument("workspace")
            q.add_argument("--reviewer",action="append",required=True)
            q.add_argument("--operator",required=True)
            q.add_argument("--confirm-roster-and-reference",action="store_true")
        elif name!="inspect-responses":
            q.add_argument("--out",required=True)
    for name in ("export","submit","analyze"):
        q=sub.add_parser(name)
        q.add_argument("workspace")
        if name=="export":
            q.add_argument("--reviewer",required=True)
            q.add_argument("--out",required=True)
        elif name=="submit":
            q.add_argument("--reviewer",required=True)
            q.add_argument("--file",required=True)
            q.add_argument("--operator",required=True)
            q.add_argument("--confirm-identity",action="store_true")
    args=p.parse_args(argv)
    try:
        if args.action=="plan":
            plan=scribe.build_plan(formal._json(Path(args.spec)))
            formal._publish_once(Path(args.out),plan)
            result={"plan_hash":plan["plan_hash"],"planned_cases":len(plan["items"])}
        elif args.action in ("exchange","response-template","inspect-responses","prepare"):
            plan=scribe.verify_plan(formal._json(Path(args.plan)))
            if plan["plan_hash"]!=args.approve_plan:
                raise ValueError("exact approved plan hash required")
            if args.action in ("exchange","response-template"):
                doc=scribe.exchange(plan) if args.action=="exchange" else scribe.response_template(plan)
                formal._publish_once(Path(args.out),doc)
                result={"plan_hash":plan["plan_hash"],"exported":True}
            else:
                responses=formal._json(Path(args.responses))
                if args.action=="inspect-responses":
                    capture=scribe.capture(plan,responses)
                    result={"response_hash":capture["response_hash"],"outcomes":{
                        status:sum(i["status"]==status for i in capture["items"]) for status in scribe.OUTCOMES},
                        "decision_grade":False,"clinical_review_completed":False}
                else:
                    result=scribe.prepare(plan,responses,args.workspace,args.reviewer,args.operator,
                                confirmed=args.confirm_roster_and_reference)
        elif args.action=="export":
            root=Path(args.workspace).resolve()
            out=Path(args.out).resolve()
            if root==out or root in out.parents:
                raise ValueError("export outside evidence workspace required")
            formal._publish_once(out,scribe.template(args.workspace,args.reviewer))
            result={"exported":True}
        elif args.action=="submit":
            result=scribe.submit(args.workspace,args.reviewer,formal._json(Path(args.file)),args.operator,
                                 confirmed=args.confirm_identity)
        else:
            report=scribe.analyze(args.workspace)
            result={"digest":report["digest"],"summary":report["summary"],"decision_grade":False}
        print(json.dumps(result,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
        print("Scribe assessment refused; verify approved inputs, reviewer and packet.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
