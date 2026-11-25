"""Trusted institutional IT export of local escalation metadata.

Does not deliver external notifications. The institution decides whether,
where and how to route the metadata under its approved policies.
"""
import argparse
import json
import sys

from . import institution_access as access, institution_escalation as escalation


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace")
    parser.add_argument("--as-admin",required=True,help="Active institutional administrator account ID")
    parser.add_argument("--confirm-trusted-local-export",action="store_true")
    args=parser.parse_args(argv)
    if not args.confirm_trusted_local_export:
        print("Explicit trusted local export approval required.",file=sys.stderr)
        return 1
    try:
        data=escalation.overview(args.workspace,{"user_id":args.as_admin,"role":"admin"})
        print(json.dumps(data,indent=2,ensure_ascii=True))
        return 0
    except (OSError,ValueError,RuntimeError,TypeError,KeyError):
        print("Escalation inventory unavailable; investigate before relying on it.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
