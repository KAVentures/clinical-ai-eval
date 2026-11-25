"""Receipt-bound preparation of an assessment from an approved collection.

No network calls, evaluator execution or evidence promotion. Complete collected
responses enter the existing canonical manual-assessment pipeline. Preview is
read-only; approval binds the full candidate collection and prepared inputs.
"""
from pathlib import Path

from . import (buyer_agent as buyer,buyer_connector as connector,buyer_assessment as assessment,
               buyer_manual as manual,buyer_plan, institution_access as access,
               buyer_risk_activation as activation,procurement_workflow as procurement)


SCHEMA="collection-assessment-handoff/1"


def _fail(message):
    raise buyer.BuyerError(message)


def _actor(conn,actor,*,admin=False):
    if actor is None:
        return
    access.require(actor,"admin" if admin else "admin",*(() if admin else ("operator",)))
    u=conn.execute("SELECT role,enabled FROM users WHERE id=?",(actor["user_id"],)).fetchone()
    if not u or not u["enabled"] or u["role"]!=actor["role"]:
        raise access.AccessDenied("active institutional account required")


def _conditions(directory,conn,requested):
    """A locally activated procurement study cannot be bypassed in this flow."""
    root=Path(directory).resolve()
    dest=root/"assessment"
    path=dest/procurement.STATE_FILE
    if dest.is_symlink() or path.is_symlink():
        _fail("invalid procurement path")
    state=procurement.load(dest) if path.exists() else None
    binding=activation.approval(conn,state)
    if binding is None:
        if requested is not None:
            _fail("no approved risk-bound conditions")
    elif requested!=state["conditions_hash"]:
        _fail("this workspace requires its exact activated procurement conditions")
    return requested


def _material(directory,conn,collection_id,panel_id,governance,conditions_hash):
    conditions_hash=_conditions(directory,conn,conditions_hash)
    row=connector.status(directory,collection_id)
    if row["state"]!="complete_unscored":
        _fail("verified complete collection required; missing responses are not a pass")
    receipt=row["receipt"]
    binding=row["binding"]
    if (receipt["n_expected"]!=binding["n_expected"] or receipt["n_provided"]!=binding["n_expected"]
            or receipt["n_attempted"]!=binding["n_expected"] or receipt["n_failed"]!=0):
        _fail("collection does not cover the exact prompt inventory")
    # Historical receipt integrity alone does not authorize a new preparation.
    # Re-read the current staged connector and scientific exchange.
    current,_,_=connector._binding(directory,binding["exchange_id"],binding["connector_id"])
    if current!=binding:
        _fail("approved connector or exchange changed")
    record,_=assessment._material(directory,receipt["submission_id"],panel_id,governance,
                                  conditions_hash=conditions_hash,conn=conn)
    if record["exchange_id"]!=binding["exchange_id"] or record["n_responses"]!=binding["n_expected"]:
        _fail("prepared assessment does not match collection")
    payload={"schema":SCHEMA,"collection_id":collection_id,"collection_binding_hash":row["binding_hash"],
             "collection_receipt_hash":receipt["receipt_hash"],"submission_id":receipt["submission_id"],
             "vendor_id":binding["vendor_id"],"target_name":binding["target_name"],
             "target_version":binding["target_version"],"assessment_id":record["assessment_id"],
             "panel_id":panel_id,"governance":record["governance"],"conditions_hash":conditions_hash,
             "mode":record["mode"],"n_responses":record["n_responses"],
             "network_authorized":False,"decision_grade":False}
    return {**payload,"digest":buyer_plan._digest(payload)}


def preview(directory,collection_id,panel_id,governance,conditions_hash=None,*,actor=None):
    with access.db(directory) as conn:
        _actor(conn,actor)
        return _material(directory,conn,collection_id,panel_id,governance,conditions_hash)


def prepare(directory,collection_id,panel_id,governance,conditions_hash,approved_digest,*,confirmed=False,actor=None):
    if confirmed is not True:
        _fail("explicit preparation approval required")
    manual._identifier(approved_digest,digest=True)
    with access.db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _actor(conn,actor,admin=True)
        doc=_material(directory,conn,collection_id,panel_id,governance,conditions_hash)
        if doc["digest"]!=approved_digest:
            _fail("collection or assessment inputs changed; preview again")
        record=assessment.prepare(directory,doc["submission_id"],panel_id,doc["governance"],
                                  conditions_hash=conditions_hash,expected_assessment_id=doc["assessment_id"],conn=conn)
        if _material(directory,conn,collection_id,panel_id,governance,conditions_hash)!=doc:
            _fail("collection changed during preparation")
        # Idempotent preparation and local approval audit, no automatic job.
        audit_id=doc["digest"]+":"+record["assessment_id"]
        if not conn.execute("SELECT 1 FROM audit WHERE action='collection-assessment-prepared' AND object_id=? AND outcome='ok'",
                            (audit_id,)).fetchone():
            access._audit(conn,actor["user_id"] if actor is not None else "local-admin",
                          "collection-assessment-prepared",audit_id)
        return {"handoff":doc,"assessment_id":record["assessment_id"],"status":"prepared_not_executed",
                "decision_grade":False,"network_authorized":False}
