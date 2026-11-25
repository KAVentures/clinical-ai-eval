"""Portable committee dossiers: frozen metadata plus explicitly selected evidence.

No secret keys, patient packages, or filesystem paths are automatically exported.
The recipient independently checks content binding and replays embedded research.
A separately communicated digest is needed to identify the expected export.
"""
import base64
import binascii
import hashlib
import hmac
import json
import re
from pathlib import Path

from . import (buyer_agent as buyer, buyer_plan, buyer_manual as manual,
               institution_access as access, institution_committee as committee,
               experimental_evidence as evidence, research_program as programs,
               buyer_research, manifest)

SCHEMA = "portable-committee-dossier/1"
MAX_SELECTED = 16
LIMIT = manual.MAX_REQUEST_BYTES
LIMITATIONS = [
    "A content digest is not a signature or proof of clinical validity.",
    "A separately trusted expected dossier digest identifies the expected original export.",
    "Embedded vendor documents are byte-verified, not independently validated claims.",
    "Embedded experimental reports are independently replayed, not clinical findings.",
    "Canonical manifests are references unless the recipient separately supplies and verifies the exact package.",
    "Unembedded or unavailable evidence must not be treated as a pass.",
    "Historical evidence may no longer be current. No purchasing or deployment decision is authorized.",
]


def _fail(message):
    raise buyer.BuyerError(message)


def _strict(doc, keys):
    if not isinstance(doc, dict) or set(doc) != set(keys):
        _fail("unexpected portable dossier fields")


def _hash(value):
    if not isinstance(value,str) or not re.fullmatch(r"[0-9a-f]{64}",value):
        _fail("invalid SHA-256 reference")
    return value


def _digest(doc):
    return buyer_plan._digest(doc)


def encode(doc):
    raw=buyer_plan._canonical(doc)
    if len(raw)>LIMIT:
        _fail("portable dossier exceeds approved exchange size; select fewer attachments")
    return raw


