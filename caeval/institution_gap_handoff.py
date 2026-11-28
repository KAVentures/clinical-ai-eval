"""Explicit audited institutional export and offline structural replay."""
import json
import sqlite3
from pathlib import Path
from . import unified_assessment as u, unified_work as work
from . import unified_evidence_gaps as gaps, unified_gap_handoff as handoff
from . import institution_access as access, diagnostic_imaging as d

EVENT="unified-evidence-gap-handoff-exported"


def _verify_coverage(coverage):
    return u.verify_export(u._package(coverage))


def verify(package,expected_digest=None):
    return handoff.verify(package,expected_digest,project=gaps.project,verify_coverage=_verify_coverage)


def _capture(directory,conn,actor,ident):
    u._actor(conn,actor,"admin")
    u._inventory(conn)
    status=u._status(directory,conn,actor,ident)
    package=handoff.build(status,work._project(directory,conn,actor,status),project=gaps.project)
    verify(package,package["digest"])
    if len(json.dumps(package,ensure_ascii=True,allow_nan=False).encode("ascii"))>handoff.MAX_BYTES:
        raise access.AccessDenied("handoff exceeds size limit")
    return package


def preview(directory,actor,ident):
    root=Path(directory)
    with access.db(root) as conn:
        u._schema(conn)
        conn.execute("BEGIN")
        return _capture(root,conn,actor,ident)


def export(directory,actor,ident,approval,*,confirmed=False):
    if confirmed is not True or not handoff.hex64(approval):
        raise access.AccessDenied("explicit exact handoff approval required")
    root=Path(directory)
    with access.db(root) as conn:
        u._schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        package=_capture(root,conn,actor,ident)
        if package["digest"]!=approval:
            raise access.AccessDenied("programme handoff changed; preview again")
        u._audit(conn,actor,EVENT,ident+":"+approval)
        return package


def configure(parser):
    parser.add_argument("action",choices=["verify"])
    parser.add_argument("file")
    parser.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    try:
        package=d._read(args.file)
        if len(json.dumps(package,ensure_ascii=True,allow_nan=False).encode("ascii"))>handoff.MAX_BYTES:
            raise ValueError("handoff exceeds size limit")
        print(json.dumps(verify(package,args.expected_digest),sort_keys=True,allow_nan=False))
    except u.ERRORS+(sqlite3.Error,RecursionError):
        raise SystemExit("Unified evidence handoff verification refused.")
