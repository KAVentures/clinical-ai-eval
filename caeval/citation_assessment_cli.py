"""Offline controlled citation-assessment CLI."""
import argparse
import json
import sys
from pathlib import Path
from . import citation_assessment as audit, formal_review as formal


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    q=sub.add_parser("plan")
    q.add_argument("--spec",required=True)
    q.add_argument("--out",required=True)
    q=sub.add_parser("prompts")
    q.add_argument("--plan",required=True)
    q.add_argument("--approve-plan",required=True)
    q.add_argument("--out",required=True)
    q=sub.add_parser("prepare")
    q.add_argument("workspace")
    q.add_argument("--plan",required=True)
    q.add_argument("--approve-plan",required=True)
    q.add_argument("--traces",required=True)
    q.add_argument("--reviewer",action="append",required=True)
    q.add_argument("--operator",required=True)
    q.add_argument("--confirm-roster-and-reference",action="store_true")
    q=sub.add_parser("export")
    q.add_argument("workspace")
    q.add_argument("--reviewer",required=True)
    q.add_argument("--out",required=True)
    q=sub.add_parser("submit")
    q.add_argument("workspace")
    q.add_argument("--reviewer",required=True)
    q.add_argument("--file",required=True)
    q.add_argument("--operator",required=True)
    q.add_argument("--confirm-identity",action="store_true")
    q=sub.add_parser("analyze")
    q.add_argument("workspace")
    args=p.parse_args(argv)
    try:
        if args.action=="plan":
            plan=audit.build_plan(formal._json(Path(args.spec)))
            formal._publish_once(Path(args.out),plan)
            result={"plan_hash":plan["plan_hash"],"n_queries":len(plan["queries"])}
        elif args.action in ("prompts","prepare"):
            plan=audit.verify_plan(formal._json(Path(args.plan)))
            if plan["plan_hash"]!=args.approve_plan:
                raise ValueError("exact plan approval required")
            if args.action=="prompts":
                formal._publish_once(Path(args.out),audit.prompts(plan))
                result={"exported":True,"plan_hash":plan["plan_hash"]}
            else:
                result=audit.prepare(plan,formal._json(Path(args.traces)),args.workspace,
                         args.reviewer,args.operator,confirmed=args.confirm_roster_and_reference)
        elif args.action=="export":
            path=Path(args.out).resolve()
            root=Path(args.workspace).resolve()
            if root==path or root in path.parents:
                raise ValueError("export outside evidence workspace")
            formal._publish_once(path,audit.template(args.workspace,args.reviewer))
            result={"exported":True}
        elif args.action=="submit":
            result=audit.submit(args.workspace,args.reviewer,formal._json(Path(args.file)),
                                args.operator,confirmed=args.confirm_identity)
        else:
            report=audit.analyze(args.workspace)
            result={"digest":report["digest"],"summary":report["summary"],"decision_grade":False}
        print(json.dumps(result,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
        print("Citation assessment refused; verify approved plan, trace inventory, reviewer and packet.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
