"""Scoped stdio MCP client for the local evaluator request HTTP service.

No workspace access, clinical data, execution, arbitrary URL or shell tools.
The host configures a loopback port and an owner-only machine grant file.
"""
import json
import sys
import urllib.request
from . import agent_mcp, agent_grants, agent_live, buyer_connector, evaluator_service as api
from . import buyer_manual as manual, institution_access as access

TOOLS={
    "discover_assessment":("Discover this grant's prepared assessment.",None,True),
    "list_evaluation_requests":("Recover this grant's evaluation request IDs.",None,True),
    "request_evaluation":("Request human approval for the granted assessment. Does not execute.","idempotency_key",False),
    "evaluation_status":("Read the current status of an owned evaluation request.","request_id",True),
    "evaluation_receipt":("Retrieve completed verified package metadata, not raw records.","request_id",True),
}


def _arguments(name,args):
    if name not in TOOLS or not isinstance(args,dict):
        raise access.AccessDenied("invalid tool")
    field=TOOLS[name][1]
    if field is None:
        if args:
            raise access.AccessDenied("unexpected tool arguments")
        return
    pattern=api.KEY if field=="idempotency_key" else api.ID
    if set(args)!={field} or not isinstance(args[field],str) or not pattern.fullmatch(args[field]):
        raise access.AccessDenied("invalid tool arguments")


class Client:
    def __init__(self,port,token):
        if type(port) is not int or not 1024<=port<=65535 or not isinstance(token,str) or not api.TOKEN.fullmatch(token):
            raise access.AccessDenied("invalid local evaluator client")
        self.origin="http://127.0.0.1:"+str(port)
        self.token=token

    def call(self,name,args):
        _arguments(name,args)
        path="/v1/evaluation-requests"
        data=None
        if name=="discover_assessment":
            path="/v1/assessment"
        elif name=="request_evaluation":
            data=json.dumps(args).encode("utf-8")
        elif name in ("evaluation_status","evaluation_receipt"):
            path+="/"+args["request_id"]
            if name=="evaluation_receipt":
                path+="/receipt"
        url=buyer_connector._endpoint(self.origin+path)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),buyer_connector._NoRedirect())
        request=urllib.request.Request(url,data=data,headers={"Authorization":"Bearer "+self.token,
                                      "Content-Type":"application/json","Accept":"application/json"},
                                      method="POST" if data is not None else "GET")
        with opener.open(request,timeout=60) as reply:
            if reply.status!=200 or reply.headers.get_content_type()!="application/json":
                raise access.AccessDenied("invalid service response")
            raw=reply.read(api.MAX_REPLY+1)
        if len(raw)>api.MAX_REPLY:
            raise access.AccessDenied("service response too large")
        doc=manual._parse(raw)
        if not isinstance(doc,dict) or set(doc)!={"ok","result"} or doc["ok"] is not True or not isinstance(doc["result"],dict):
            raise access.AccessDenied("service request denied")
        return doc["result"]


class Session(agent_mcp.Session):
    def __init__(self,client):
        super().__init__({})
        self.client=client

    def handle(self,req):
        if not isinstance(req,dict) or req.get("method") not in ("tools/list","tools/call"):
            result=super().handle(req)
            if isinstance(req,dict) and req.get("method")=="initialize" and isinstance(result,dict) and "result" in result:
                result["result"]["serverInfo"]["name"]="clinical-ai-eval-scoped-evaluator"
                result["result"]["instructions"]="Request and status only. Human approval and model execution are separate."
            return result
        if "id" not in req:
            return None
        identifier=req["id"]
        if req.get("jsonrpc")!="2.0" or type(identifier) not in (str,int) or (isinstance(identifier,str) and len(identifier)>256):
            return self._error(None,-32600,"Invalid request")
        if self.phase!="ready":
            return self._error(identifier,-32000,"Server not initialized")
        params=req.get("params",{})
        try:
            if req["method"]=="tools/list":
                if params!={}:
                    return self._error(identifier,-32602,"Invalid params")
                # Refuse tool discovery with an expired or revoked grant.
                self.client.call("discover_assessment",{})
                tools=[]
                for name,(description,field,read_only) in TOOLS.items():
                    pattern=api.KEY.pattern if field=="idempotency_key" else api.ID.pattern
                    schema={"type":"object","additionalProperties":False,"properties":{}}
                    if field is not None:
                        schema.update(properties={field:{"type":"string","pattern":"^"+pattern+"$"}},required=[field])
                    tools.append({"name":name,"description":description,"inputSchema":schema,
                                  "annotations":{"readOnlyHint":read_only,"destructiveHint":False,
                                                 "idempotentHint":True,"openWorldHint":False}})
                return self._result(identifier,{"tools":tools})
            if not isinstance(params,dict) or set(params)-{"name","arguments"} or not isinstance(params.get("name"),str):
                return self._error(identifier,-32602,"Invalid params")
            name=params["name"]
            args=params.get("arguments",{})
            _arguments(name,args)
            result=self.client.call(name,args)
            return self._result(identifier,{"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False)}],"isError":False})
        except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
            return self._error(identifier,-32000,"Evaluation service unavailable or grant denied")


def relay(port,token_file,stdin=None,stdout=None):
    token=agent_grants.read_private(token_file,api.TOKEN)
    session=Session(Client(port,token))
    inp=stdin if stdin is not None else sys.stdin.buffer
    out=stdout if stdout is not None else sys.stdout.buffer
    while True:
        raw=inp.readline(agent_mcp.MAX_LINE+1)
        if not raw or len(raw)>agent_mcp.MAX_LINE:
            break
        try:
            req=agent_live._decode(raw)
        except (ValueError,UnicodeError,RecursionError):
            response=session._error(None,-32700,"Parse error")
        else:
            response=session.handle(req)
        if response is not None:
            out.write(agent_live._line(response))
            out.flush()


def cli(args):
    try:
        relay(args.port,args.token_file)
    except (OSError,ValueError,RuntimeError,TypeError):
        print("Evaluator MCP client refused.",file=sys.stderr)
        raise SystemExit(1)