def load(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file():
        _fail("regular dossier file required")
    with p.open("rb") as f:
        return manual._parse(f.read(LIMIT+1))


def _snapshot(saved):
    _strict(saved,("snapshot_id","payload","current","dispositions","decision_grade"))
    if (saved["decision_grade"] is not False or type(saved["current"]) is not bool or
            _hash(saved["snapshot_id"])!=_digest(saved["payload"])):
        _fail("frozen snapshot integrity mismatch")
    payload=saved["payload"]
    if (payload.get("schema")!="institution-committee-evidence/1" or
            payload.get("decision_grade") is not False):
        _fail("unsupported or promoted committee snapshot")
    dispositions=saved["dispositions"]
    if not isinstance(dispositions,list):
        _fail("invalid dispositions")
    ids=set()
    for row in dispositions:
        _strict(row,("id","snapshot_id","actor","disposition","rationale","created_at"))
        if (row["snapshot_id"]!=saved["snapshot_id"] or
                row["disposition"] not in committee.DISPOSITIONS or
                not isinstance(row["rationale"],str) or not row["rationale"].strip() or
                not isinstance(row["actor"],str) or not row["actor"] or
                not isinstance(row["created_at"],str) or not row["created_at"] or
                _hash(row["id"])!=_digest({k:row[k] for k in ("snapshot_id","actor","disposition","rationale")}) or
                row["id"] in ids):
            _fail("invalid or changed administrative disposition")
        ids.add(row["id"])
    return payload


def _inventory(payload):
    """Derive every referenced item from the exact frozen snapshot, never a supplied index."""
    docs={}
    seen=set()
    for vendor in payload["vendors"]:
        vid=vendor["vendor_id"]
        if vid in seen:
            _fail("duplicate vendor")
        seen.add(vid)
        for row in vendor["documents"]:
            eid=row["evidence_id"]
            if eid in docs:
                _fail("duplicate evidence identifier")
            digest=row["sha256"]
            # Malformed/invalid records remain disclosed, not embeddable.
            eligible=(row["integrity"]=="intact" and isinstance(digest,str) and
                      re.fullmatch(r"[0-9a-f]{64}",digest) is not None)
            docs[eid]={"vendor_id":vid,"kind":row["kind"],"sha256":digest,
                       "integrity_at_freeze":row["integrity"],"eligible":eligible}
    lifecycle=payload.get("lifecycle_inventory") or {}
    research={}
    for programme in lifecycle.get("experimental_research_programmes",[]):
        if programme.get("decision_grade") is not False:
            _fail("unexpected research claim authority")
        if programme.get("state")=="unavailable":
            continue
        for module in programme["modules"]:
            key=programme["id"]+":"+module["kind"]
            if key in research or module.get("decision_grade") is not False:
                _fail("duplicate or promoted research reference")
            digest=module["report_digest"]
            if digest is not None:
                _hash(digest)
            research[key]={"programme_id":programme["id"],"program_digest":_hash(programme["program_digest"]),
                           "vendor_id":programme["vendor_id"],"target_version":programme["target_version"],
                           "kind":module["kind"],"state_at_freeze":module["state"],
                           "report_digest":digest,"eligible":digest is not None}
    canonical={}
    def add(row,family,vid,version,name,origin):
        digest=_hash(row["manifest_hash"])
        ref={"family":family,"vendor_id":vid,"target_version":version,
             "target_name":name,"origin":origin}
        refs=canonical.setdefault(digest,[])
        if ref not in refs:
            refs.append(ref)
    for family in payload["clinical_comparison"]["families"]:
        for row in family.get("verified_packages",[]):
            add(row,family["family_id"],row["vendor_id"],row["target_version"],
                row["target_name"],"frozen_comparison")
    for job in lifecycle.get("assessments",[]):
        for row in job["packages"]:
            add(row,row["family_id"],job["vendor_id"],job["target_version"],None,"assessment_inventory")
    return {"documents":docs,"experimental_reports":research,"canonical_manifests":canonical}


def choices(saved):
    return _inventory(_snapshot(saved))


def _selection(values,inventory):
    if not isinstance(values,list) or any(not isinstance(x,str) for x in values) or len(set(values))!=len(values):
        _fail("distinct attachment identifiers required")
    if len(values)>MAX_SELECTED or any(x not in inventory or not inventory[x]["eligible"] for x in values):
        _fail("attachment is not eligible in the frozen snapshot")
    return sorted(values)


def _document(directory,eid,ref):
    root=buyer._root(directory)
    rows=[row for row in buyer.load(root)["evidence"] if row["evidence_id"]==eid]
    if len(rows)!=1:
        _fail("selected original document is unavailable")
    row=rows[0]
    if (any(row[k]!=ref[k] for k in ("vendor_id","kind","sha256")) or
            buyer._verify_document(root,row)!="intact"):
        _fail("selected original document changed")
    p=root/"evidence"/_hash(ref["sha256"])
    with p.open("rb") as f:
        raw=f.read(min(LIMIT,buyer.MAX_DOCUMENT_BYTES)+1)
    if len(raw)>LIMIT or hashlib.sha256(raw).hexdigest()!=ref["sha256"]:
        _fail("selected document exceeds limit or changed during export")
    return base64.b64encode(raw).decode("ascii")


def _research(directory,actor,ref):
    with access.db(directory) as conn:
        programs._schema(conn)
        record,payload,bp,related=programs._load(directory,conn,actor,ref["programme_id"])
        if (record["digest"]!=ref["program_digest"] or bp["vendor_id"]!=ref["vendor_id"] or
                payload["target"]["version"]!=ref["target_version"]):
            _fail("selected programme differs from frozen snapshot")
        member=payload["members"][ref["kind"]]
        sid=member["study_id"]
    package=evidence.from_study(directory,actor,sid,confirmed=True)
    if (package["kind"]!=ref["kind"] or package["report"]["digest"]!=ref["report_digest"] or
            package["plan"]["plan_hash"]!=member["plan_hash"] or
            package["plan"]["spec"]["target"]!=payload["target"]):
        _fail("selected study report differs from frozen snapshot")
    return package


def export(directory,actor,identifier,documents=None,research=None,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit dossier sharing approval required")
    saved=committee.snapshot(directory,actor,identifier)
    inventory=choices(saved)
    docs=_selection([] if documents is None else documents,inventory["documents"])
    reports=_selection([] if research is None else research,inventory["experimental_reports"])
    if len(docs)+len(reports)>MAX_SELECTED:
        _fail("too many attachments")
    if docs or reports:
        access.require(actor,"admin")
    doc={"schema":SCHEMA,"snapshot":saved,"inventory":inventory,
         "attachments":{"documents":{},"experimental_reports":{}},
         "decision_grade":False,"deployment_authorized":False,"limitations":LIMITATIONS[:]}
    for key in docs:
        doc["attachments"]["documents"][key]=_document(directory,key,inventory["documents"][key])
        encode(doc)
    for key in reports:
        doc["attachments"]["experimental_reports"][key]=_research(directory,actor,inventory["experimental_reports"][key])
        encode(doc)
    result={**doc,"digest":_digest(doc)}
    # The exact portable object is verified before release.
    verify(result)
    with access.db(directory) as conn:
        committee._actor(conn,actor)
        if docs or reports:
            access.require(actor,"admin")
        access._audit(conn,actor["user_id"],"portable-committee-dossier-exported",result["digest"])
    return result


def _canonical_package(path,inventory):
    p=Path(path)
    if p.is_symlink() or not p.is_dir():
        _fail("regular canonical package directory required")
    metadata=buyer_research._preflight(p)
    digest=_hash(metadata["manifest_hash"])
    refs=inventory.get(digest)
    if not refs:
        _fail("canonical package is not referenced by the frozen dossier")
    if manifest.verify_manifest(p)["verdict"]!=manifest.VALID:
        _fail("canonical package did not verify")
    binding=manual._parse(buyer_research.verified_bytes(p,metadata,"plan_binding"))
    for ref in refs:
        if (metadata["family_id"]!=ref["family"] or binding["family_id"]!=ref["family"] or
                binding["target_version"]!=ref["target_version"] or
                (ref["target_name"] is not None and binding["target_name"]!=ref["target_name"])):
            _fail("canonical package target or family differs from frozen reference")
    if (buyer_research._preflight(p)!=metadata or
            manifest.verify_manifest(p)["verdict"]!=manifest.VALID):
        _fail("canonical package changed during verification")
    return digest


def verify(doc,*,expected_digest=None,canonical_packages=()):
    """Verify without the source institution, local accounts, or network access."""
    encode(doc)
    _strict(doc,("schema","snapshot","inventory","attachments","decision_grade",
                 "deployment_authorized","limitations","digest"))
    digest=_hash(doc["digest"])
    if (doc["schema"]!=SCHEMA or doc["decision_grade"] is not False or
            doc["deployment_authorized"] is not False or doc["limitations"]!=LIMITATIONS or
            _digest({k:v for k,v in doc.items() if k!="digest"})!=digest):
        _fail("portable dossier integrity or authority mismatch")
    if expected_digest is not None and not hmac.compare_digest(digest,_hash(expected_digest)):
        _fail("dossier differs from independently communicated digest")
    inventory=choices(doc["snapshot"])
    if doc["inventory"]!=inventory:
        _fail("attachment inventory differs from frozen snapshot")
    attached=doc["attachments"]
    _strict(attached,("documents","experimental_reports"))
    if any(not isinstance(v,dict) for v in attached.values()):
        _fail("invalid attachment mappings")
    docs=_selection(list(attached["documents"]),inventory["documents"])
    reports=_selection(list(attached["experimental_reports"]),inventory["experimental_reports"])
    if len(docs)+len(reports)>MAX_SELECTED:
        _fail("too many attachments")
    for key in docs:
        try:
            encoded=attached["documents"][key]
            if not isinstance(encoded,str):
                _fail("invalid embedded document")
            raw=base64.b64decode(encoded,validate=True)
            if base64.b64encode(raw).decode("ascii")!=encoded:
                _fail("noncanonical embedded document encoding")
        except (ValueError,binascii.Error,UnicodeError) as exc:
            raise buyer.BuyerError("invalid embedded document encoding") from exc
        if hashlib.sha256(raw).hexdigest()!=inventory["documents"][key]["sha256"]:
            _fail("embedded vendor document hash mismatch")
    for key in reports:
        package=attached["experimental_reports"][key]
        receipt=evidence.verify(package)
        ref=inventory["experimental_reports"][key]
        if (receipt["kind"]!=ref["kind"] or receipt["report_digest"]!=ref["report_digest"] or
                package["plan"]["spec"]["target"]["version"]!=ref["target_version"]):
            _fail("embedded report differs from frozen reference")
    canonical=[]
    if len(canonical_packages)>MAX_SELECTED:
        _fail("too many external canonical packages")
    for path in canonical_packages:
        verified=_canonical_package(path,inventory["canonical_manifests"])
        if verified in canonical:
            _fail("duplicate external canonical package")
        canonical.append(verified)
    return {"status":"DOSSIER_INTEGRITY_VERIFIED","digest":digest,
            "snapshot_id":doc["snapshot"]["snapshot_id"],"expected_digest_matched":expected_digest is not None,
            "snapshot_current_at_export":doc["snapshot"]["current"],
            "n_dispositions":len(doc["snapshot"]["dispositions"]),
            "documents_byte_verified":docs,
            "documents_not_embedded":sorted(set(inventory["documents"])-set(docs)),
            "experimental_reports_replayed":reports,
            "experimental_reports_not_embedded":sorted(set(inventory["experimental_reports"])-set(reports)),
            "canonical_manifests_verified":sorted(canonical),
            "canonical_manifests_reference_only":sorted(set(inventory["canonical_manifests"])-set(canonical)),
            "decision_grade":False,"deployment_authorized":False,"limitations":LIMITATIONS[:]}


def cli(args):
    try:
        result=verify(load(args.dossier),expected_digest=args.expected_digest,
                      canonical_packages=args.canonical_package)
        print(json.dumps({"ok":True,"result":result},ensure_ascii=False,allow_nan=False))
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError,IndexError,OverflowError):
        print(json.dumps({"ok":False,"error":"portable committee dossier verification refused",
                          "decision_grade":False,"deployment_authorized":False}))
        raise SystemExit(1)
