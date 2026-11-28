"""Offline, explicit-acknowledgment alert-feed transition contract.

No network delivery, safety resolution, clinical authorization or source attestation.
The caller must retain the pending batch before delivery and commit its checkpoint
only after an approved delivery mechanism acknowledges that exact batch.
"""
import hashlib
import json
import re
from datetime import date

MAX_ALERTS = 1600
KINDS = frozenset(("missing_window", "low_volume", "threshold", "version_overlap",
                   "version_change", "incident", "scope_stale", "schedule_ended"))
SEVERITIES = ("low", "moderate", "serious", "critical")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _keys(doc, fields):
    _require(isinstance(doc, dict) and set(doc) == set(fields.split()), "unexpected fields")


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _digest(doc):
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()


def _seal(doc):
    return {**doc, "digest": _digest(doc)}


def _feed(doc):
    _keys(doc, "schema monitor_id plan_digest as_of alerts attention_required decision_grade deployment_authorized")
    _require(doc["schema"] == "lifecycle-monitor-feed/1" and isinstance(doc["monitor_id"], str)
             and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", doc["monitor_id"]) is not None
             and _hex(doc["plan_digest"]) and doc["decision_grade"] is False
             and doc["deployment_authorized"] is False, "invalid monitor feed scope")
    day = date.fromisoformat(doc["as_of"])
    _require(day.isoformat() == doc["as_of"], "canonical feed date required")
    alerts = doc["alerts"]
    _require(isinstance(alerts, list) and len(alerts) <= MAX_ALERTS, "invalid alert inventory")
    seen = set()
    for alert in alerts:
        _keys(alert, "id kind severity")
        _require(_hex(alert["id"]) and alert["id"] not in seen and alert["kind"] in KINDS
                 and alert["severity"] in SEVERITIES, "invalid or duplicate alert")
        seen.add(alert["id"])
    _require(doc["attention_required"] is bool(alerts), "inconsistent attention flag")
    return {**doc, "alerts": sorted((dict(a) for a in alerts), key=lambda a: a["id"])}


def checkpoint(feed):
    return _seal({"schema": "monitor-alert-checkpoint/1", "feed": _feed(feed),
                  "clinical_safety_established": False})


def _checkpoint(doc):
    _require(checkpoint(doc["feed"]) == doc, "invalid checkpoint digest or content")
    return doc


def prepare(current, previous=None):
    """Create an immutable transition batch. Missing feed is never an empty feed."""
    new = checkpoint(current)
    before = {} if previous is None else _checkpoint(previous)
    feed = new["feed"]
    if before:
        old = before["feed"]
        _require((old["monitor_id"], old["plan_digest"]) == (feed["monitor_id"], feed["plan_digest"]),
                 "checkpoint scope mismatch; initialize separate outbox")
        _require(old["as_of"] <= feed["as_of"], "feed date moved backwards")
    else:
        old = {"alerts": []}
    a = {r["id"]: r for r in old["alerts"]}
    b = {r["id"]: r for r in feed["alerts"]}
    common = sorted(a.keys() & b.keys())
    # Stable IDs are derived from the source condition; a changed type under
    # an existing ID is inconsistent, while a severity change is noteworthy.
    _require(all(a[k]["kind"] == b[k]["kind"] for k in common), "alert kind changed under stable ID")
    events = ([{"transition": "newly_reported", "alert": b[k]} for k in sorted(b.keys()-a.keys())]
              + [{"transition": "severity_changed", "previous_severity": a[k]["severity"], "alert": b[k]}
                 for k in common if a[k]["severity"] != b[k]["severity"]]
              + [{"transition": "no_longer_reported", "alert": a[k]} for k in sorted(a.keys()-b.keys())])
    return _seal({"schema": "monitor-alert-outbox/1", "previous": previous, "next_checkpoint": new,
                  "events": events, "persistent_count": sum(a[k] == b[k] for k in common),
                  "delivery_required": bool(events), "delivery_verified": False,
                  "clinical_safety_established": False, "deployment_authorized": False})


def verify(batch):
    rebuilt = prepare(batch["next_checkpoint"]["feed"], batch["previous"])
    _require(rebuilt == batch, "outbox replay mismatch")
    return rebuilt


def acknowledge(batch, expected_digest, *, delivered=False):
    """Return the exact next checkpoint, only with explicit delivery attestation.

    For an event-free transition, no delivery attestation is needed, but exact
    digest approval is still required. Attestation is not independently verified.
    """
    batch = verify(batch)
    _require(expected_digest == batch["digest"], "outbox digest mismatch")
    _require(type(delivered) is bool and (not batch["delivery_required"] or delivered),
             "explicit delivery attestation required")
    return batch["next_checkpoint"]


def _read(path):
    with open(path, "rb") as f:
        raw = f.read(2*1024*1024+1)
    _require(len(raw) <= 2*1024*1024, "outbox file too large")
    def pairs(items):
        result = {}
        for k,v in items:
            _require(k not in result, "duplicate JSON key")
            result[k] = v
        return result
    def nonfinite(value):
        raise ValueError("nonfinite JSON value")
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)


def configure(parser):
    sub = parser.add_subparsers(dest="outbox_action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("feed")
    p.add_argument("--previous")
    p = sub.add_parser("verify")
    p.add_argument("batch")
    p = sub.add_parser("acknowledge")
    p.add_argument("batch")
    p.add_argument("--expected-digest", required=True)
    p.add_argument("--confirm-delivered", action="store_true")
    parser.set_defaults(func=cli)


def cli(args):
    try:
        if args.outbox_action == "prepare":
            result = prepare(_read(args.feed), _read(args.previous) if args.previous else None)
        elif args.outbox_action == "verify":
            batch = verify(_read(args.batch))
            result = {"status": "REPLAY_VERIFIED", "digest": batch["digest"], "delivery_verified": False}
        else:
            result = acknowledge(_read(args.batch), args.expected_digest, delivered=args.confirm_delivered)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError):
        raise SystemExit("Monitor alert outbox request refused.")
