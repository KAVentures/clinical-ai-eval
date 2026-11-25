"""Offline operator CLI for independent patient episode reviews."""
import argparse
import json
import os
import sys
from pathlib import Path

from . import patient_review as review, buyer_manual as manual, formal_review as formal


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    for name in ("prepare","export","submit","analyze"):
        q=sub.add_parser(name)
        q.add_argument("source")
        q.add_argument("review_workspace")
        if name=="prepare":
            q.add_argument("--approve-manifest",required=True)
            q.add_argument("--reviewer",action="append",required=True)
            q.add_argument("--operator",required=True)
            q.add_argument("--confirm-roster",action="store_true")
        elif name=="export":
            q.add_argument("--reviewer",required=True)
            q.add_argument("--out",required=True)
        elif name=="submit":
            q.add_argument("--reviewer",required=True)
            q.add_argument("--file",required=True)
            q.add_argument("--operator",required=True)
            q.add_argument("--confirm-identity",action="store_true")
    a=p.parse_args(argv)
    try:
        if a.action=="prepare":
            result=review.prepare(a.source,a.approve_manifest,a.review_workspace,a.reviewer,a.operator,confirmed=a.confirm_roster)
        elif a.action=="export":
            doc=review.template(a.source,a.review_workspace,a.reviewer)
            raw=(json.dumps(doc,ensure_ascii=False,indent=2)+"\n").encode()
            if len(raw)>review.MAX_PACKET_BYTES:
                raise ValueError("packet too large")
            out=Path(a.out).absolute()
            # Never write exports into source or derivative evidence.
            if any(base==out or base in out.resolve().parents for base in (Path(a.source).resolve(),Path(a.review_workspace).resolve())):
                raise ValueError("export must be outside evidence directories")
            fd=os.open(str(out),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,"wb") as f:
                f.write(raw)
            result={"exported":True}
        elif a.action=="submit":
            doc=manual._parse(formal._read(Path(a.file),review.MAX_PACKET_BYTES))
            result=review.submit(a.source,a.review_workspace,a.reviewer,doc,a.operator,confirmed=a.confirm_identity)
        else:
            result=review.analyze(a.source,a.review_workspace)
        print(json.dumps(result,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError):
        print("Patient review operation refused; verify approved evidence, roster and packet.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
