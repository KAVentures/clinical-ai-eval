"""Minimal stdio MCP server for a fixed, explicitly exported aggregate snapshot.

Supports the MCP 2025-03-26 and 2024-11-05 stdio JSON-RPC lifecycle and tools.
No dynamic workspace paths, shell execution, resources, sampling, network,
or mutation tools. Stdout is reserved for JSON-RPC messages.
"""
from __future__ import annotations

import json
import sys

from . import agent_snapshot

PROTOCOLS = ("2025-03-26", "2024-11-05")
MAX_LINE = 65536

TOOLS = {
    "clinical_eval_capabilities": ("List supported scientific test families and their maturity.", "capabilities"),
    "clinical_eval_job_summary": ("Inspect anonymized evaluation job states and verified package counts.", "candidate_evaluation_jobs"),
    "clinical_eval_research_summary": ("Inspect aggregate research package integrity and recorded claim ceilings.", "research_runs"),
    "clinical_eval_procurement_inventory": ("Inspect aggregate procurement evidence inventory and missing-document counts.", "procurement_inventory"),
}


class Session:
    def __init__(self, envelope):
        self.envelope = envelope
        self.phase = "new"

    @staticmethod
    def _error(identifier, code, message):
        return {"jsonrpc":"2.0","id":identifier,"error":{"code":code,"message":message}}

    @staticmethod
    def _result(identifier, result):
        return {"jsonrpc":"2.0","id":identifier,"result":result}

    def handle(self, request):
        if not isinstance(request,dict) or request.get("jsonrpc")!="2.0" or not isinstance(request.get("method"),str):
            return self._error(None,-32600,"Invalid request")
        identifier=request.get("id")
        notification="id" not in request
        if not notification and (type(identifier) not in (int,str) or (isinstance(identifier,str) and len(identifier)>256)):
            return self._error(None,-32600,"Invalid request ID")
        method=request["method"]
        params=request.get("params",{})
        if notification:
            if method=="notifications/initialized" and self.phase=="initializing" and isinstance(params,dict):
                self.phase="ready"
            return None
        if not isinstance(params,dict):
            return self._error(identifier,-32602,"Invalid params")
        if method=="ping":
            return self._result(identifier,{})
        if method=="initialize":
            if self.phase!="new" or not isinstance(params.get("protocolVersion"),str):
                return self._error(identifier,-32602,"Invalid initialization")
            requested=params["protocolVersion"]
            version=requested if requested in PROTOCOLS else PROTOCOLS[0]
            self.phase="initializing"
            return self._result(identifier,{"protocolVersion":version,
                "capabilities":{"tools":{"listChanged":False}},
                "serverInfo":{"name":"clinical-ai-eval-local-summary","version":"1.0"},
                "instructions":"Read-only exported aggregate snapshot; not live, clinically validated, or decision-grade."})
        if self.phase!="ready":
            return self._error(identifier,-32000,"Server not initialized")
        if method=="tools/list":
            if params:
                return self._error(identifier,-32602,"No pagination or parameters supported")
            return self._result(identifier,{"tools":[
                {"name":name,"description":description,
                 "inputSchema":{"type":"object","properties":{},"additionalProperties":False},
                 "annotations":{"readOnlyHint":True,"destructiveHint":False,
                                "idempotentHint":True,"openWorldHint":False}}
                for name,(description,_) in TOOLS.items()]})
        if method=="tools/call":
            name=params.get("name")
            if (set(params)-{"name","arguments"} or not isinstance(name,str)
                    or name not in TOOLS or params.get("arguments",{})!={}):
                return self._error(identifier,-32602,"Unknown tool or invalid arguments")
            key=TOOLS[name][1]
            payload={"snapshot_digest":self.envelope["digest"],"live":False,"decision_grade":False,
                     "scope":"aggregate_snapshot_only","data":self.envelope["payload"][key]}
            return self._result(identifier,{"content":[{"type":"text","text":json.dumps(payload,ensure_ascii=False)}],
                                            "isError":False})
        return self._error(identifier,-32601,"Method not found")


def serve(path, stdin=None, stdout=None):
    # Load once before handshake: replacing the file cannot silently change the
    # approved evidence during a session. Restart with a newly reviewed export.
    envelope=agent_snapshot.load(path)
    session=Session(envelope)
    inp=stdin if stdin is not None else sys.stdin.buffer
    out=stdout if stdout is not None else sys.stdout.buffer
    while True:
        line=inp.readline(MAX_LINE+1)
        if not line:
            break
        if len(line)>MAX_LINE:
            # Terminate rather than treating the remaining bytes of an
            # oversized message as a new valid protocol message.
            break
        try:
            request=json.loads(line.decode("utf-8"))
        except (ValueError,UnicodeError,RecursionError):
            response=session._error(None,-32700,"Parse error")
        else:
            response=session.handle(request)
        if response is not None:
            out.write(json.dumps(response,ensure_ascii=False,separators=(",",":")).encode("utf-8")+b"\n")
            out.flush()
