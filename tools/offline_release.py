#!/usr/bin/env python3
"""Stdlib-only offline release preparation and side-by-side installation.

Run this trusted, independently verified script with the exact interpreter
and OS class intended for deployment. Never install unapproved wheels.
No source builds, dependency downloads, live workspace edits or service switch.
"""
from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import sysconfig
import tempfile
import venv
import zipfile
from pathlib import Path

SCHEMA = "caeval-offline-release/1"
MANIFEST = "release.json"
MAX_WHEELS = 150
MAX_TOTAL = 4 * 1024**3
MAX_METADATA = 1024**2
MAX_MANIFEST = 1024**2
DIGEST = re.compile(r"[a-f0-9]{64}")
WHEEL = re.compile(r"[A-Za-z0-9_.+-]+\.whl")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+!-]{0,127}")


class ReleaseError(ValueError):
    pass


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def normalized(name):
    if not isinstance(name,str) or not NAME.fullmatch(name):
        raise ReleaseError("invalid distribution name")
    return re.sub(r"[-_.]+","-",name).lower()


def target():
    return {"implementation":sys.implementation.name,
            "python":[sys.version_info.major,sys.version_info.minor],
            "platform":sysconfig.get_platform(),"machine":platform.machine(),
            "soabi":sysconfig.get_config_var("SOABI") or "",
            "system":platform.system(),"libc":list(platform.libc_ver())}


def root(path):
    p=Path(path).absolute()
    if p.is_symlink() or not p.is_dir():
        raise ReleaseError("existing regular release directory required")
    return p.resolve()


def _open_regular(path):
    if path.is_symlink():
        raise ReleaseError("linked release files refused")
    fd=os.open(str(path),os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0)|getattr(os,"O_BINARY",0))
    handle=os.fdopen(fd,"rb")
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
        handle.close()
        raise ReleaseError("regular release files required")
    return handle


def _copy_hash(path,destination=None):
    h=hashlib.sha256()
    size=0
    with _open_regular(path) as inp:
        before=os.fstat(inp.fileno())
        if before.st_size>MAX_TOTAL:
            raise ReleaseError("wheel too large")
        out=None
        try:
            if destination is not None:
                out=destination.open("xb")
            for block in iter(lambda:inp.read(1024*1024),b""):
                size+=len(block)
                if size>before.st_size:
                    raise ReleaseError("wheel changed during read")
                h.update(block)
                if out is not None:
                    out.write(block)
            after=os.fstat(inp.fileno())
            if (size!=before.st_size or (before.st_size,before.st_mtime_ns,before.st_ino)
                    !=(after.st_size,after.st_mtime_ns,after.st_ino)):
                raise ReleaseError("wheel changed during read")
        finally:
            if out is not None:
                out.close()
    return {"sha256":h.hexdigest(),"bytes":size}


def _metadata(path):
    # Only inspect metadata; never extract untrusted archives.
    with _open_regular(path) as f, zipfile.ZipFile(f) as z:
        entries=z.infolist()
        matches=[i for i in entries if re.fullmatch(r"[^/]+\.dist-info/METADATA",i.filename)]
        if len(matches)!=1 or matches[0].file_size>MAX_METADATA:
            raise ReleaseError("invalid wheel metadata")
        if len(entries)>100000 or sum(i.file_size for i in entries)>MAX_TOTAL:
        raise ReleaseError("wheel expansion exceeds limits")
    if len({i.filename for i in entries})!=len(entries):
            raise ReleaseError("duplicate wheel members")
        for i in entries:
            parts=i.filename.rstrip("/").split("/")
            if (not i.filename or "\\" in i.filename or ":" in i.filename
                    or any(p in ("",".","..") for p in parts)
                    or stat.S_ISLNK(i.external_attr>>16)):
                raise ReleaseError("unsafe wheel member")
        with z.open(matches[0]) as meta:
            raw=meta.read(MAX_METADATA+1)
    if len(raw)>MAX_METADATA:
        raise ReleaseError("wheel metadata too large")
    m=email.parser.BytesParser().parsebytes(raw)
    if len(m.get_all("Name",[]))!=1 or len(m.get_all("Version",[]))!=1:
        raise ReleaseError("missing or duplicate distribution identity")
    name=normalized(m["Name"])
    version=m["Version"]
    if not VERSION.fullmatch(version):
        raise ReleaseError("invalid version metadata")
    # Wheel names use underscores in the distribution component.
    parts=path.name[:-4].split("-")
    if len(parts) not in (5,6) or normalized(parts[0])!=name or parts[1]!=version:
        raise ReleaseError("wheel filename differs from metadata")
    return {"name":name,"version":version}


def _inventory(directory,allow_manifest):
    paths=[]
    for p in directory.iterdir():
        if p.name==MANIFEST and allow_manifest:
            continue
        if not WHEEL.fullmatch(p.name) or p.is_symlink() or not p.is_file():
            raise ReleaseError("release directory must contain only approved wheels and release manifest")
        paths.append(p)
    if not 1<=len(paths)<=MAX_WHEELS:
        raise ReleaseError("wheel count outside limits")
    if sum(p.stat().st_size for p in paths)>MAX_TOTAL:
        raise ReleaseError("wheelhouse exceeds size limit")
    return sorted(paths)


