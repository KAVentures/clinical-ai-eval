"""Explicit release of one canonical family evidence archive to one request.

Metadata grants alone never expose raw prompts, outputs or reviews. IT first
writes a private preview archive outside the workspace, reviews its contents,
then approves its exact SHA-256. Download rechecks both the live canonical
job and the exact archive bytes. No arbitrary files or paths are exported.
"""
import hashlib
import io
import os
import stat
import tempfile
import zipfile
from pathlib import Path

from . import (evaluator_service as service,institution_access as access,
               buyer_jobs as jobs,buyer_manual as manual,buyer_research as research,
               manifest,buyer_plan,agent_grants)

MAX_ARCHIVE=16*1024*1024
MAX_FILES=1000
EVENT="evaluator-service-package-release-approved"


def _schema(conn):
    service._schema(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS evaluator_package_releases(
        request_id TEXT NOT NULL REFERENCES evaluator_service_requests(id),
        family TEXT NOT NULL, digest TEXT NOT NULL, manifest_hash TEXT NOT NULL,
        audit_seq INTEGER NOT NULL UNIQUE, PRIMARY KEY(request_id,family))""")


def _raw(ws,rel,limit):
    if not research._relative(rel):
        service._fail()
    p=ws
    for part in rel.split("/"):
        p=p/part
        if p.is_symlink():
            service._fail()
    if not p.is_file() or p.stat().st_nlink!=1:
        service._fail()
    with p.open("rb") as f:
        raw=f.read(limit+1)
    if len(raw)>limit:
        service._fail()
    return raw


def _archive(directory,row,family):
    manual._identifier(family)
    if not row["job_id"]:
        service._fail()
    job=jobs.status(directory,row["job_id"])
    if job["status"]!="completed" or job["assessment_id"]!=row["assessment_id"]:
        service._fail()
    packages=[p for p in job["result"]["packages"] if p["family_id"]==family]
    if len(packages)!=1:
        service._fail()
    reference=packages[0]
    ws=jobs._job_path(directory,row["job_id"])/"out"/("run_"+family)
    if ws.is_symlink() or ws.parent.is_symlink() or not ws.is_dir():
        service._fail()
    meta=research._preflight(ws)
    if (meta["manifest_hash"]!=reference["manifest_hash"] or
            manifest.verify_manifest(ws)["verdict"]!=manifest.VALID):
        service._fail()
    # No unmanifested diagnostic files, workspace configuration, or secrets.
    names={manifest.MANIFEST_FILE}
    names.update(v["path"] for v in meta["artifacts"].values() if v["present"])
    names.update(meta["review_inputs"])
    if len(names)>MAX_FILES:
        service._fail()
    total=0
    data={}
    for rel in sorted(names):
        raw=_raw(ws,rel,MAX_ARCHIVE-total)
        data[rel]=raw
        total+=len(raw)
    # Verify the actual snapshot being released, not merely the live source.
    with tempfile.TemporaryDirectory(prefix="caeval-release-") as tmp:
        dest=Path(tmp)
        for rel,raw in data.items():
            p=dest/rel
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(raw)
        snap=research._preflight(dest)
        if (snap!=meta or manifest.verify_manifest(dest)["verdict"]!=manifest.VALID):
            service._fail()
    if jobs.status(directory,row["job_id"])!=job:
        service._fail()
    output=io.BytesIO()
    with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_STORED) as z:
        for rel,raw in data.items():
            info=zipfile.ZipInfo(rel,date_time=(1980,1,1,0,0,0))
            info.create_system=3
            info.external_attr=(stat.S_IFREG|0o600)<<16
            z.writestr(info,raw)
    raw=output.getvalue()
    if len(raw)>MAX_ARCHIVE:
        service._fail()
    return raw,{"request_id":row["id"],"assessment_id":row["assessment_id"],
                "family_id":family,"manifest_hash":reference["manifest_hash"],
                "sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw),
                "decision_grade":False}


def _local_request(directory,rid):
    with access.db(directory) as conn:
        _schema(conn)
        row=dict(service._active_request(conn,rid))
        if row["job_id"] and row["job_id"].startswith("job-service-"):
            service._queue_approval(conn,row["job_id"],row["assessment_id"])
        return row


def preview(directory,rid,family,out):
    agent_grants.outside_workspace(directory,out)
    row=_local_request(directory,rid)
    raw,metadata=_archive(directory,row,family)
    fd=os.open(str(out),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw)
    return metadata


def approve(directory,rid,family,digest,*,confirmed=False):
    if confirmed is not True:
        service._fail()
    manual._identifier(digest,digest=True)
    row=_local_request(directory,rid)
    _,metadata=_archive(directory,row,family)
    if metadata["sha256"]!=digest:
        service._fail()
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        current=dict(service._active_request(conn,rid))
        if current!=row:
            service._fail()
        if row["job_id"].startswith("job-service-"):
            service._queue_approval(conn,row["job_id"],row["assessment_id"])
        old=conn.execute("SELECT * FROM evaluator_package_releases WHERE request_id=? AND family=?",(rid,family)).fetchone()
        if old:
            _check(conn,row,family,metadata)
            return metadata
        object_id=_event_id(row,family,metadata)
        access._audit(conn,"local-admin",EVENT,object_id)
        seq=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO evaluator_package_releases VALUES(?,?,?,?,?)",
                     (rid,family,digest,metadata["manifest_hash"],seq))
    return metadata


def _event_id(row,family,meta):
    return buyer_plan._digest([row["id"],row["grant_id"],row["assessment_id"],row["job_id"],
                              family,meta["manifest_hash"],meta["sha256"]])


def _check(conn,row,family,meta):
    release=conn.execute("SELECT * FROM evaluator_package_releases WHERE request_id=? AND family=?",(row["id"],family)).fetchone()
    if (not release or release["digest"]!=meta["sha256"] or release["manifest_hash"]!=meta["manifest_hash"]):
        service._fail()
    events=conn.execute("SELECT seq,actor FROM audit WHERE action=? AND object_id=? AND outcome='ok'",
                        (EVENT,_event_id(row,family,meta))).fetchall()
    if len(events)!=1 or events[0]["seq"]!=release["audit_seq"] or events[0]["actor"]!="local-admin":
        service._fail()


def download(directory,token,rid,family):
    manual._identifier(family)
    with access.db(directory) as conn:
        _schema(conn)
        grant=service._grant(conn,token)
        row=dict(service._request(conn,grant["id"],rid))
        if row["assessment_id"]!=grant["assessment_id"] or not row["job_id"]:
            service._fail()
        if row["job_id"].startswith("job-service-"):
            service._queue_approval(conn,row["job_id"],row["assessment_id"])
        if not conn.execute("SELECT 1 FROM evaluator_package_releases WHERE request_id=? AND family=?",(rid,family)).fetchone():
            service._fail()
    raw,metadata=_archive(directory,row,family)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        grant=service._grant(conn,token)
        if dict(service._request(conn,grant["id"],rid))!=row:
            service._fail()
        if row["job_id"].startswith("job-service-"):
            service._queue_approval(conn,row["job_id"],row["assessment_id"])
        _check(conn,row,family,metadata)
        access._audit(conn,grant["owner"],"evaluator-service-package-downloaded",_event_id(row,family,metadata))
    return raw,metadata
