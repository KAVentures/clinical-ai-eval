"""Explicitly approved external HTTPS candidate bridge.

The evaluation workers still use their existing literal-loopback transport.
A trusted IT operator separately starts this narrowly scoped gateway, which
can reach exactly one public IP/HTTPS hostname/path. No DNS, proxies,
redirects, arbitrary URLs, patient-world metadata or retry are accepted.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import adapters, buyer_manual, buyer_plan

MAX_CONFIG=32768
MAX_BODY=256*1024
MAX_TEXT=6000
MAX_CALLS=1500
MAX_SECONDS=1800
NAME=re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
ENV=re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
HOST=re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
PATH=re.compile(r"/[A-Za-z0-9_./-]{1,250}")
ANSWER=re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")


class BridgeError(RuntimeError):
    pass


def _parse(raw):
    if len(raw)>MAX_BODY:
        raise BridgeError("JSON exceeds limit")
    try:
        return buyer_manual._parse(raw)
    except (ValueError,RuntimeError,TypeError,RecursionError):
        raise BridgeError("invalid JSON") from None


def validate(config):
    fields={"vendor_id","target_name","target_version","modality","listen_port",
            "upstream_url","upstream_ip","upstream_input_field","upstream_answer_path",
            "fixed_fields","local_token_env","upstream_token_env","upstream_auth",
            "timeout_seconds","max_calls","max_seconds","data_scope"}
    if not isinstance(config,dict) or set(config)!=fields:
        raise BridgeError("exact approved bridge configuration required")
    buyer_manual._identifier(config["vendor_id"])
    for k in ("target_name","target_version"):
        if not isinstance(config[k],str) or not config[k].strip() or len(config[k])>300:
            raise BridgeError("explicit candidate and version required")
    if config["modality"] not in ("single_turn","conversation") or config["data_scope"]!="approved_synthetic_or_nonidentifiable":
        raise BridgeError("approved data scope and supported modality required")
    for k,lo,hi in (("listen_port",1024,65535),("timeout_seconds",1,60),
                    ("max_calls",1,MAX_CALLS),("max_seconds",1,MAX_SECONDS)):
        if type(config[k]) is not int or not lo<=config[k]<=hi:
            raise BridgeError("invalid port, timeout or budget")
    url=config["upstream_url"]
    try:
        if not isinstance(url,str) or not url.isascii() or len(url)>512 or any(c.isspace() for c in url):
            raise ValueError()
        p=urllib.parse.urlsplit(url)
        if (p.scheme!="https" or not p.hostname or not HOST.fullmatch(p.hostname)
                or "." not in p.hostname or any(not label or label.startswith("-") or label.endswith("-") or len(label)>63 for label in p.hostname.split("."))
                or p.netloc not in (p.hostname,p.hostname+":443") or p.query or p.fragment
                or not PATH.fullmatch(p.path) or any(x in (".","..") for x in p.path.split("/"))):
            raise ValueError()
        # Certificate authentication uses the declared DNS name, not an IP.
        try:
            ipaddress.ip_address(p.hostname)
        except ValueError:
            pass
        else:
            raise ValueError()
        ip=ipaddress.ip_address(config["upstream_ip"])
        if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified
                or ip.is_loopback or ip.is_link_local or getattr(ip,"ipv4_mapped",None) is not None
                or str(ip)!=config["upstream_ip"]):
            raise ValueError()
    except (ValueError,TypeError,AttributeError):
        raise BridgeError("fixed public IP and HTTPS hostname/path required") from None
    if not isinstance(config["upstream_input_field"],str) or not NAME.fullmatch(config["upstream_input_field"]):
        raise BridgeError("invalid upstream input mapping")
    answer=config["upstream_answer_path"]
    if not isinstance(answer,str) or len(answer)>200 or not ANSWER.fullmatch(answer):
        raise BridgeError("invalid upstream response mapping")
    fixed=config["fixed_fields"]
    if (not isinstance(fixed,dict) or len(fixed)>20 or config["upstream_input_field"] in fixed
            or any(not NAME.fullmatch(k) or any(x in k.lower() for x in ("secret","token","password","key","auth","header","url"))
                   or type(v) not in (str,int,float,bool) or isinstance(v,str) and (len(v)>1000 or "${" in v)
                   for k,v in fixed.items())):
        raise BridgeError("only fixed noncredential scalar upstream fields allowed")
    if fixed.get("stream") not in (None,False):
        raise BridgeError("streaming response is unsupported")
    for k in ("local_token_env","upstream_token_env"):
        if not isinstance(config[k],str) or not ENV.fullmatch(config[k]):
            raise BridgeError("credentials must be environment variable names")
    if config["local_token_env"]==config["upstream_token_env"] or config["upstream_auth"] not in ("bearer","x-api-key"):
        raise BridgeError("separate local and upstream credentials required")
    try:
        buyer_plan._canonical(config)
    except (TypeError,ValueError):
        raise BridgeError("nonfinite or malformed configuration") from None
    return config


def load(path):
    # Bounded, nonblocking open; reject symlinks and special files.
    fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_NONBLOCK",0))
    with os.fdopen(fd,"rb") as handle:
        info=os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o022:
            raise BridgeError("trusted regular configuration not writable by group or world required")
        raw=handle.read(MAX_CONFIG+1)
    if len(raw)>MAX_CONFIG:
        raise BridgeError("configuration exceeds limit")
    return validate(_parse(raw))


def binding(config):
    return buyer_plan._digest(validate(config))


def connector(config):
    """Print the existing staging format. The URL binds every config field."""
    digest=binding(config)
    doc={k:config[k] for k in ("vendor_id","target_name","target_version","timeout_seconds")}
    doc.update(url="http://127.0.0.1:"+str(config["listen_port"])+"/evaluate/"+digest,
               answer_path="answer",headers={"Authorization":"Bearer ${"+config["local_token_env"]+"}"})
    if config["modality"]=="single_turn":
        doc["prompt_field"]="prompt"
    else:
        doc.update(history_field="messages",session_field="")
    return doc


def _credentials(config):
    local=os.environ.get(config["local_token_env"],"")
    remote=os.environ.get(config["upstream_token_env"],"")
    if (not re.fullmatch(r"[0-9a-f]{64}",local) or not remote or len(remote)>8192
            or not remote.isascii() or any(ord(c)<33 or ord(c)>126 for c in remote)
            or hmac.compare_digest(local,remote)):
        raise BridgeError("separate valid local and upstream credentials required")
    return local,remote


def _payload(config,doc):
    """Only visible prompt/history; translate roles for chat APIs."""
    key="prompt" if config["modality"]=="single_turn" else "messages"
    if not isinstance(doc,dict) or set(doc)!={key}:
        raise BridgeError("unexpected candidate request fields")
    value=doc[key]
    if key=="prompt":
        if not isinstance(value,str) or not value.strip():
            raise BridgeError("nonempty prompt required")
    else:
        if not isinstance(value,list) or not 1<=len(value)<=100 or len(value)%2!=1:
            raise BridgeError("nonempty alternating patient/product transcript required")
        messages=[]
        for i,row in enumerate(value):
            role="patient" if i%2==0 else "system"
            if (not isinstance(row,dict) or set(row)!={"role","content"} or row["role"]!=role
                    or not isinstance(row["content"],str) or not row["content"].strip()):
                raise BridgeError("invalid conversational role or content")
            messages.append({"role":"user" if role=="patient" else "assistant","content":row["content"]})
        value=messages
    result={**config["fixed_fields"],config["upstream_input_field"]:value}
    raw=json.dumps(result,ensure_ascii=False,allow_nan=False).encode("utf-8")
    if len(raw)>MAX_BODY:
        raise BridgeError("upstream request exceeds limit")
    return raw


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self,host,ip,timeout):
        self._ip=ip
        super().__init__(host,443,timeout=timeout,context=ssl.create_default_context())

    def connect(self):
        # No DNS resolution even if the configured name is compromised.
        parsed=ipaddress.ip_address(self._ip)
        sock=socket.socket(socket.AF_INET6 if parsed.version==6 else socket.AF_INET,socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect((self._ip,443))
            self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _upstream(config,payload,secret):
    p=urllib.parse.urlsplit(config["upstream_url"])
    headers={"Content-Type":"application/json","Accept":"application/json","Accept-Encoding":"identity"}
    if config["upstream_auth"]=="bearer":
        headers["Authorization"]="Bearer "+secret
    else:
        headers["X-API-Key"]=secret
    conn=_PinnedHTTPS(p.hostname,config["upstream_ip"],config["timeout_seconds"])
    try:
        conn.request("POST",p.path,body=payload,headers=headers)
        response=conn.getresponse()
        if (response.status!=200 or response.getheader("Content-Encoding","identity").lower()!="identity"
                or response.getheader("Content-Type","").split(";")[0].strip().lower()!="application/json"):
            raise BridgeError("upstream rejected or unsupported response")
        raw=response.read(MAX_BODY+1)
        reply=adapters.resolve_answer_path(_parse(raw),config["upstream_answer_path"])
        if not isinstance(reply,str) or not reply.strip() or len(reply.encode("utf-8"))>MAX_TEXT:
            raise BridgeError("invalid upstream answer")
        # Never return provider metadata, headers or raw error bodies.
        return reply
    except (OSError,ValueError,TypeError,KeyError,RuntimeError,http.client.HTTPException,UnicodeError):
        raise BridgeError("approved upstream request failed") from None
    finally:
        conn.close()


class Bridge:
    def __init__(self,config,approved_digest,*,confirmed=False):
        self.config=json.loads(buyer_plan._canonical(validate(config)))
        if confirmed is not True or not hmac.compare_digest(binding(self.config),approved_digest):
            raise BridgeError("exact binding and explicit external data transmission approval required")
        self.local,self.remote=_credentials(self.config)
        self.path=urllib.parse.urlsplit(connector(self.config)["url"]).path
        self.used=0
        self.deadline=time.monotonic()+self.config["max_seconds"]

    def request(self,path,auth,raw):
        if path!=self.path or not isinstance(auth,str) or not hmac.compare_digest(auth,"Bearer "+self.local):
            raise BridgeError("bridge access denied")
        if self.used>=self.config["max_calls"] or time.monotonic()>=self.deadline:
            raise BridgeError("approved bridge budget exhausted")
        payload=_payload(self.config,_parse(raw))
        self.used+=1  # failures count; no retries
        return {"answer":_upstream(self.config,payload,self.remote)}


def server(bridge):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self,*args):
            pass

        def _send(self,status,doc):
            raw=json.dumps(doc,ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type","application/json")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(raw)))
            self.send_header("Connection","close")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            try:
                lengths=self.headers.get_all("Content-Length",[])
                auth=self.headers.get_all("Authorization",[])
                if (len(lengths)!=1 or len(auth)!=1 or self.headers.get("Transfer-Encoding")
                        or self.headers.get("Content-Type","").split(";")[0]!="application/json"
                        or self.headers.get("Host")!="127.0.0.1:"+str(bridge.config["listen_port"])):
                    raise BridgeError("invalid bridge request")
                # Refuse unauthorized requests before reading their bodies.
                if self.path!=bridge.path or not hmac.compare_digest(auth[0],"Bearer "+bridge.local):
                    raise BridgeError("bridge access denied")
                n=int(lengths[0])
                if not 0<n<=MAX_BODY:
                    raise BridgeError("invalid body size")
                raw=self.rfile.read(n)
                if len(raw)!=n:
                    raise BridgeError("truncated body")
                answer=bridge.request(self.path,auth[0],raw)
            except (OSError,ValueError,TypeError,RuntimeError):
                self._send(403,{"error":"bridge request refused or failed"})
                return
            self._send(200,answer)

    return HTTPServer(("127.0.0.1",bridge.config["listen_port"]),Handler)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("describe","serve"))
    p.add_argument("config")
    p.add_argument("--approve-binding",default="")
    p.add_argument("--confirm-external-transmission",action="store_true")
    args=p.parse_args(argv)
    try:
        config=load(args.config)
        if args.action=="describe":
            print(json.dumps({"binding_hash":binding(config),"connector":connector(config),
                              "data_scope":config["data_scope"],"decision_grade":False},indent=2))
            return 0
        bridge=Bridge(config,args.approve_binding,confirmed=args.confirm_external_transmission)
        with server(bridge) as srv:
            srv.timeout=1
            while bridge.used<config["max_calls"] and time.monotonic()<bridge.deadline:
                srv.handle_request()
        return 0
    except (OSError,ValueError,RuntimeError,TypeError,KeyError):
        print("External candidate bridge refused or failed; inspect approved configuration.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
