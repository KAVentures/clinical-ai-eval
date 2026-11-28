"""Portable experimental research evidence with independent numerical replay.

Never exports original packet HMAC keys, credentials, or the institution database.
Reconstructed packets are ephemeral replay artifacts, NOT original attestations.
A digest supplied over a trusted separate channel binds the expected export.
"""
import hashlib
import hmac
import json
import re
import tempfile
from .experimental_plan_inspect import canonical_equal
from pathlib import Path

from . import (buyer_agent as buyer, buyer_manual as manual, buyer_plan,
               citation_assessment as citation, scribe_assessment as scribe,
               decision_assessment as decision, robustness_assessment as robustness,
               formal_review as formal)

SCHEMA = "experimental-evidence-replay/1"
MODULES = {"citation":citation, "scribe":scribe, "decision":decision, "robustness":robustness}
LIMIT = manual.MAX_REQUEST_BYTES
LIMITATIONS = [
    "Replays supplied evidence only; does not authenticate vendor responses or clinician identity.",
    "Fresh temporary review packets are generated solely for replay; original signing secrets are excluded.",
    "An independently communicated digest is required to identify an expected original export.",
    "Not a canonical assessment manifest, clinical validation, procurement finding or deployment approval."
]


def fail(message):
    raise buyer.BuyerError(message)


def _strict(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        fail("unexpected experimental evidence fields")


def encode(package):
    raw = json.dumps(package, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > LIMIT:
        fail("experimental evidence exceeds portable package limit")
    return raw


def load(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        fail("regular evidence JSON file required")
    with p.open("rb") as f:
        raw = f.read(LIMIT+1)
    return manual._parse(raw)


def _expected_digest(value):
    if not isinstance(value,str) or re.fullmatch("[0-9a-f]{64}",value) is None:
        fail("expected digest must be lowercase SHA-256")


def build(kind, plan, responses, binding, submissions, report):
    payload = {"schema":SCHEMA, "kind":kind, "plan":plan, "responses":responses,
               "binding":binding, "submissions":submissions, "report":report,
               "decision_grade":False, "limitations":LIMITATIONS[:]}
    package = {**payload,"digest":buyer_plan._digest(payload)}
    # Refuse a package that cannot be replayed, including definition drift.
    verify(package)
    return package


def _replay_review(mod, plan, responses, binding, records):
    if not isinstance(binding,dict) or not isinstance(records,dict):
        fail("complete review binding and submissions required")
    with tempfile.TemporaryDirectory(prefix="caeval-evidence-replay-") as tmp:
        root = Path(tmp)/"review"
        if mod is robustness:
            args = (binding["validity_reviewers"],binding["answer_reviewers"],binding["operator"])
            ids = set(args[0]) | set(args[1])
        else:
            args = (binding["reviewers"],binding["operator"])
            ids = set(args[0])
        if set(records) != ids:
            fail("submission inventory differs from frozen roster")
        reconstructed = mod.prepare(plan,responses,root,*args,confirmed=True)
        if not canonical_equal(reconstructed,binding):
            fail("reconstructed review binding differs")
        for rid in sorted(ids):
            record = records[rid]
            _strict(record,("binding_hash","reviewer","operator","operator_attested","submission","digest"))
            if record["operator_attested"] is not True or record["reviewer"] != rid:
                fail("invalid supplied review record")
            document = mod.template(root,rid)
            document["submission"] = record["submission"]
            mod.submit(root,rid,document,record["operator"],confirmed=True)
            replayed = formal._json(root/"submissions"/(rid+".json"))
            if not canonical_equal(replayed,record):
                fail("replayed submission differs from supplied record")
        return mod.analyze(root)


def verify(package, *, expected_digest=None):
    """Regenerate full scientific report without network or original keys."""
    try:
        _strict(package,("schema","kind","plan","responses","binding","submissions","report",
                         "decision_grade","limitations","digest"))
        raw = encode(package)
        # Hash the same JSON value contract used by the research engine.
        digest = package["digest"]
        _expected_digest(digest)
        if digest != buyer_plan._digest({k:v for k,v in package.items() if k!="digest"}):
            fail("experimental evidence digest mismatch")
        if expected_digest is not None:
            _expected_digest(expected_digest)
            if not hmac.compare_digest(digest,expected_digest):
                fail("package differs from independently supplied digest")
        if (package["schema"]!=SCHEMA or package["decision_grade"] is not False
                or package["limitations"]!=LIMITATIONS or package["kind"] not in MODULES):
            fail("unsupported experimental evidence schema or claim")
        mod = MODULES[package["kind"]]
        plan = mod.verify_plan(package["plan"])
        if not canonical_equal(plan,package["plan"]):
            fail("plan differs from current definition")
        if mod is decision:
            if package["binding"] is not None or package["submissions"] != {}:
                fail("decision replay must not contain human review records")
            computed = mod.assess(plan,package["responses"])
        else:
            computed = _replay_review(mod,plan,package["responses"],package["binding"],package["submissions"])
        if not canonical_equal(computed,package["report"]) or computed.get("decision_grade") is not False:
            fail("scientific report differs from independently replayed result")
        return {"status":"REPLAY_VERIFIED", "kind":package["kind"], "package_digest":digest,
                "report_digest":computed["digest"], "plan_hash":plan["plan_hash"],
                "expected_digest_matched":expected_digest is not None,
                "encoded_sha256":hashlib.sha256(raw).hexdigest(),
                "decision_grade":False, "limitations":LIMITATIONS[:]}
    except (KeyError,IndexError,AttributeError,TypeError,ValueError,OverflowError,RuntimeError,OSError) as exc:
        if isinstance(exc,buyer.BuyerError):
            raise
        raise buyer.BuyerError("malformed or unreplayable experimental evidence") from exc


def from_study(directory, actor, identifier, *, confirmed=False):
    """Administrator-only export of complete, sensitive research evidence."""
    from . import institution_research as studio, institution_access as access
    if confirmed is not True:
        fail("explicit approval to export full research evidence required")
    with access.db(directory) as conn:
        studio._schema(conn)
        studio._actor(conn,actor,"admin")
        row = studio._row(conn,identifier)
        root,mod,plan = studio._plan(directory,row)
        report = studio._report(directory,conn,row)
        responses = studio._verified_collection(directory,conn,row,plan,mod,studio._collected(root,row))
        binding,records = None,{}
        if mod is not decision:
            _,_,_,(binding,_,issued) = studio._review(directory,conn,row)
            records = {rid:formal._json(root/"review"/"submissions"/(rid+".json")) for rid in issued}
        package = build(row["kind"],plan,responses,binding,records,report)
        access._audit(conn,actor["user_id"],"research-replay-package-export",identifier)
        return package


def cli(args):
    """Separate command; deliberately not accepted by canonical verify-package."""
    try:
        result = verify(load(args.package),expected_digest=args.expected_digest)
    except (buyer.BuyerError,OSError) as exc:
        # No clinical text or local paths in CLI error output.
        print(json.dumps({"ok":False,"error":"experimental evidence verification failed",
                          "decision_grade":False}))
        raise SystemExit(1) from exc
    print(json.dumps({"ok":True,"result":result},ensure_ascii=False,allow_nan=False))
