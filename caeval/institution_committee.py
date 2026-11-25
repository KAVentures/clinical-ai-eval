"""Institutional committee evidence snapshots, not automated purchasing decisions.

Read the existing buyer evidence register and reconstruct the frozen study using
only verified, explicitly bound, locally staged evidence. No new scoring logic.
Institutional committee records are append-only administrative dispositions.
"""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

from . import buyer_agent as buyer, buyer_plan, buyer_manual as manual, buyer_research
from . import institution_access as access, manifest, procurement_workflow as pw

DISPOSITIONS = ("request_more_evidence", "defer", "refer_for_governance_review", "close_review")
MAX_SNAPSHOTS = 200


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS committee_snapshots(
      id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_by TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS committee_dispositions(
      id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES committee_snapshots(id),
      actor TEXT NOT NULL, disposition TEXT NOT NULL, rationale TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")


def _actor(conn, actor, write=False):
    access.require(actor, * (("admin","operator") if write else ("admin","operator","observer")))
    row=conn.execute("SELECT role,enabled FROM users WHERE id=?", (actor["user_id"],)).fetchone()
    if not row or not row["enabled"] or row["role"]!=actor["role"]:
        raise access.AccessDenied("active account required")


def _package(root, vendor, family, item):
    """Strict institutional import boundary; arbitrary external paths are refused."""
    package=item.get("package") or {}
    path=Path(package.get("workspace") or "")
    if not path.is_absolute():
        path=root/path
    staged=root/"assessment"/"packages"
    # Only IT-staged package directories within this organisation are eligible.
    if staged.is_symlink() or staged.parent.is_symlink():
        raise access.AccessDenied("invalid staging directory")
    try:
        relative=path.relative_to(staged)
    except ValueError as exc:
        raise access.AccessDenied("package not staged in institutional study") from exc
    if not relative.parts or any(p in ("..",".") for p in relative.parts):
        raise access.AccessDenied("invalid package path")
    cursor=staged
    for part in relative.parts:
        cursor=cursor/part
        if cursor.is_symlink():
            raise access.AccessDenied("linked package path refused")
    meta=buyer_research._preflight(path)
    digest=package.get("package_digest")
    if not digest or meta["manifest_hash"]!=digest or meta["family_id"]!=family:
        raise access.AccessDenied("recorded package changed")
    if manifest.verify_manifest(path)["verdict"]!=manifest.VALID:
        raise access.AccessDenied("invalid package")
    # Institutional comparison requires explicit product identity in the
    # frozen vendor registration, not attribution from a directory name.
    spec=vendor.get("subject_spec") or {}
    name,version=spec.get("name"),spec.get("version")
    if not isinstance(name,str) or not name.strip() or not isinstance(version,str) or not version.strip():
        raise access.AccessDenied("frozen vendor name and version required")
    binding=json.loads((path/"plan_binding.json").read_text(encoding="utf-8"))
    if binding.get("target_name")!=name or binding.get("target_version")!=version or binding.get("family_id")!=family:
        raise access.AccessDenied("candidate identity mismatch")
    return path,digest


def _comparison(root, ids):
    directory=root/"assessment"
    state_file=directory/pw.STATE_FILE
    if not state_file.exists():
        return {"status":"not_configured","families":[],"gaps":["No frozen clinical procurement study."]}
    if directory.is_symlink() or state_file.is_symlink():
        raise access.AccessDenied("invalid procurement state path")
    original=pw.load(directory)
    rebuilt=copy.deepcopy(original)
    rebuilt["vendors"]=[v for v in original["vendors"] if v["vendor_id"] in ids]
    rebuilt["results"]={}
    gaps=[]
    registered={v["vendor_id"] for v in rebuilt["vendors"]}
    for missing in sorted(ids-registered):
        gaps.append("Candidate "+missing+" is not registered in frozen study.")
    families=[]
    with tempfile.TemporaryDirectory(prefix=".comparison-",dir=access._root(root)) as tmp:
        pw.save(tmp,rebuilt)
        for vendor in rebuilt["vendors"]:
            vid=vendor["vendor_id"]
            for family in original["conditions"]["families"]:
                item=original["results"].get(vid,{}).get(family)
                if not item or item.get("ingestion")!="verified_package":
                    gaps.append(vid+" / "+family+": no verified package recorded.")
                    continue
                try:
                    path,digest=_package(root,vendor,family,item)
                    # Ingest in a separate scratch study so a failed post-check
                    # cannot contaminate the main reconstructed comparison.
                    with tempfile.TemporaryDirectory(prefix=".ingest-",dir=access._root(root)) as staging:
                        one=copy.deepcopy(rebuilt)
                        one["results"]={}
                        pw.save(staging,one)
                        ingested=pw.ingest_package(staging,vid,path)
                        result=ingested["results"][vid][family]
                        if result["environment"]["package_digest"]!=digest:
                            raise access.AccessDenied("ingested package differs")
                        _,after=_package(root,vendor,family,item)
                        if after!=digest:
                            raise access.AccessDenied("package changed during ingestion")
                        current=pw.load(tmp)
                        current["results"].setdefault(vid,{})[family]=result
                        pw.save(tmp,current)
                except (OSError,ValueError,RuntimeError,KeyError,TypeError,AttributeError):
                    gaps.append(vid+" / "+family+": missing, changed, misattributed or invalid package; excluded.")
        for family in original["conditions"]["families"]:
            try:
                cmp=pw.compare(tmp,family)
            except pw.ProcurementError:
                families.append({"family_id":family,"status":"not_measured"})
                continue
            # Canonical output includes rates, uncertainty, missingness and
            # comparability. No cells or raw clinical content are returned.
            verified=[]
            current=pw.load(tmp)
            for vendor in current["vendors"]:
                vid=vendor["vendor_id"]
                rec=current["results"].get(vid,{}).get(family)
                if rec is not None:
                    verified.append({"vendor_id":vid,"blinded_label":vendor["blinded_label"],
                        "target_name":vendor["subject_spec"]["name"],
                        "target_version":vendor["subject_spec"]["version"],
                        "manifest_hash":rec["environment"]["package_digest"]})
            families.append({"family_id":family,"status":"exploratory",
                "verified_packages":verified,"comparison":cmp})
    if pw.load(directory)!=original:
        raise access.AccessDenied("frozen study changed during reconstruction")
    return {"status":"configured","conditions_hash":original["conditions_hash"],
            "families":families,"gaps":gaps}


def overview(directory,actor):
    with access.db(directory) as conn:
        _actor(conn,actor)
    root=Path(directory).resolve()
    source=buyer.load(root)
    state=buyer.status(root)
    from . import vendor_document_review
    source_reviews=vendor_document_review.committee_inventory(root,actor)
    vendors=[]
    for v in state["vendors"]:
        docs=[{k:d[k] for k in ("evidence_id","kind","title","sha256","integrity","review_status")} for d in v["documents"]]
        for doc in docs:
            doc.update(source_reviews.get(doc["evidence_id"],{"source_review_status":"unavailable","document_freshness":"unavailable","source_review_digest":None,"source_review_count":0}))
        disclosures=[{k:q.get(k) for k in ("evidence_id","integrity","schema_status","product_version_self_reported","counts")} for q in v["questionnaires"]]
        vendors.append({"vendor_id":v["vendor_id"],"name":v["name"],"documents":docs,
                        "missing_evidence_kinds":v["missing_evidence_kinds"],
                        "disclosures_self_reported_unverified":disclosures})
    ids={v["vendor_id"] for v in vendors}
    comparison=_comparison(root,ids)
    from . import institution_dossier
    inventory=institution_dossier.collect(root,actor,ids)
    from . import buyer_risk_protocol
    inventory["buyer_risk_protocols"]=buyer_risk_protocol.committee_inventory(root,actor)
    if buyer.load(root)!=source:
        raise access.AccessDenied("buyer register changed during evidence reconstruction")
    return {"schema":"institution-committee-evidence/1","project_name":state["project_name"],
            "intended_use":state["intended_use"],"missing_intake":state["missing_intake"],
            "vendors":vendors,"clinical_comparison":comparison,"lifecycle_inventory":inventory,
            "decision_grade":False,"limitations":["Document presence and integrity do not establish clinical validity.",
            "Vendor disclosures are self-reported and not independently verified.",
            "Clinical test families are experimental. Missing evidence is not a pass.",
            "No combined score, ranking, automated purchasing or deployment decision."]}


def freeze(directory,actor):
    with access.db(directory) as conn:
        _actor(conn,actor,True)
    payload=overview(directory,actor)
    digest=buyer_plan._digest(payload)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,True)
        old=conn.execute("SELECT payload FROM committee_snapshots WHERE id=?",(digest,)).fetchone()
        if old:
            if json.loads(old["payload"])!=payload:
                raise access.AccessDenied("snapshot digest collision")
        else:
            if conn.execute("SELECT count(*) FROM committee_snapshots").fetchone()[0]>=MAX_SNAPSHOTS:
                raise access.AccessDenied("committee snapshot limit reached")
            conn.execute("INSERT INTO committee_snapshots(id,payload,created_by) VALUES(?,?,?)",
                         (digest,json.dumps(payload,ensure_ascii=False),actor["user_id"]))
        access._audit(conn,actor["user_id"],"committee-snapshot-frozen",digest)
    return {"snapshot_id":digest,"decision_grade":False}


def snapshot(directory,actor,snapshot_id):
    manual._identifier(snapshot_id,digest=True)
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor)
        row=conn.execute("SELECT * FROM committee_snapshots WHERE id=?",(snapshot_id,)).fetchone()
        if not row:
            raise access.AccessDenied("snapshot not found")
        payload=json.loads(row["payload"])
        if buyer_plan._digest(payload)!=snapshot_id:
            raise access.AccessDenied("snapshot integrity mismatch")
        dispositions=[dict(r) for r in conn.execute("SELECT * FROM committee_dispositions WHERE snapshot_id=? ORDER BY created_at,id",(snapshot_id,))]
        for d in dispositions:
            material={k:d[k] for k in ("snapshot_id","actor","disposition","rationale")}
            if buyer_plan._digest(material)!=d["id"]:
                raise access.AccessDenied("committee disposition integrity mismatch")
        access._audit(conn,actor["user_id"],"committee-snapshot-opened",snapshot_id)
    current=overview(directory,actor)
    return {"snapshot_id":snapshot_id,"payload":payload,"current":buyer_plan._digest(current)==snapshot_id,
            "dispositions":dispositions,"decision_grade":False}


