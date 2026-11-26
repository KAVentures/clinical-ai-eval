"""Frozen collection-to-assessment handoff, no model calls or scoring.

The existing canonical prepared assessment remains authoritative. The receipt
binds the collection provenance separately; it does not attest clinical truth
or the actual model identity. Repeating an identical approved handoff is safe.
"""
from pathlib import Path

from . import (buyer_connector as connector,buyer_assessment as assessment,
               buyer_plan,buyer_agent as buyer,buyer_manual as manual,
               institution_access as access,institution_setup as setup,
               buyer_risk_activation as activation,procurement_workflow as procurement)


def _fail(message):
    raise buyer.BuyerError(message)


def _actor(conn,actor,*,admin=False):
    # None is reserved for the trusted local CLI, not a remote browser request.
    if actor is not None:
        setup._actor(conn,actor)
        if admin:
            access.require(actor,"admin")


def _material(directory,collection_id,panel_id,governance,conditions_hash,conn):
    dest=Path(directory).resolve()/"assessment"
    path=dest/procurement.STATE_FILE
    if dest.is_symlink() or path.is_symlink():
        _fail("invalid procurement path")
    state=procurement.load(dest) if path.exists() else None
    binding=activation.approval(conn,state)
    if binding is None and conditions_hash is not None:
        _fail("no approved risk-bound conditions")
    if binding is not None and conditions_hash!=state["conditions_hash"]:
        _fail("exact activated procurement conditions required")
    row=connector.status(directory,collection_id)
    if row["state"]!="complete_unscored":
        _fail("complete verified collection required")
    receipt=row["receipt"]
    binding=row["binding"]
    if (receipt["n_expected"]!=binding["n_expected"] or
            receipt["n_provided"]!=binding["n_expected"] or
            receipt["n_attempted"]!=binding["n_expected"] or receipt["n_failed"]!=0):
        _fail("collection coverage differs from exact exchange")
    current,_,_=connector._binding(directory,binding["exchange_id"],binding["connector_id"])
    if current!=binding:
        _fail("approved connector or exchange changed")
    exchange=manual._load(directory,binding["exchange_id"])
    if (binding["vendor_id"]!=exchange["vendor_id"] or
            binding["target_name"]!=exchange["target"]["name"] or
            binding["target_version"]!=exchange["target"]["version"] or
            len(exchange["slots"])!=binding["n_expected"]):
        _fail("collection target or inventory differs from source")
    record,_=assessment._material(directory,receipt["submission_id"],panel_id,governance,
                                  conditions_hash=conditions_hash,conn=conn)
    if record["exchange_id"]!=binding["exchange_id"] or record["n_responses"]!=binding["n_expected"]:
        _fail("assessment differs from verified collection")
    material={"schema":"collection-assessment-handoff/1","collection_id":collection_id,
              "collection_binding_hash":row["binding_hash"],"collection_receipt_hash":receipt["receipt_hash"],
              "vendor_id":binding["vendor_id"],"target":{"name":binding["target_name"],"version":binding["target_version"]},
              "assessment_id":record["assessment_id"],"submission_id":receipt["submission_id"],
              "panel_id":panel_id,"governance":record["governance"],"conditions_hash":conditions_hash,
              "n_responses":record["n_responses"],"mode":record["mode"],
              "network_authorized":False,"decision_grade":False}
    return {**material,"digest":buyer_plan._digest(material)}


def preview(directory,actor,collection_id,panel_id,governance,*,conditions_hash=None):
    with access.db(directory) as conn:
        _actor(conn,actor)
        return _material(directory,collection_id,panel_id,governance,conditions_hash,conn)


def prepare(directory,actor,collection_id,panel_id,governance,approved_digest,*,conditions_hash=None,confirmed=False):
    if confirmed is not True:
        _fail("explicit collection handoff approval required")
    manual._identifier(approved_digest,digest=True)
    with access.db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,admin=True)
        doc=_material(directory,collection_id,panel_id,governance,conditions_hash,conn)
        if approved_digest!=doc["digest"]:
            _fail("handoff changed; approve exact current preview")
        prepared=assessment.prepare(directory,doc["submission_id"],panel_id,governance,
                   conditions_hash=conditions_hash,conn=conn,expected_id=doc["assessment_id"])
        if prepared["assessment_id"]!=doc["assessment_id"] or _material(directory,collection_id,panel_id,governance,conditions_hash,conn)!=doc:
            _fail("handoff inputs changed during preparation")
        # The canonical prepared record is content-addressed. Do not overwrite
        # it or silently add a clinical claim. Audit the exact handoff once.
        event=doc["digest"]+":"+doc["assessment_id"]
        prior=conn.execute("SELECT seq FROM audit WHERE action='collection-assessment-prepared' AND object_id=? AND outcome='ok'",(event,)).fetchall()
        if len(prior)>1:
            _fail("duplicate handoff audit")
        if not prior:
            access._audit(conn,actor["user_id"] if actor is not None else "local-admin","collection-assessment-prepared",event)
        return doc
