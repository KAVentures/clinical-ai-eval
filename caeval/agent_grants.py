"""Trusted institutional live aggregate agent grants.

The broker owns workspace access. An agent receives only a bounded, expiring
bearer credential and a local socket; it does not need filesystem access to
the workspace. No clinical text or assessment execution is exposed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from . import agent_mcp, agent_snapshot, buyer_plan
from . import institution_access as access, buyer_manual as manual

MAX_TTL=86400
MAX_CALLS=200
MAX_GRANTS=200
TOKEN=re.compile(r"[A-Za-z0-9_-]{43}")
TOOLS=tuple(agent_mcp.TOOLS)


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_grants(
      id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
      owner TEXT NOT NULL REFERENCES users(id), scopes TEXT NOT NULL,
      workspace_hash TEXT NOT NULL, generation_hash TEXT NOT NULL,
      expires INTEGER NOT NULL, remaining INTEGER NOT NULL,
      revoked INTEGER NOT NULL DEFAULT 0)""")


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _workspace(directory):
    return _hash(str(Path(directory).resolve()))


def _generation(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9a-f]{64}",value):
        raise access.AccessDenied("invalid broker generation")
    return _hash(value)


def _scopes(values):
    if (not isinstance(values,(list,tuple)) or not values
            or any(not isinstance(v,str) or v not in TOOLS for v in values)
            or len(set(values))!=len(values)):
        raise access.AccessDenied("explicit unique supported scopes required")
    return sorted(values)


def _owner(conn,owner):
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?",(owner,)).fetchone()
    if not row or not row["enabled"] or row["role"] not in ("admin","operator","observer"):
        raise access.AccessDenied("active institutional metadata account required")


def issue(directory,owner,scopes,ttl,calls,generation,*,confirmed=False):
    """Trusted IT only. The caller must explicitly approve agent data handling."""
    if confirmed is not True:
        raise access.AccessDenied("explicit metadata-sharing approval required")
    manual._identifier(owner)
    scopes=_scopes(scopes)
    if type(ttl) is not int or not 1<=ttl<=MAX_TTL or type(calls) is not int or not 1<=calls<=MAX_CALLS:
        raise access.AccessDenied("grant duration or call budget outside limits")
    epoch=_generation(generation)
    now=int(time.time())
    identifier="grant-"+secrets.token_hex(16)
    token=secrets.token_urlsafe(32)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _owner(conn,owner)
        if conn.execute("SELECT count(*) FROM agent_grants WHERE revoked=0 AND expires>?",(now,)).fetchone()[0]>=MAX_GRANTS:
            raise access.AccessDenied("active grant limit")
        conn.execute("INSERT INTO agent_grants VALUES(?,?,?,?,?,?,?,?,0)",
                     (identifier,_hash(token),owner,json.dumps(scopes),_workspace(directory),epoch,now+ttl,calls))
        access._audit(conn,owner,"agent-grant-issued",identifier)
    return {"grant_id":identifier,"token":token,"expires":now+ttl,"scopes":scopes,"remaining":calls}


def revoke(directory,identifier):
    manual._identifier(identifier)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("UPDATE agent_grants SET revoked=1 WHERE id=?",(identifier,)).rowcount!=1:
            raise access.AccessDenied("unknown grant")
        access._audit(conn,"local-admin","agent-grant-revoked",identifier)


def inventory(directory):
    with access.db(directory) as conn:
        _schema(conn)
        rows=[dict(r) for r in conn.execute("""SELECT id,owner,scopes,expires,remaining,revoked
                FROM agent_grants ORDER BY expires DESC LIMIT 500""")]
    for row in rows:
        row["scopes"]=json.loads(row["scopes"])
    return rows


def _grant(conn,directory,token,generation):
    if not isinstance(token,str) or not TOKEN.fullmatch(token):
        raise access.AccessDenied("agent grant denied")
    row=conn.execute("SELECT * FROM agent_grants WHERE token_hash=?",(_hash(token),)).fetchone()
    if (not row or row["revoked"] or row["expires"]<=int(time.time())
            or row["workspace_hash"]!=_workspace(directory)
            or row["generation_hash"]!=_generation(generation)):
        raise access.AccessDenied("agent grant denied")
    _owner(conn,row["owner"])
    scopes=_scopes(json.loads(row["scopes"]))
    return row,scopes


def permitted(directory,token,generation):
    with access.db(directory) as conn:
        _schema(conn)
        row,scopes=_grant(conn,directory,token,generation)
        if row["remaining"]<=0:
            raise access.AccessDenied("agent grant exhausted")
    return scopes


def query(directory,token,generation,tool):
    if not isinstance(tool,str) or tool not in TOOLS:
        raise access.AccessDenied("unsupported agent tool")
    # Reserve a call transactionally before doing potentially expensive work.
    # Failure consumes a call as well. No concurrent overspending.
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row,scopes=_grant(conn,directory,token,generation)
        if tool not in scopes or row["remaining"]<=0:
            raise access.AccessDenied("agent tool not granted or budget exhausted")
        conn.execute("UPDATE agent_grants SET remaining=remaining-1 WHERE id=?",(row["id"],))
        identifier=row["id"]
        owner=row["owner"]
        access._audit(conn,owner,"agent-read-requested:"+tool,identifier)
    try:
        envelope=agent_snapshot.build(directory)
        agent_snapshot._validate(envelope["payload"])
        if buyer_plan._digest(envelope["payload"])!=envelope["digest"]:
            raise access.AccessDenied("invalid summary digest")
        if len(buyer_plan._canonical(envelope))>agent_snapshot.MAX_BYTES:
            raise access.AccessDenied("summary too large")
        key=agent_mcp.TOOLS[tool][1]
        result={"live":True,"decision_grade":False,"scope":"aggregate_live_read_only",
                "data":envelope["payload"][key]}
        # Digest only the permitted view, not other scopes. Not a signature.
        result["view_digest"]=buyer_plan._digest(result)
        with access.db(directory) as conn:
            _schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            row,scopes=_grant(conn,directory,token,generation)
            if row["id"]!=identifier or tool not in scopes:
                raise access.AccessDenied("agent grant changed during computation")
            access._audit(conn,owner,"agent-read-returned:"+tool,identifier)
        return result
    except (OSError,ValueError,RuntimeError,KeyError,TypeError):
        access.event(directory,owner,"agent-read-denied:"+tool,identifier,"denied")
        raise access.AccessDenied("agent metadata unavailable or grant denied") from None


def read_private(path,pattern):
    """Owner-only, bounded credential file. No credentials in argv or logs."""
    p=Path(path)
    if p.is_symlink() or not p.is_file():
        raise access.AccessDenied("private credential file required")
    fd=os.open(str(p),os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    with os.fdopen(fd,"rb") as f:
        import stat
        info=os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name=="posix" and info.st_mode&0o077):
            raise access.AccessDenied("private credential file required")
        value=f.read(128).decode("ascii").strip()
    if not pattern.fullmatch(value):
        raise access.AccessDenied("invalid credential file")
    return value


def outside_workspace(directory,path):
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
    except ValueError:
        return
    raise access.AccessDenied("credentials and socket must be outside workspace")


def write_private(path,value):
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="ascii") as f:
        f.write(value+"\n")
