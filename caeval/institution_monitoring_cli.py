"""Trusted supervised monitoring worker. Invoke from the institution's scheduler."""
import argparse
import json
import sys

from . import institution_monitoring as monitoring


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace")
    parser.add_argument("--schedule",required=True)
    parser.add_argument("--approve-binding",required=True)
    parser.add_argument("--allow-candidate-calls",action="store_true")
    parser.add_argument("--allow-model-calls",action="store_true")
    args=parser.parse_args(argv)
    try:
        result=monitoring.run_due(args.workspace,args.schedule,args.approve_binding,
                                  allow_candidate_calls=args.allow_candidate_calls,
                                  allow_model_calls=args.allow_model_calls)
        print(json.dumps(result,indent=2))
        return 0 if result["status"] in ("not_due","completed") else 1
    except (OSError,ValueError,RuntimeError,TypeError,KeyError):
        print("Monitoring occurrence refused; inspect approved scope and stored occurrence state.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