def _exclusive_json(path,obj):
    raw=canonical(obj)+b"\n"
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def prepare(directory,version,source_commit):
    directory=root(directory)
    if not VERSION.fullmatch(version) or not re.fullmatch(r"[a-f0-9]{40}",source_commit):
        raise ReleaseError("exact application version and source commit required")
    files={}
    names=set()
    for p in _inventory(directory,False):
        meta=_metadata(p)
        if meta["name"] in names:
            raise ReleaseError("exactly one wheel per distribution required")
        names.add(meta["name"])
        files[p.name]={**meta,**_copy_hash(p)}
    app=[x for x in files.values() if x["name"]=="clinical-ai-eval"]
    if len(app)!=1 or app[0]["version"]!=version:
        raise ReleaseError("expected application wheel missing")
    obj={"schema":SCHEMA,"version":version,"source_commit":source_commit,
         "target":target(),"wheels":files}
    obj["release_id"]=digest(obj)
    _exclusive_json(directory/MANIFEST,obj)
    verify(directory,obj["release_id"])
    return {"release_id":obj["release_id"],"version":version,"wheels":len(files),
            "source_commit":source_commit,"authenticity_independently_verified":False}


def _manifest(directory,expected):
    if not isinstance(expected,str) or not DIGEST.fullmatch(expected):
        raise ReleaseError("independently approved release ID required")
    with _open_regular(directory/MANIFEST) as f:
        raw=f.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST:
        raise ReleaseError("manifest too large")
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:
                raise ReleaseError("duplicate manifest field")
            result[k]=v
        return result
    obj=json.loads(raw.decode("utf-8"),object_pairs_hook=unique)
    if (not isinstance(obj,dict) or set(obj)!={"schema","version","source_commit","target","wheels","release_id"}
            or obj["schema"]!=SCHEMA or obj["release_id"]!=expected
            or digest({k:v for k,v in obj.items() if k!="release_id"})!=expected):
        raise ReleaseError("release manifest differs from approval")
    if (not isinstance(obj["version"],str) or not VERSION.fullmatch(obj["version"])
            or not isinstance(obj["source_commit"],str) or not re.fullmatch(r"[a-f0-9]{40}",obj["source_commit"])
            or obj["target"]!=target() or not isinstance(obj["wheels"],dict)):
        raise ReleaseError("release target or identity incompatible")
    return obj


def verify(directory,expected,copy_to=None):
    directory=root(directory)
    obj=_manifest(directory,expected)
    paths=_inventory(directory,True)
    if {p.name for p in paths}!=set(obj["wheels"]):
        raise ReleaseError("wheel inventory differs from approved release")
    names=set()
    for p in paths:
        dest=copy_to/p.name if copy_to is not None else None
        observed=_copy_hash(p,dest)
        # Verify the staged bytes before parsing or installing them.
        rec=obj["wheels"][p.name]
        if not isinstance(rec,dict) or set(rec)!={"name","version","sha256","bytes"} or any(rec.get(k)!=v for k,v in observed.items()):
            raise ReleaseError("wheel content differs from approved release")
        meta=_metadata(dest if dest is not None else p)
        if any(rec.get(k)!=v for k,v in meta.items()) or meta["name"] in names:
            raise ReleaseError("wheel identity differs from approved release")
        names.add(meta["name"])
    if not any(x["name"]=="clinical-ai-eval" and x["version"]==obj["version"] for x in obj["wheels"].values()):
        raise ReleaseError("approved application version missing")
    return obj


def _env():
    # Do not inherit pip index, Python path, application data overrides or API keys.
    keys=("SYSTEMROOT","WINDIR","TEMP","TMP","TMPDIR","LANG","LC_ALL")
    env={k:os.environ[k] for k in keys if k in os.environ}
    env.update(PIP_CONFIG_FILE=os.devnull,PIP_NO_INDEX="1",PIP_DISABLE_PIP_VERSION_CHECK="1",
               PYTHONNOUSERSITE="1",PYTHONDONTWRITEBYTECODE="1")
    return env


def _run(argv,cwd):
    result=subprocess.run([str(x) for x in argv],cwd=cwd,env=_env(),stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=600,check=False)
    if result.returncode:
        raise ReleaseError("offline installation or smoke test failed")
    return result.stdout


def _receipt(obj):
    return {"schema":"caeval-offline-install/1","release_id":obj["release_id"],
            "version":obj["version"],"source_commit":obj["source_commit"],"target":obj["target"],
            "wheel_hashes":{k:v["sha256"] for k,v in obj["wheels"].items()},
            "dependency_check":"passed","installed_cli_smoke":"passed",
            "service_activated":False,"production_approved":False}


