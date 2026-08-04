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
from dataclasses import dataclass, field

from .util import stable_hash_text

# Config keys that hold secrets. Never hashed, never serialized, never logged.
SECRET_KEYS = {"api_key", "token", "authorization", "auth", "password", "secret",
               "bearer", "cookie", "session_token", "client_secret"}

REDACTED = "<redacted>"


class AdapterError(RuntimeError):
    """Raised when an endpoint cannot be used. Always fail closed: a product we
    cannot talk to is an unknown product, never a safe one."""


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

    def __post_init__(self):
        if self.kind not in ("mock", "http_single_turn", "http_conversation", "callable"):
            raise AdapterError(f"unknown adapter kind {self.kind!r}")
        if self.kind.startswith("http") and not self.url:
            raise AdapterError(f"adapter {self.adapter_id!r}: kind {self.kind!r} requires a url")
        if self.kind == "http_conversation":
            self.modality = "conversation"

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

    def _reset(self):
        self._session_id = None

    def _reply(self, history) -> str:
        s = self.spec
        payload = {s.history_field: [{"role": h["role"], "content": h["text"]} for h in history],
                   **s.extra}
        if s.session_field and self._session_id:
            payload[s.session_field] = self._session_id
        req = urllib.request.Request(
            s.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **s.headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=s.timeout) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise AdapterError(f"{s.adapter_id}: HTTP {e.code} from {_host_of(s.url)}") from e
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"{s.adapter_id}: transport error to {_host_of(s.url)}: {e!r}") from e
        if s.session_field and isinstance(data, dict) and s.session_field in data:
            self._session_id = data[s.session_field]
        node = data
        for key in s.answer_path.split("."):
            if not isinstance(node, dict) or key not in node:
                raise AdapterError(
                    f"{s.adapter_id}: answer_path {s.answer_path!r} does not resolve in the "
                    f"response (stopped at {key!r}). Fix the adapter mapping rather than "
                    f"letting the cell score as empty.")
            node = node[key]
        return str(node)


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
