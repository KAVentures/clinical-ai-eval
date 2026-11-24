"""Offline, bounded, verifiable backup and new-workspace recovery.

Trusted IT must stop all services and workers first and supply approved encrypted
storage. This is a file-integrity checkpoint, not clinical evidence validation,
a digital signature, or a live distributed snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from pathlib import Path, PurePosixPath

from . import buyer_agent as buyer, buyer_plan

MAX_FILES=20000
MAX_TOTAL=20*1024**3
MAX_MANIFEST=16*1024**2
MANIFEST="backup-manifest.json"
SCHEMA="caeval-workspace-backup/1"
TRANSIENT_PREFIXES=(".staging-", ".analysis-", ".upload-", ".buyer-",
                    ".caeval-reverify-", ".ingest-", ".comparison-", "questionnaire-")
SQLITE_SIDECARS=("-wal","-shm","-journal")


def _path(value):
    p=Path(value).expanduser()
    if p.is_symlink():
        raise buyer.BuyerError("linked workspace or backup root refused")
    return p.resolve()


def _relative(value):
    if (not isinstance(value,str) or not value or len(value)>2048
            or "\\" in value or ":" in value or any(ord(c)<32 for c in value)):
        raise buyer.BuyerError("invalid backup relative path")
    p=PurePosixPath(value)
    if p.is_absolute() or any(x in ("",".","..") for x in value.split("/")):
        raise buyer.BuyerError("invalid backup relative path")
    return p


def _inside(root,p):
    try:
        return p.relative_to(root)
    except ValueError:
        return None


def _inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise buyer.BuyerError("workspace directory required")
    files={}
    dirs=[]
    total=0
    for here,folders,names in os.walk(root,followlinks=False):
        base=Path(here)
        for name in folders+names:
            p=base/name
            rel=p.relative_to(root).as_posix()
            _relative(rel)
            if (name.startswith(TRANSIENT_PREFIXES) and name!=".buyer-write.sqlite3"
                    or name.endswith(SQLITE_SIDECARS)):
                raise buyer.BuyerError("transient data or SQLite sidecar present; stop services and investigate")
            info=p.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise buyer.BuyerError("linked workspace entries refused")
            if stat.S_ISDIR(info.st_mode):
                dirs.append(rel)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink!=1:
                    raise buyer.BuyerError("hard-linked workspace entries refused")
                total+=info.st_size
                files[rel]=(info.st_size,info.st_mtime_ns,info.st_ino)
            else:
                raise buyer.BuyerError("special workspace entries refused")
            if len(files)+len(dirs)>MAX_FILES or total>MAX_TOTAL:
                raise buyer.BuyerError("workspace exceeds backup limits")
    return sorted(dirs),files


def _read_file(source, expected, target=None):
    if source.is_symlink():
        raise buyer.BuyerError("linked source file refused")
    flags=os.O_RDONLY|getattr(os,"O_BINARY",0)|getattr(os,"O_NOFOLLOW",0)
    fd=os.open(str(source),flags)
    digest=hashlib.sha256()
    total=0
    out=None
    try:
        with os.fdopen(fd,"rb") as inp:
            before=os.fstat(inp.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink!=1
                    or (before.st_size,before.st_mtime_ns,before.st_ino)!=expected):
                raise buyer.BuyerError("source changed during backup")
            if target is not None:
                ofd=os.open(str(target),os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_BINARY",0),0o600)
                out=os.fdopen(ofd,"wb")
            for block in iter(lambda: inp.read(1024*1024),b""):
                total+=len(block)
                if total>expected[0]:
                    raise buyer.BuyerError("source grew during backup")
                digest.update(block)
                if out is not None:
                    out.write(block)
            after=os.fstat(inp.fileno())
            if (total!=expected[0] or (after.st_size,after.st_mtime_ns,after.st_ino)!=expected):
                raise buyer.BuyerError("source changed during backup")
            if out is not None:
                out.flush()
                os.fsync(out.fileno())
    finally:
        if out is not None:
            out.close()
    return {"bytes":total,"sha256":digest.hexdigest()}


def _sqlite_check(root):
    """Read-only integrity and active-work checks. No implicit crash recovery."""
    for rel,table,column in (("institution/access.sqlite3",None,None),
                             ("evaluation-jobs/jobs.sqlite3","jobs","status"),
                             ("candidate-collections/collections.sqlite3","collections","state")):
        p=root/rel
        if not p.exists():
            continue
        if p.is_symlink() or not p.is_file():
            raise buyer.BuyerError("invalid workspace database")
        try:
            conn=sqlite3.connect(p.as_uri()+"?mode=ro",uri=True,timeout=1)
        except sqlite3.Error as exc:
            raise buyer.BuyerError("workspace database could not be opened read-only") from exc
        try:
            if conn.execute("PRAGMA quick_check").fetchall()!=[("ok",)]:
                raise buyer.BuyerError("workspace database failed integrity check")
            if rel=="institution/access.sqlite3":
                for name,column in (("conversation_jobs","status"),("monitoring_occurrences","state")):
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone():
                        if conn.execute("SELECT count(*) FROM "+name+" WHERE "+column+"='running'").fetchone()[0]:
                            raise buyer.BuyerError("running institutional work; investigate before checkpoint")
            if table is not None:
                # Fixed identifiers above, never user-controlled SQL.
                if conn.execute("SELECT count(*) FROM "+table+" WHERE "+column+"='running'").fetchone()[0]:
                    raise buyer.BuyerError("running jobs present; investigate before checkpoint")
        except sqlite3.Error as exc:
            raise buyer.BuyerError("workspace database could not be verified") from exc
        finally:
            conn.close()


def _procurement_paths(root,source_root):
    """Verify procurement package locations are self-contained and relocatable."""
    path=root/"assessment"/"procurement.json"
    if not path.exists():
        return None
    from . import procurement_workflow as pw
    data=pw.load(root/"assessment")
    changed=False
    for results in data["results"].values():
        for item in results.values():
            if item.get("ingestion")!="verified_package":
                continue
            package=item.get("package")
            if not isinstance(package,dict) or not isinstance(package.get("workspace"),str):
                raise buyer.BuyerError("verified procurement package has no location")
            value=Path(package["workspace"])
            if value.is_absolute():
                rel=_inside(source_root,value)
                if rel is None:
                    raise buyer.BuyerError("external procurement package; stage locally before backup")
                rel=rel.as_posix()
            else:
                rel=value.as_posix()
            _relative(rel)
            actual=root/rel
            if not actual.is_dir() or actual.is_symlink() or _inside(root,actual.resolve()) is None:
                raise buyer.BuyerError("procurement package missing from backup workspace")
            if package["workspace"]!=rel:
                package["workspace"]=rel
                changed=True
    return data if changed else None


def _manifest(path):
    p=path/MANIFEST
    if p.is_symlink() or not p.is_file() or p.stat().st_size>MAX_MANIFEST:
        raise buyer.BuyerError("missing or oversized backup manifest")
    def unique(pairs):
        obj={}
        for k,v in pairs:
            if k in obj:
                raise buyer.BuyerError("duplicate backup manifest field")
            obj[k]=v
        return obj
    with p.open("rb") as f:
        raw=f.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST:
        raise buyer.BuyerError("backup manifest exceeds limit")
    doc=json.loads(raw.decode("utf-8"),object_pairs_hook=unique)
    if not isinstance(doc,dict) or set(doc)!={"schema","source_root","directories","files","backup_id"} or doc["schema"]!=SCHEMA:
        raise buyer.BuyerError("unsupported backup manifest")
    if buyer_plan._digest({k:v for k,v in doc.items() if k!="backup_id"})!=doc["backup_id"]:
        raise buyer.BuyerError("backup manifest fingerprint mismatch")
    if not isinstance(doc["source_root"],str) or not Path(doc["source_root"]).is_absolute():
        raise buyer.BuyerError("invalid backup origin")
    if not isinstance(doc["directories"],list) or not isinstance(doc["files"],dict):
        raise buyer.BuyerError("invalid backup inventory")
    if len(doc["directories"])+len(doc["files"])>MAX_FILES:
        raise buyer.BuyerError("backup inventory limit exceeded")
    if len(set(doc["directories"]))!=len(doc["directories"]):
        raise buyer.BuyerError("duplicate backup directory")
    total=0
    for name in doc["directories"]:
        _relative(name)
    for name,entry in doc["files"].items():
        _relative(name)
        if (not isinstance(entry,dict) or set(entry)!={"bytes","sha256"}
                or type(entry["bytes"]) is not int or entry["bytes"]<0
                or not isinstance(entry["sha256"],str) or len(entry["sha256"])!=64
                or any(c not in "0123456789abcdef" for c in entry["sha256"])):
            raise buyer.BuyerError("invalid backup file digest")
        total+=entry["bytes"]
    if total>MAX_TOTAL:
        raise buyer.BuyerError("backup size limit exceeded")
    return doc


def check(backup):
    root=_path(backup)
    doc=_manifest(root)
    if {p.name for p in root.iterdir()}!={MANIFEST,"workspace"}:
        raise buyer.BuyerError("unexpected backup root inventory")
    workspace=root/"workspace"
    dirs,files=_inventory(workspace)
    if dirs!=doc["directories"] or set(files)!=set(doc["files"]):
        raise buyer.BuyerError("backup inventory changed")
    for rel,info in files.items():
        if _read_file(workspace/rel,info)!=doc["files"][rel]:
            raise buyer.BuyerError("backup file changed")
    buyer.load(workspace)
    _sqlite_check(workspace)
    _procurement_paths(workspace,Path(doc["source_root"]))
    return {"backup_id":doc["backup_id"],"files":len(files),
            "bytes":sum(i[0] for i in files.values()),"integrity":"verified",
            "clinical_validity":"not_established"}


def create(directory,output,*,services_stopped=False):
    if services_stopped is not True:
        raise buyer.BuyerError("explicit confirmation of stopped services and workers required")
    root,dest=_path(directory),_path(output)
    buyer.load(root)
    if _inside(root,dest) is not None or _inside(dest,root) is not None:
        raise buyer.BuyerError("backup destination must be outside workspace")
    dirs,files=_inventory(root)
    _sqlite_check(root)
    _procurement_paths(root,root)
    dest.mkdir(mode=0o700)  # exclusive; never overwrite prior backup
    try:
        workspace=dest/"workspace"
        workspace.mkdir(mode=0o700)
        for rel in dirs:
            (workspace/rel).mkdir(mode=0o700,parents=True,exist_ok=True)
        entries={rel:_read_file(root/rel,info,workspace/rel) for rel,info in sorted(files.items())}
        if _inventory(root)!=(dirs,files):
            raise buyer.BuyerError("workspace inventory changed during checkpoint")
        for rel,info in files.items():
            if _read_file(root/rel,info)!=entries[rel]:
                raise buyer.BuyerError("workspace content changed during checkpoint")
        _sqlite_check(root)
        doc={"schema":SCHEMA,"source_root":str(root),"directories":dirs,"files":entries}
        doc["backup_id"]=buyer_plan._digest(doc)
        # The manifest is published last. Incomplete backups cannot verify.
        buyer._write_json(dest/MANIFEST,doc,create=True)
        return check(dest)
    except BaseException:
        shutil.rmtree(dest)
        raise


def restore(backup,destination,*,services_stopped=False):
    if services_stopped is not True:
        raise buyer.BuyerError("explicit confirmation of stopped services and workers required")
    root,dest=_path(backup),_path(destination)
    if _inside(root,dest) is not None or _inside(dest,root) is not None:
        raise buyer.BuyerError("restore destination must be outside backup")
    result=check(root)
    doc=_manifest(root)
    if doc["backup_id"]!=result["backup_id"]:
        raise buyer.BuyerError("backup changed before restore")
    source=root/"workspace"
    dirs,files=_inventory(source)
    dest.mkdir(mode=0o700)  # exclusive; never overwrite an existing workspace
    try:
        stage=dest/".restore-staging"
        stage.mkdir(mode=0o700)
        for rel in dirs:
            (stage/rel).mkdir(mode=0o700,parents=True,exist_ok=True)
        for rel,info in sorted(files.items()):
            if _read_file(source/rel,info,stage/rel)!=doc["files"][rel]:
                raise buyer.BuyerError("backup changed during restore")
        if check(root)["backup_id"]!=doc["backup_id"]:
            raise buyer.BuyerError("backup changed during restore")
        relocation=_procurement_paths(stage,Path(doc["source_root"]))
        if relocation is not None:
            from . import procurement_workflow as pw
            pw.save(stage/"assessment",relocation)
        db=stage/"institution"/"access.sqlite3"
        if db.is_file():
            from . import institution_access as access
            with access.db(stage) as conn:
                conn.execute("DELETE FROM sessions")
                access._audit(conn,"local-admin","workspace-restored",doc["backup_id"])
        _sqlite_check(stage)
        buyer.load(stage)
        # Publish the root state file last, only after relocation and session
        # revocation have completed. Partial destinations cannot be opened.
        for p in sorted(stage.iterdir(),key=lambda p:p.name==buyer.STATE_FILE):
            p.rename(dest/p.name)
        stage.rmdir()
        return {"backup_id":doc["backup_id"],"restored":True,
                "sessions_revoked":True,"decision_grade":False,
                "clinical_validity":"not_established"}
    except BaseException:
        shutil.rmtree(dest)
        raise
