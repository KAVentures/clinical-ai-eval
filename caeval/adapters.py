"""Adapter layer — connecting a real product to the harness.

`subject.py` covers single-turn subjects. This module covers what that could not:

  * CONVERSATIONAL products, which the patient substrate needs. A multi-turn
    adapter must carry session state the harness does not own, so the contract is
    `adapter(history) -> reply_text` plus an explicit `reset()` between episodes.
    An adapter that leaks state across episodes silently destroys the paired
    design, so leakage is detected rather than trusted (see `probe_determinism`).
  * CAPABILITY PROBING before a run, so an incompatible endpoint fails at connect
    time with a specific reason instead of producing a run full of empty cells
    that later reads as a safe product.
  * CREDENTIAL SEPARATION. Connector credentials never enter the plan hash, the
    provenance record, or any artifact. `describe()` returns the redacted view and
    is the ONLY thing written to disk.

Nothing here can raise a conformance or maturity level. Connecting a real product
makes a run possible; it does not make the measurement valid.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import urllib.parse
import time
from dataclasses import dataclass, field

from .util import stable_hash_text

# Config keys that hold secrets. Never hashed, never serialized, never logged.
SECRET_KEYS = {"api_key", "token", "authorization", "auth", "password", "secret",
               "bearer", "cookie", "session_token", "client_secret"}

REDACTED = "<redacted>"
LOCAL_MAX_REPLY = 256 * 1024
LOCAL_MAX_REQUEST = 256 * 1024
LOCAL_MAX_TEXT = 6000
LOCAL_MAX_CALLS = 1500
LOCAL_MAX_SECONDS = 900


class AdapterError(RuntimeError):
    """Raised when an endpoint cannot be used. Always fail closed: a product we
    cannot talk to is an unknown product, never a safe one."""


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(obj, environ=None):
    """Replace `${NAME}` in strings with the environment variable NAME.

    This is how a credential reaches a product endpoint without being written
    into project.yaml: `Authorization: "Bearer ${MY_PRODUCT_TOKEN}"`. A reference
    to an unset variable fails closed rather than sending an empty credential."""
    import os
    env = os.environ if environ is None else environ
    if isinstance(obj, str):
        missing = [n for n in _ENV_REF.findall(obj) if not env.get(n)]
        if missing:
            raise AdapterError(f"environment variable(s) not set: {', '.join(sorted(set(missing)))}")
        return _ENV_REF.sub(lambda m: env[m.group(1)], obj)
    if isinstance(obj, dict):
        return {k: expand_env(v, env) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [expand_env(v, env) for v in obj]
    return obj


def persistable(obj):
    """The view of a connector spec that may be written to a workspace or an
    evidence package. A secret-named field keeps its value only when the value is
    a pure `${NAME}` reference (which names a variable, not a secret); anything
    else under a secret-named key, at any depth, is replaced."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            secret = str(k).lower() in SECRET_KEYS or any(t in str(k).lower() for t in ("key", "token", "secret"))
            if secret and not (isinstance(v, str) and _is_reference_only(v)):
                out[k] = REDACTED if not isinstance(v, (dict, list)) else persistable(v)
            else:
                out[k] = persistable(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [persistable(v) for v in obj]
    return obj


def _is_reference_only(value: str) -> bool:
    """True when a secret-named value holds nothing but variable references and
    a fixed scheme word, e.g. "Bearer ${TOKEN}" or "${API_KEY}"."""
    rest = _ENV_REF.sub("", value).strip()
    return bool(_ENV_REF.search(value)) and rest.lower() in ("", "bearer", "token", "basic", "apikey", "api-key", "key")


def resolve_answer_path(data, path: str, adapter_id: str = "subject"):
    """Follow a dotted path through dicts and lists: `data.text`,
    `choices.0.message.content`. Fails with the step that did not resolve."""
    node = data
    for key in str(path).split("."):
        if isinstance(node, list) and key.lstrip("-").isdigit() and -len(node) <= int(key) < len(node):
            node = node[int(key)]
        elif isinstance(node, dict) and key in node:
            node = node[key]
        else:
            raise AdapterError(
                f"{adapter_id}: answer_path {path!r} does not resolve in the response "
                f"(stopped at {key!r}). Fix the mapping rather than letting the cell score as empty.")
    if isinstance(node, (dict, list)):
        raise AdapterError(f"{adapter_id}: answer_path {path!r} points at a {type(node).__name__}, not text")
    return node


RETRYABLE_HTTP = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def post_json(url: str, payload: dict, headers: dict, *, timeout: float, retries: int = 3,
              backoff: float = 1.0, adapter_id: str = "subject", sleep=None):
    """POST JSON and return the decoded body, retrying transient failures.

    Errors name the host and status only. Response bodies are never put into an
    exception, because a product's error page can echo the clinical prompt."""
    import time
    sleep = sleep or time.sleep
    host = _host_of(url)
    last = ""
    for attempt in range(1, max(1, int(retries)) + 1):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise AdapterError(f"{adapter_id}: {host} returned a non-JSON body") from e
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code not in RETRYABLE_HTTP:
                raise AdapterError(f"{adapter_id}: {last} from {host}") from None
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001  timeouts, refused connections, DNS
            last = f"transport error ({type(e).__name__})"
        if attempt < retries:
            sleep(backoff * (2 ** (attempt - 1)))
    raise AdapterError(f"{adapter_id}: {last} from {host} after {retries} attempt(s)")


def validate_local_endpoint(url):
    """Literal loopback only; no DNS, credentials, fragments or redirects."""
    try:
        if not isinstance(url,str) or not url.isascii() or len(url)>512 or any(c.isspace() for c in url):
            raise ValueError()
        p=urllib.parse.urlsplit(url)
        if (p.scheme!="http" or p.hostname not in ("127.0.0.1","::1")
                or not p.port or p.username is not None or p.password is not None
                or p.query or p.fragment or not re.fullmatch(r"/[A-Za-z0-9_./-]*",p.path)
                or any(x in (".","..") for x in p.path.split("/"))
                or p.netloc not in ("127.0.0.1:"+str(p.port),"[::1]:"+str(p.port))):
            raise ValueError()
    except (ValueError,TypeError):
        raise AdapterError("local-only transport requires literal loopback HTTP with explicit port") from None
    return url


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AdapterError("local candidate redirect refused")


def post_local_json(url, payload, headers, *, timeout, strict=False):
    """Bounded single-attempt loopback transport. Never echoes response bodies."""
    validate_local_endpoint(url)
    if (type(timeout) is not int or not 1<=timeout<=60 or not isinstance(headers,dict)
            or set(headers)-{"Authorization","X-API-Key"}
            or any(not isinstance(v,str) or len(v)>8192 or "\r" in v or "\n" in v for v in headers.values())):
        raise AdapterError("invalid local transport options")
    try:
        raw=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode("utf-8")
        if len(raw)>LOCAL_MAX_REQUEST:
            raise AdapterError("local candidate request exceeds limit")
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_RefuseRedirect())
        req=urllib.request.Request(url,data=raw,headers={"Content-Type":"application/json",**headers},method="POST")
        with opener.open(req,timeout=timeout) as response:
            if strict and (response.status!=200 or response.headers.get_content_type()!="application/json"):
                raise AdapterError("standard conversation requires HTTP 200 JSON")
            raw=response.read(LOCAL_MAX_REPLY+1)
        if len(raw)>LOCAL_MAX_REPLY:
            raise AdapterError("local candidate reply exceeds limit")
        if strict:
            from . import buyer_manual
            return buyer_manual._parse(raw)
        return json.loads(raw.decode("utf-8"))
    except AdapterError:
        raise
    except (OSError,ValueError,TypeError,UnicodeError,RecursionError):
        raise AdapterError("local candidate transport or JSON response failed") from None


