"""Unix-socket live metadata broker and unprivileged stdio MCP relay.

The broker is a trusted server process. The relay is a client process with
only a credential file and socket path, not a clinical workspace path.
"""
from __future__ import annotations

import json
import os
import re
import socket
import socketserver
import stat
import sys
from pathlib import Path

from . import agent_grants as grants, agent_mcp, agent_snapshot
from . import institution_access as access

MAX_REQUEST=4096
MAX_REPLY=2*agent_snapshot.MAX_BYTES
TIMEOUT=60


def _decode(raw):
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:
                raise ValueError("duplicate field")
            result[k]=v
        return result
    return json.loads(raw.decode("utf-8"),object_pairs_hook=unique)


def _line(obj):
    return json.dumps(obj,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")+b"\n"


def dispatch(directory,generation,request):
    if not isinstance(request,dict):
        raise access.AccessDenied("invalid broker request")
    if set(request)=={"op","token"} and request["op"]=="list":
        return {"scopes":grants.permitted(directory,request["token"],generation)}
    if set(request)=={"op","token","tool"} and request["op"]=="read":
        return grants.query(directory,request["token"],generation,request["tool"])
    raise access.AccessDenied("invalid broker request")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(TIMEOUT)
        try:
            raw=self.rfile.readline(MAX_REQUEST+1)
            if not raw.endswith(b"\n") or len(raw)>MAX_REQUEST:
                raise ValueError("invalid message size")
            result=dispatch(self.server.directory,self.server.generation,_decode(raw))
            response=_line({"ok":True,"result":result})
            if len(response)>MAX_REPLY:
                raise ValueError("reply exceeds limit")
        except (OSError,ValueError,RuntimeError,TypeError,KeyError,RecursionError):
            response=_line({"ok":False,"error":"Agent request denied or metadata unavailable."})
        try:
            self.wfile.write(response)
        except OSError:
            pass


class Server(socketserver.UnixStreamServer):
    request_queue_size=8
    def handle_error(self,request,client_address):
        # Never dump credentials, request contents or workspace paths.
        print("Agent broker request failed.",file=sys.stderr)


def serve(directory,socket_path,generation_file):
    if not hasattr(socket,"AF_UNIX") or os.name!="posix":
        raise access.AccessDenied("live broker requires POSIX Unix sockets")
    path=Path(socket_path)
    grants.outside_workspace(directory,path)
    grants.outside_workspace(directory,generation_file)
    generation=grants.read_private(generation_file,re.compile(r"[0-9a-f]{64}"))
    if not path.is_absolute() or path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise access.AccessDenied("fresh absolute socket path required")
    mode=path.parent.stat().st_mode
    if not stat.S_ISDIR(mode) or mode&0o022:
        raise access.AccessDenied("socket parent must not be group/world writable")
    # No unlink of existing paths. The trusted administrator controls the
    # protected socket directory. Single worker bounds simultaneous reads.
    old_umask=os.umask(0o117)
    try:
        server=Server(str(path),Handler)
    finally:
        os.umask(old_umask)
    try:
        os.chmod(path,0o660)
        identity=path.lstat()
        server.directory=directory
        server.generation=generation
        server.serve_forever()
    finally:
        server.server_close()
        if path.is_socket() and not path.is_symlink() and "identity" in locals():
            current=path.lstat()
            if (current.st_dev,current.st_ino)==(identity.st_dev,identity.st_ino):
                path.unlink()


def request(socket_path,token,op,tool=None):
    path=Path(socket_path)
    if not path.is_absolute() or path.is_symlink() or not path.is_socket():
        raise access.AccessDenied("approved Unix socket required")
    req={"op":op,"token":token}
    if tool is not None:
        req["tool"]=tool
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.settimeout(TIMEOUT)
            conn.connect(str(path))
            conn.sendall(_line(req))
            with conn.makefile("rb") as f:
                raw=f.readline(MAX_REPLY+1)
        if len(raw)>MAX_REPLY or not raw.endswith(b"\n"):
            raise ValueError("invalid reply")
        out=_decode(raw)
        if not isinstance(out,dict) or set(out)!={"ok","result"} or out["ok"] is not True:
            raise ValueError("denied")
        return out["result"]
    except (OSError,ValueError,TypeError,KeyError,RecursionError):
        raise access.AccessDenied("live agent broker unavailable or grant denied") from None


class LiveSession(agent_mcp.Session):
    def __init__(self,socket_path,token):
        super().__init__({})
        self.socket_path=socket_path
        self.token=token

    def handle(self,req):
        if not isinstance(req,dict) or req.get("method") not in ("tools/list","tools/call"):
            result=super().handle(req)
            if isinstance(result,dict) and isinstance(req,dict) and req.get("method")=="initialize" and "result" in result:
                result["result"]["serverInfo"]["name"]="clinical-ai-eval-live-metadata"
                result["result"]["instructions"]="Revocable, scoped, read-only institutional aggregate metadata; not clinical validation."
            return result
        identifier=req.get("id")
        if "id" not in req:
            return None
        if req.get("jsonrpc")!="2.0" or type(identifier) not in (int,str) or (isinstance(identifier,str) and len(identifier)>256):
            return self._error(None,-32600,"Invalid request")
        if self.phase!="ready":
            return self._error(identifier,-32000,"Server not initialized")
        params=req.get("params",{})
        try:
            if req["method"]=="tools/list":
                if params!={}:
                    return self._error(identifier,-32602,"Invalid params")
                result=request(self.socket_path,self.token,"list")
                scopes=grants._scopes(result["scopes"])
                return self._result(identifier,{"tools":[
                    {"name":name,"description":agent_mcp.TOOLS[name][0],
                     "inputSchema":{"type":"object","properties":{},"additionalProperties":False},
                     "annotations":{"readOnlyHint":True,"destructiveHint":False,
                                    "idempotentHint":False,"openWorldHint":False}}
                    for name in scopes]})
            if (not isinstance(params,dict) or set(params)-{"name","arguments"}
                    or not isinstance(params.get("name"),str) or params["name"] not in grants.TOOLS
                    or params.get("arguments",{})!={}):
                return self._error(identifier,-32602,"Invalid tool or arguments")
            result=request(self.socket_path,self.token,"read",params["name"])
            return self._result(identifier,{"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}],"isError":False})
        except (OSError,ValueError,RuntimeError,TypeError,KeyError):
            return self._error(identifier,-32000,"Live metadata unavailable or grant denied")


def relay(socket_path,token_file,stdin=None,stdout=None):
    token=grants.read_private(token_file,grants.TOKEN)
    session=LiveSession(socket_path,token)
    inp=stdin if stdin is not None else sys.stdin.buffer
    out=stdout if stdout is not None else sys.stdout.buffer
    while True:
        raw=inp.readline(agent_mcp.MAX_LINE+1)
        if not raw or len(raw)>agent_mcp.MAX_LINE:
            break
        try:
            req=_decode(raw)
        except (ValueError,UnicodeError,RecursionError):
            response=session._error(None,-32700,"Parse error")
        else:
            response=session.handle(req)
        if response is not None:
            out.write(_line(response))
            out.flush()
