"""Offline structured decision certificate assessment."""
import argparse
import json
import sys
from pathlib import Path

from . import decision_assessment as audit, formal_review as formal


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="action",required=True)
    p=sub.add_parser("plan")
    p.add_argument("--spec",required=True)
    p.add_argument("--out",required=True)
    for action in ("exchange","response-template","assess"):
        p=sub.add_parser(action)
        p.add_argument("--plan",required=True)
        p.add_argument("--approve-plan",required=True)
        p.add_argument("--out",required=True)
        if action=="assess":
            p.add_argument("--responses",required=True)
            p.add_argument("--confirm-authored-reference",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.action=="plan":
            doc=audit.build_plan(formal._json(Path(args.spec)))
            result={"plan_hash":doc["plan_hash"],"planned_cases":len(doc["items"])}
        else:
            plan=audit.verify_plan(formal._json(Path(args.plan)))
            if plan["plan_hash"]!=args.approve_plan:
                raise ValueError("exact approved plan hash required")
            if args.action=="exchange":
                doc=audit.exchange(plan)
            elif args.action=="response-template":
                doc=audit.response_template(plan)
            else:
                if not args.confirm_authored_reference:
                    raise ValueError("explicit reference approval required")
                doc=audit.assess(plan,formal._json(Path(args.responses)))
            result={"plan_hash":plan["plan_hash"],"decision_grade":False}
            if args.action=="assess":
                result.update(digest=doc["digest"],summary=doc["summary"])
        formal._publish_once(Path(args.out),doc)
        print(json.dumps(result,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError):
        print("Decision assessment refused; verify exact plan, reference and response inventory.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