def list_snapshots(directory,actor):
    with access.db(directory) as conn:
        _schema(conn)
        _actor(conn,actor)
        return [dict(r) for r in conn.execute("SELECT id,created_at FROM committee_snapshots ORDER BY created_at DESC,id LIMIT ?",(MAX_SNAPSHOTS,))]


def disposition(directory,actor,snapshot_id,kind,rationale):
    access.require(actor,"admin")
    if kind not in DISPOSITIONS or not isinstance(rationale,str) or not rationale.strip() or len(rationale)>4000:
        raise access.AccessDenied("valid disposition and explicit rationale required")
    current=snapshot(directory,actor,snapshot_id)
    if not current["current"]:
        raise access.AccessDenied("snapshot is stale; freeze current evidence")
    material={"snapshot_id":snapshot_id,"actor":actor["user_id"],"disposition":kind,"rationale":rationale.strip()}
    digest=buyer_plan._digest(material)
    with access.db(directory) as conn:
        _schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,True)
        conn.execute("INSERT OR IGNORE INTO committee_dispositions(id,snapshot_id,actor,disposition,rationale) VALUES(?,?,?,?,?)",
                     (digest,snapshot_id,actor["user_id"],kind,rationale.strip()))
        access._audit(conn,actor["user_id"],"committee-disposition-recorded",snapshot_id)
    return {"id":digest,"decision_grade":False,"deployment_authorized":False}
