"""Offline paired clinical robustness research CLI."""
import argparse
import json
import sys
from pathlib import Path

from . import robustness_assessment as audit, formal_review as formal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    for action in ("exchange", "response-template", "inspect-responses", "prepare"):
        p = sub.add_parser(action)
        p.add_argument("--plan", required=True)
        p.add_argument("--approve-plan", required=True)
        if action in ("inspect-responses", "prepare"):
            p.add_argument("--responses", required=True)
        if action == "prepare":
            p.add_argument("workspace")
            p.add_argument("--validity-reviewer", action="append", required=True)
            p.add_argument("--answer-reviewer", action="append", required=True)
            p.add_argument("--operator", required=True)
            p.add_argument("--confirm-roster-and-reference", action="store_true")
        elif action != "inspect-responses":
            p.add_argument("--out", required=True)
    for action in ("export", "submit", "analyze"):
        p = sub.add_parser(action)
        p.add_argument("workspace")
        if action == "export":
            p.add_argument("--reviewer", required=True)
            p.add_argument("--out", required=True)
        elif action == "submit":
            p.add_argument("--reviewer", required=True)
            p.add_argument("--file", required=True)
            p.add_argument("--operator", required=True)
            p.add_argument("--confirm-identity", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            plan = audit.build_plan(formal._json(Path(args.spec)))
            formal._publish_once(Path(args.out), plan)
            result = {"plan_hash":plan["plan_hash"],"planned_pairs":len(plan["pairs"])}
        elif args.action in ("exchange", "response-template", "inspect-responses", "prepare"):
            plan = audit.verify_plan(formal._json(Path(args.plan)))
            if plan["plan_hash"] != args.approve_plan:
                raise ValueError("exact approved plan hash required")
            if args.action in ("exchange", "response-template"):
                doc = audit.exchange(plan) if args.action == "exchange" else audit.response_template(plan)
                formal._publish_once(Path(args.out), doc)
                result = {"plan_hash":plan["plan_hash"],"exported":True}
            else:
                responses = formal._json(Path(args.responses))
                if args.action == "inspect-responses":
                    e = audit.capture(plan, responses)
                    result = {"response_hash":e["response_hash"],"outcomes":{
                        status:sum(a["status"]==status for a in e["arms"]) for status in audit.OUTCOMES},
                        "decision_grade":False,"clinical_review_completed":False}
                else:
                    result = audit.prepare(plan, responses, args.workspace, args.validity_reviewer,
                        args.answer_reviewer, args.operator, confirmed=args.confirm_roster_and_reference)
        elif args.action == "export":
            root = Path(args.workspace).resolve()
            out = Path(args.out).resolve()
            if root == out or root in out.parents:
                raise ValueError("export outside evidence workspace required")
            formal._publish_once(out, audit.template(args.workspace, args.reviewer))
            result = {"exported":True}
        elif args.action == "submit":
            result = audit.submit(args.workspace, args.reviewer, formal._json(Path(args.file)),
                                  args.operator, confirmed=args.confirm_identity)
        else:
            report = audit.analyze(args.workspace)
            result = {"digest":report["digest"],"summary":report["summary"],"decision_grade":False}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        print("Robustness assessment refused; verify approved plan, responses and reviewer packet.",file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