def _check_environment(dest,obj,cwd):
    environment=dest/"venv"
    if environment.is_symlink() or not environment.is_dir():
        raise ReleaseError("installed environment unavailable")
    python=environment/("Scripts/python.exe" if os.name=="nt" else "bin/python")
    if not python.is_file():
        raise ReleaseError("installed interpreter unavailable")
    _run([python,"-I","-m","pip","--isolated","check"],cwd)
    check=("import importlib.metadata as m,json,sys,pathlib,caeval,re;"
           "want=json.loads(sys.argv[1]);"
           "norm=lambda n:re.sub(r'[-_.]+','-',n).lower();"
           "actual={norm(d.metadata['Name']):d.version for d in m.distributions()};"
           "assert set(actual)<=set(want)|{'pip','setuptools'};"
           "assert all(m.version(k)==v for k,v in want.items());"
           "assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[2]).resolve();"
           "assert pathlib.Path(caeval.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve());"
           "assert caeval.__version__==want['clinical-ai-eval']")
    _run([python,"-I","-c",check,json.dumps({v["name"]:v["version"] for v in obj["wheels"].values()}),environment],cwd)
    for args in (("inspect",),("families",),("capabilities","--check")):
        _run([python,"-I","-m","caeval.cli",*args],cwd)


def check_install(directory,expected,destination):
    """Recheck a retained version before controlled upgrade or rollback.

    This checks receipt identity, package versions, dependency consistency and
    runnable CLI. It does not authenticate installed executable bytes.
    """
    obj=verify(directory,expected)
    dest=root(destination)
    with _open_regular(dest/"installed-release.json") as f:
        raw=f.read(MAX_MANIFEST+1)
    if len(raw)>MAX_MANIFEST or json.loads(raw.decode("utf-8"))!=_receipt(obj):
        raise ReleaseError("installed release receipt differs from approved bundle")
    with tempfile.TemporaryDirectory(prefix=".caeval-release-check-") as tmp:
        _check_environment(dest,obj,Path(tmp))
    return {**_receipt(obj),"rechecked":True,"installed_file_integrity":"not_established"}


def install(directory,expected,destination,*,confirmed=False):
    if confirmed is not True:
        raise ReleaseError("explicit trusted release approval required")
    directory=root(directory)
    dest=Path(destination).absolute()
    if dest.exists() or dest.is_symlink() or not dest.parent.is_dir() or dest.parent.is_symlink():
        raise ReleaseError("fresh side-by-side installation directory required")
    dest=dest.parent.resolve()/dest.name
    if directory==dest or directory in dest.parents or dest in directory.parents:
        raise ReleaseError("installation must be separate from release bundle")
    # Destination parent must be controlled by IT. venvs are not relocatable.
    # Never activate or modify an existing environment, clinical workspace or service.
    with tempfile.TemporaryDirectory(prefix=".caeval-approved-",dir=dest.parent) as temp:
        stage=Path(temp)
        obj=verify(directory,expected,stage)
        dest.mkdir(mode=0o700)
        try:
            environment=dest/"venv"
            # ensurepip bootstraps only from the interpreter's bundled wheels.
            venv.EnvBuilder(with_pip=True,clear=False).create(environment)
            python=environment/("Scripts/python.exe" if os.name=="nt" else "bin/python")
            wheels=[stage/name for name in sorted(obj["wheels"])]
            _run([python,"-I","-m","pip","--isolated","--disable-pip-version-check",
                  "install","--no-index","--no-deps","--only-binary=:all:","--no-cache-dir",
                  "--force-reinstall",*wheels],stage)
            _check_environment(dest,obj,stage)
            receipt=_receipt(obj)
            _exclusive_json(dest/"installed-release.json",receipt)
            return receipt
        except BaseException:
            shutil.rmtree(dest)
            raise


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    prep=sub.add_parser("prepare")
    prep.add_argument("wheelhouse")
    prep.add_argument("--version",required=True)
    prep.add_argument("--source-commit",required=True)
    for name in ("verify","install","check-install"):
        q=sub.add_parser(name)
        q.add_argument("wheelhouse")
        q.add_argument("--approve-release",required=True)
        if name in ("install","check-install"):
            q.add_argument("--destination",required=True)
        if name=="install":
            q.add_argument("--confirm-trusted-wheels",action="store_true")
    args=p.parse_args(argv)
    try:
        if args.action=="prepare":
            out=prepare(args.wheelhouse,args.version,args.source_commit)
        elif args.action=="verify":
            obj=verify(args.wheelhouse,args.approve_release)
            out={"release_id":obj["release_id"],"integrity":"verified","version":obj["version"]}
        elif args.action=="check-install":
            out=check_install(args.wheelhouse,args.approve_release,args.destination)
        else:
            out=install(args.wheelhouse,args.approve_release,args.destination,confirmed=args.confirm_trusted_wheels)
        print(json.dumps(out,indent=2))
        return 0
    except (OSError,ValueError,RuntimeError,TypeError,KeyError,zipfile.BadZipFile,subprocess.SubprocessError):
        print("Release operation refused; verify the trusted bundle, target interpreter and installation logs.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
