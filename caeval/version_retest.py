"""Prepare a new-version, fixed-science retest from a verified baseline.

Preview is read-only. Approval saves only the canonical child plan and exchange.
An authorized administrator separately freezes the monitoring schedule and a
trusted worker separately authorizes transmission and judge calls.
"""
from . import (buyer_agent as buyer,buyer_plan,buyer_manual as manual,
               buyer_connector as connector,institution_access as access,
               institution_monitoring as monitor,buyer_assessment as assessment)

EVENT="version-reassessment-prepared"


def _material(directory,baseline_job,new_version,connector_id):
    base,bound,old,cfg=monitor._baseline(directory,baseline_job)
    old_plan=buyer_plan.load(directory,old["plan_id"])
    version=buyer_plan._text(new_version,"new version",300)
    if version==old["target"]["version"]:
        raise buyer.BuyerError("choose a distinct version for reassessment")
    p=old_plan["plan"]
    spec={"vendor_id":old["vendor_id"],"target_name":old["target"]["name"],
          "target_version":version,"profiles":p["target_profile"]["types"],"answers":p["intake"]}
    child=buyer_plan.preview(directory,spec,save=False)
    # The old exchange was just reconstructed and verified by _baseline.
    # A version-only change must preserve its entire scientific inventory.
    new={**old,"plan_id":child["plan_id"],"target":child["plan"]["target"]}
    new.pop("exchange_id")
    new["exchange_id"]=buyer_plan._digest(new)
    binding,_,_=connector._binding_from_record(directory,new,connector_id)
    raw,_=assessment._panel(directory,bound["panel_id"])
    if assessment._sha(raw)!=bound["file_sha256"]["panel.toml"]:
        raise buyer.BuyerError("baseline judge panel changed")
    conditions=bound.get("conditions_hash")
    if conditions is not None:
        # A new version is not admitted to a frozen procurement roster just
        # because it is the same brand. It must be separately included.
        from . import buyer_risk_activation as activation
        if activation.check(directory,child["plan_id"],old["pack_content_hash"],bound["panel_id"],raw)!=conditions:
            raise buyer.BuyerError("new version is not approved under baseline conditions")
    payload={"schema":"version-reassessment-preview/1","baseline_job":baseline_job,
             "baseline_assessment":base["assessment_id"],
             "baseline_packages":{p["family_id"]:p["manifest_hash"] for p in base["result"]["packages"]},
             "vendor_id":old["vendor_id"],"target_name":old["target"]["name"],
             "baseline_version":old["target"]["version"],"target_version":version,
             "plan_id":child["plan_id"],"exchange_id":new["exchange_id"],
             "connector_id":connector_id,"connector_binding_hash":buyer_plan._digest(binding),
             "panel_id":bound["panel_id"],"panel_hash":assessment._sha(raw),
             "pack_id":old["pack_id"],"pack_content_hash":old["pack_content_hash"],
             "families":old["family_ids"],"n_prompts":len(old["slots"]),
             "governance":bound["governance"],"conditions_hash":conditions,
             "required_but_not_run":child["plan"]["selection"]["required_but_not_run"],
             "network_authorized":False,"decision_grade":False}
    return spec,{**payload,"digest":buyer_plan._digest(payload)}


def preview(directory,actor,baseline_job,new_version,connector_id):
    with access.db(directory) as conn:
        monitor._account(conn,actor,("admin",))
    return _material(directory,baseline_job,new_version,connector_id)[1]


def prepare(directory,actor,baseline_job,new_version,connector_id,digest,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit version reassessment approval required")
    # Reconstruct before and after publication without holding the access
    # store across operations which may independently read procurement state.
    spec,doc=_material(directory,baseline_job,new_version,connector_id)
    if doc["digest"]!=digest:
        raise access.AccessDenied("version reassessment changed since preview")
    with access.db(directory) as conn:
        monitor._account(conn,actor,("admin",))
    child=buyer_plan.preview(directory,spec)
    exchange=manual.prepare(directory,child["plan_id"],doc["pack_id"])
    if exchange["request"]["exchange_id"]!=doc["exchange_id"]:
        raise access.AccessDenied("prepared exchange differs from approved preview")
    # Reuse the actual monitoring preflight; no scheduling or candidate call.
    material=monitor._material(directory,baseline_job,doc["exchange_id"],connector_id,doc["panel_id"],doc["governance"])
    if (material["baseline_packages"]!=doc["baseline_packages"] or
            buyer_plan._digest(material["connector_binding"])!=doc["connector_binding_hash"] or
            material.get("conditions_hash")!=doc["conditions_hash"] or
            _material(directory,baseline_job,new_version,connector_id)[1]!=doc):
        raise access.AccessDenied("reassessment source changed during preparation")
    with access.db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        monitor._account(conn,actor,("admin",))
        prior=conn.execute("SELECT seq FROM audit WHERE action=? AND object_id=? AND outcome='ok'",(EVENT,digest)).fetchall()
        if len(prior)>1:
            raise access.AccessDenied("duplicate version preparation audit")
        if not prior:
            access._audit(conn,actor["user_id"],EVENT,digest)
    return doc
