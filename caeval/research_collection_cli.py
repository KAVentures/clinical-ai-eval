"""Trusted local research collection worker; never invoked by browser requests."""
import argparse
import json
import sys
from . import research_collection as collection


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    run=sub.add_parser("execute")
    run.add_argument("workspace")
    run.add_argument("study")
    run.add_argument("--approve-binding",required=True)
    run.add_argument("--allow-candidate-calls",action="store_true")
    stop=sub.add_parser("abandon")
    stop.add_argument("workspace")
    stop.add_argument("study")
    stop.add_argument("--worker-stopped",action="store_true")
    args=p.parse_args(argv)
    try:
        if args.action=="execute":
            result=collection.execute(args.workspace,args.study,args.approve_binding,
                                      allow_candidate_calls=args.allow_candidate_calls)
        else:
            collection.abandon(args.workspace,args.study,worker_stopped=args.worker_stopped)
            result={"interrupted":True}
        print(json.dumps(result,indent=2))
        return 0
    except (ValueError,RuntimeError,OSError,TypeError,KeyError):
        print("Research collection refused or interrupted; inspect study state locally. Do not retry running work.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
