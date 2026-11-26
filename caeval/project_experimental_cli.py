"""Machine-readable offline experimental-project CLI."""
import json
from pathlib import Path

from . import experimental_evidence as evidence, formal_review as formal
from . import project_experimental as lane


def configure(parser):
    sub=parser.add_subparsers(dest="action",required=True)
    q=sub.add_parser("freeze")
    q.add_argument("--project",required=True)
    q.add_argument("--specs",required=True,help="JSON mapping module names to scientific specifications")
    q.add_argument("--out",required=True)
    for action in ("module-plan","exchange","response-template","prepare-review",
                   "complete-module","assemble","verify"):
        q=sub.add_parser(action)
        q.add_argument("--project",required=True)
        q.add_argument("--plan",required=True)
        q.add_argument("--approve-plan",required=True)
        if action in ("module-plan","exchange","response-template","prepare-review","complete-module"):
            q.add_argument("--kind",choices=sorted(lane.SUITES),required=True)
        if action=="prepare-review":
            q.add_argument("--responses",required=True)
            q.add_argument("--workspace",required=True)
            q.add_argument("--reviewer",action="append",required=True)
            q.add_argument("--validity-reviewer",action="append",default=[])
            q.add_argument("--operator",required=True)
            q.add_argument("--confirm",action="store_true")
        elif action=="complete-module":
            q.add_argument("--source",required=True,help="decision response JSON or completed review directory")
            q.add_argument("--confirm",action="store_true")
        elif action=="assemble":
            q.add_argument("--package",action="append",required=True,help="module=package.json")
        elif action=="verify":
            q.add_argument("--result",required=True)
            q.add_argument("--expected-digest")
        if action not in ("verify","prepare-review"):
            q.add_argument("--out",required=True)
    parser.set_defaults(func=run)


def _packages(entries):
    packages={}
    for entry in entries:
        kind,sep,path=entry.partition("=")
        if not sep or kind not in lane.SUITES or kind in packages or not path:
            lane.fail("invalid or duplicate module package argument")
        packages[kind]=evidence.load(path)
    return packages


def run(args):
    try:
        action=args.action
        if action=="freeze":
            doc=lane.build_plan(args.project,evidence.load(args.specs))
            result={"digest":doc["digest"],"planned_modules":sorted(doc["modules"]),
                    "unplanned_related_modules":doc["unplanned_related_modules"]}
        else:
            frozen=evidence.load(args.plan)
            bound=(args.project,frozen,args.approve_plan)
            if action=="module-plan":
                doc=lane.module_plan(*bound,args.kind)
                result={"plan_hash":doc["plan_hash"]}
            elif action=="exchange":
                doc=lane.exchange(*bound,args.kind)
                result={"plan_hash":doc["plan_hash"],"exported":True}
            elif action=="response-template":
                doc=lane.response_template(*bound,args.kind)
                result={"exported":True,"contains_withheld_material":True}
            elif action=="prepare-review":
                result=lane.prepare_review(*bound,args.kind,evidence.load(args.responses),
                         args.workspace,args.reviewer,args.validity_reviewer,args.operator,
                         confirmed=args.confirm)
            elif action=="complete-module":
                doc=lane.complete_module(*bound,args.kind,args.source,confirmed=args.confirm)
                result={"digest":doc["digest"],"report_digest":doc["report"]["digest"]}
            elif action=="assemble":
                doc=lane.assemble(*bound,_packages(args.package))
                result={"digest":doc["digest"],"all_planned_modules_replayed":doc["all_planned_modules_replayed"],
                        "uncompleted_planned_modules":doc["uncompleted_planned_modules"],
                        "unplanned_related_modules":doc["unplanned_related_modules"]}
            else:
                result=lane.verify_result(*bound,evidence.load(args.result),expected_digest=args.expected_digest)
        if action not in ("prepare-review","verify"):
            # Existing publisher refuses replacement and uses owner-only files.
            # Do not publish if the pretty JSON exceeds the common exchange bound.
            formal._publish_once(Path(args.out),doc)
        print(json.dumps({"ok":True,"result":{**result,"decision_grade":False}},
                         ensure_ascii=False,allow_nan=False))
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,AttributeError,IndexError):
        print(json.dumps({"ok":False,"error":"experimental project operation refused",
                          "decision_grade":False}))
        raise SystemExit(1)
