"""Trusted worker for one explicitly approved conversation assessment."""
import argparse
import json
import sys
from . import institution_conversation as conversation


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("workspace")
    p.add_argument("--job",required=True)
    p.add_argument("--approve-binding",required=True)
    p.add_argument("--allow-candidate-calls",action="store_true")
    p.add_argument("--allow-model-calls",action="store_true")
    args=p.parse_args(argv)
    try:
        result=conversation.execute(args.workspace,args.job,args.approve_binding,
                    allow_candidate_calls=args.allow_candidate_calls,
                    allow_model_calls=args.allow_model_calls)
        print(json.dumps(result,indent=2))
        return 0 if result["status"]=="completed" else 1
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        print("Conversation execution refused; verify approved inputs and binding.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