def redact(obj):
    """Deep-redact secrets. Applied to everything that leaves this module."""
    if isinstance(obj, dict):
        return {k: (REDACTED if k.lower() in SECRET_KEYS else redact(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return obj


@dataclass
class AdapterSpec:
    """Declarative description of a product endpoint."""
    adapter_id: str
    kind: str                       # mock | http_single_turn | http_conversation | callable
    version: str = "unknown"
    url: str = ""
    prompt_field: str = "prompt"
    history_field: str = "messages"
    answer_path: str = "answer"
    session_field: str = ""         # set if the product wants its own session id echoed back
    headers: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    timeout: int = 120
    modality: str = "single_turn"   # single_turn | conversation
    local_only: bool = False        # opt-in restricted institutional transport
    protocol: str = ""              # optional versioned full-history contract
    target_name: str = ""

    def __post_init__(self):
        if self.kind not in ("mock", "http_single_turn", "http_conversation", "callable"):
            raise AdapterError(f"unknown adapter kind {self.kind!r}")
        if self.kind.startswith("http") and not self.url:
            raise AdapterError(f"adapter {self.adapter_id!r}: kind {self.kind!r} requires a url")
        if self.kind == "http_conversation":
            self.modality = "conversation"
        if type(self.local_only) is not bool:
            raise AdapterError("local_only must be a boolean")
        if self.protocol:
            from . import conversation_protocol
            if (self.protocol!=conversation_protocol.PROTOCOL or not self.local_only
                    or self.kind!="http_conversation" or self.session_field
                    or self.history_field!="messages" or self.answer_path!="answer"):
                raise AdapterError("standard conversation requires local full-history transport without session mapping")
            conversation_protocol.base._target({"name":self.target_name,"version":self.version})
        if self.local_only:
            if self.kind!="http_conversation":
                raise AdapterError("local-only adapter requires conversational HTTP")
            validate_local_endpoint(self.url)
            if type(self.timeout) is not int or not 1<=self.timeout<=60:
                raise AdapterError("local-only timeout must be 1–60 seconds")
            fields=(self.history_field,self.session_field)
            if (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}",fields[0])
                    or fields[1] and (fields[1]==fields[0] or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}",fields[1]))
                    or not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*",self.answer_path)
                    or len(self.answer_path)>200):
                raise AdapterError("invalid local-only response/history mapping")
            if (not isinstance(self.extra,dict) or set(self.extra)-{"_retries"}
                    or self.extra.get("_retries",1)!=1):
                raise AdapterError("local-only transport permits no extra payload fields or retries")
            if (not isinstance(self.headers,dict) or set(self.headers)-{"Authorization","X-API-Key"}
                    or any(not isinstance(v,str) or not re.fullmatch(r"(?:Bearer )?\$\{[A-Za-z_][A-Za-z0-9_]*\}",v)
                           for v in self.headers.values())):
                raise AdapterError("local-only credentials must be approved environment references")

    def describe(self) -> dict:
        """The ONLY view of this spec that may be written to an artifact.

        Credentials are removed rather than hashed: a hash of a secret is still a
        secret-derived value in a file we publish, and it lets an attacker confirm
        a guess. The endpoint identity is the host, not the key.
        """
        return {
            "adapter_id": self.adapter_id,
            "kind": self.kind,
            "version": self.version,
            "modality": self.modality,
            "endpoint_host": _host_of(self.url),
            "headers": redact(self.headers),
            "extra": redact(self.extra),
            **({"local_only": True} if self.local_only else {}),
            **({"protocol": self.protocol,"target_name":self.target_name} if self.protocol else {}),
        }

    def identity_hash(self) -> str:
        """Content address of WHAT was tested — deliberately excludes credentials,
        so rotating a key does not look like testing a different product."""
        return stable_hash_text(json.dumps(self.describe(), sort_keys=True))[:16]


def _host_of(url: str) -> str:
    m = re.match(r"^[a-z]+://([^/]+)", url or "")
    return m.group(1) if m else ""


# --------------------------------------------------------------------------
# Conversational adapters
# --------------------------------------------------------------------------

class ConversationAdapter:
    """`adapter(history) -> reply`. Subclasses implement `_reply` and `_reset`."""

    def __init__(self, spec: AdapterSpec):
        self.spec = spec
        self.calls = 0

    def __call__(self, history) -> str:
        self.calls += 1
        try:
            reply = self._reply(history)
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"adapter {self.spec.adapter_id!r} failed on turn "
                               f"{self.calls}: {e!r}") from e
        if not isinstance(reply, str) or not reply.strip():
            # An empty reply must not be scored as a safe non-answer.
            raise AdapterError(f"adapter {self.spec.adapter_id!r} returned an empty reply on "
                               f"turn {self.calls}; refusing to score it as a response")
        return reply

    def reset(self):
        """Called between episodes. State that survives a reset breaks pairing."""
        self.calls = 0
        self._reset()

    def _reply(self, history) -> str:
        raise NotImplementedError

    def _reset(self):
        pass


class CallableAdapter(ConversationAdapter):
    """Wraps a plain python callable — the mock targets, or a local model."""

    def __init__(self, spec: AdapterSpec, fn, reset_fn=None):
        super().__init__(spec)
        self._fn = fn
        self._reset_fn = reset_fn

    def _reply(self, history) -> str:
        return self._fn(history)

    def _reset(self):
        if self._reset_fn:
            self._reset_fn()


class HttpConversationAdapter(ConversationAdapter):
    """POSTs the whole transcript each turn, or echoes a product session id."""

    def __init__(self, spec: AdapterSpec):
        super().__init__(spec)
        self._session_id = None
        self._total_calls = 0
        self._deadline = time.monotonic()+LOCAL_MAX_SECONDS
        self._episode_id = None
        self._reset()

    def _reset(self):
        self._session_id = None
        if self.spec.protocol:
            import secrets
            self._episode_id=secrets.token_hex(16)

    def _reply(self, history) -> str:
        s = self.spec
        if s.protocol:
            from . import conversation_protocol
            if self._total_calls>=LOCAL_MAX_CALLS or time.monotonic()>=self._deadline:
                raise AdapterError("local candidate run budget exhausted")
            payload=conversation_protocol.request({"name":s.target_name,"version":s.version},self._episode_id,history)
            self._total_calls+=1
            try:
                data=post_local_json(s.url,payload,expand_env(s.headers),timeout=s.timeout,strict=True)
                data=conversation_protocol.validate_response(payload,data)
            except (ValueError,RuntimeError,TypeError,KeyError):
                raise AdapterError("standard conversation transport or correlation failed") from None
            if data["status"]!="complete":
                raise AdapterError("candidate explicitly unavailable")
            return data["answer"]
        payload = {s.history_field: [{"role": h["role"], "content": h["text"]} for h in history],
                   **expand_env({k: v for k, v in s.extra.items() if not str(k).startswith("_")})}
        if s.session_field and self._session_id:
            payload[s.session_field] = self._session_id
        if s.local_only:
            if self._total_calls>=LOCAL_MAX_CALLS or time.monotonic()>=self._deadline:
                raise AdapterError("local candidate run budget exhausted")
            self._total_calls+=1
            data=post_local_json(s.url,payload,expand_env(s.headers),timeout=s.timeout)
        else:
            data = post_json(expand_env(s.url), payload, expand_env(s.headers), timeout=s.timeout,
                             retries=int(s.extra.get("_retries", 3)) if isinstance(s.extra, dict) else 3,
                             adapter_id=s.adapter_id)
        if s.session_field and isinstance(data, dict) and s.session_field in data:
            sid=data[s.session_field]
            if s.local_only and (not isinstance(sid,str) or not sid.strip() or len(sid)>512):
                raise AdapterError("invalid local candidate session identifier")
            self._session_id = sid
        reply=resolve_answer_path(data,s.answer_path,s.adapter_id)
        if s.local_only and (not isinstance(reply,str) or not reply.strip() or len(reply.encode("utf-8"))>LOCAL_MAX_TEXT):
            raise AdapterError("invalid local candidate reply text")
        return str(reply)


def build_conversation_adapter(spec: AdapterSpec, fn=None) -> ConversationAdapter:
    if spec.kind in ("mock", "callable"):
        if fn is None:
            raise AdapterError(f"{spec.adapter_id}: kind {spec.kind!r} needs a callable")
        return CallableAdapter(spec, fn)
    if spec.kind == "http_conversation":
        return HttpConversationAdapter(spec)
    raise AdapterError(f"{spec.adapter_id}: kind {spec.kind!r} is not conversational; "
                       f"use caeval.subject.build_subject for single-turn products")


# --------------------------------------------------------------------------
# Pre-run probing — fail at connect time, not in the results
# --------------------------------------------------------------------------

PROBE_OPENING = "I have had a headache since this morning."


def probe_liveness(adapter: ConversationAdapter) -> dict:
    """One throwaway exchange. Confirms the mapping resolves and a reply arrives."""
    adapter.reset()
    try:
        reply = adapter([{"role": "patient", "text": PROBE_OPENING}])
    except AdapterError as e:
        return {"ok": False, "reason": str(e)}
    finally:
        adapter.reset()
    return {"ok": True, "reply_chars": len(reply)}


def probe_determinism(adapter: ConversationAdapter, n: int = 3) -> dict:
    """Same opening, n times, with a reset between each.

    Two separate things are being checked. NON-DETERMINISM means paired
    comparisons carry sampling noise and need repeats to interpret. STATE LEAKAGE
    means the reset did not work and episode k saw episode k-1 — which silently
    destroys the paired design, because the 'control' is no longer a control.
    Neither is a reason to refuse a run; both are reasons the run must be labelled.
    """
    replies = []
    for _ in range(n):
        adapter.reset()
        try:
            replies.append(adapter([{"role": "patient", "text": PROBE_OPENING}]))
        except AdapterError as e:
            return {"ok": False, "reason": str(e)}
    adapter.reset()
    distinct = len(set(replies))
    return {
        "ok": True,
        "deterministic": distinct == 1,
        "distinct_replies": distinct,
        "trials": n,
        "pairing_valid": distinct == 1,
        "note": ("Identical replies after reset: pairing is sound." if distinct == 1 else
                 "Replies differ across identical resets. This is either sampling "
                 "non-determinism or state leaking across episodes; the harness cannot "
                 "tell them apart from outside. Paired deltas from this product are "
                 "NOT attributable to the perturbation alone without repeats."),
    }


REQUIRED_FOR_PATIENT = ["conversation", "multi_turn_history", "deterministic_or_repeated"]


def probe_capabilities(adapter: ConversationAdapter) -> dict:
    """What this endpoint can support, checked rather than declared."""
    live = probe_liveness(adapter)
    if not live["ok"]:
        return {"ok": False, "reason": live["reason"], "capabilities": []}
    det = probe_determinism(adapter)
    if not det.get("ok"):
        return {"ok": False, "reason": det.get("reason", "repeat probe failed"), "capabilities": []}
    caps = ["conversation", "multi_turn_history"]
    if det.get("deterministic"):
        caps.append("deterministic_or_repeated")
    missing = [c for c in REQUIRED_FOR_PATIENT if c not in caps]
    return {
        "ok": True,
        "capabilities": caps,
        "missing_for_patient_family": missing,
        "determinism": det,
        "adapter": adapter.spec.describe(),
        "adapter_identity_hash": adapter.spec.identity_hash(),
    }


def connect(spec: AdapterSpec, fn=None, require_deterministic: bool = False) -> tuple:
    """Build, probe, and return (adapter, probe_report). Fails closed on liveness."""
    adapter = build_conversation_adapter(spec, fn)
    report = probe_capabilities(adapter)
    if not report["ok"]:
        raise AdapterError(f"cannot connect {spec.adapter_id!r}: {report['reason']}")
    if require_deterministic and not report["determinism"].get("deterministic"):
        raise AdapterError(
            f"{spec.adapter_id!r} is not reproducible across resets and "
            f"require_deterministic was set: {report['determinism']['note']}")
    return adapter, report
