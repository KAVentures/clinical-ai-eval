"""Guided single-turn vendor onboarding from a saved buyer scope.

No network access, credential input or model execution. Mixed-modality plans
are refused by the existing canonical manual exchange builder. The guided
queue is idempotent for the same exact binding; an intentional repeat must use
the ordinary collection workflow.
"""
from . import (buyer_plan, buyer_manual as manual, buyer_connector as connector,
               buyer_agent as buyer, institution_access as access,
               institution_setup as setup, research_buyer_coverage as bridge,
               executors, candidate_protocol)


def options(directory,actor,plan_id):
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        p=bridge._buyer_plan(directory,plan_id)
        t=p["plan"]["target"]
        selected=p["plan"]["selection"]
        runnable=selected["runnable_suites"]
        single=bool(runnable) and all(executors.resolve(f).executor_id==executors.GENERIC_PAIRED_TEXT for f in runnable)
        matches=[{k:c[k] for k in ("connector_id","protocol")} for c in connector.connectors(directory)
                 if c["vendor_id"]==p["vendor_id"] and c["target_name"]==t["name"] and
                 c["target_version"]==t["version"] and c["protocol"] in ("custom-json",candidate_protocol.PROTOCOL)]
        return {"plan_id":plan_id,"target":{"name":t["name"],"version":t["version"]},
                "vendor_id":p["vendor_id"],"single_turn_supported":single,
                "connectors":matches,"packs":manual.packs(directory),"runnable_suites":runnable,
                "required_but_not_run":selected["required_but_not_run"],"decision_grade":False}


def _material(directory,plan_id,pack_id,connector_id):
    p=bridge._buyer_plan(directory,plan_id)
    record=manual._build(directory,plan_id,pack_id)
    binding,_,_=connector._binding_from_record(directory,record,connector_id)
    doc={"schema":"guided-candidate-collection/1","plan_id":plan_id,
         "exchange_id":record["exchange_id"],"pack_id":pack_id,"pack_content_hash":record["pack_content_hash"],
         "vendor_id":record["vendor_id"],"target":{"name":binding["target_name"],"version":binding["target_version"]},
         "connector_id":connector_id,"collection_binding_hash":buyer_plan._digest(binding),
         "n_expected":binding["n_expected"],"runnable_suites":record["family_ids"],
         "required_but_not_run":p["plan"]["selection"]["required_but_not_run"],
         "network_authorized":False,"decision_grade":False}
    return {**doc,"digest":buyer_plan._digest(doc)}


def preview(directory,actor,plan_id,pack_id,connector_id):
    with access.db(directory) as conn:
        setup._actor(conn,actor)
        return _material(directory,plan_id,pack_id,connector_id)


def approve(directory,actor,plan_id,pack_id,connector_id,digest,*,confirmed=False):
    if confirmed is not True:
        raise access.AccessDenied("explicit guided collection approval required")
    with access.db(directory) as conn:
        conn.execute("BEGIN IMMEDIATE")
        setup._actor(conn,actor)
        material=_material(directory,plan_id,pack_id,connector_id)
        if material["digest"]!=digest:
            raise access.AccessDenied("guided collection changed since preview")
        exchange=manual.prepare(directory,plan_id,pack_id)
        if exchange["request"]["exchange_id"]!=material["exchange_id"]:
            raise access.AccessDenied("exchange changed during preparation")
        row=connector.queue(directory,material["exchange_id"],connector_id,
                            expected_binding=material["collection_binding_hash"],guided=True)
        if row["binding_hash"]!=material["collection_binding_hash"]:
            raise access.AccessDenied("collection binding changed")
        obj=material["digest"]+":"+row["id"]
        events=conn.execute("SELECT seq FROM audit WHERE action='guided-collection-approved' AND object_id=? AND outcome='ok'",(obj,)).fetchall()
        if len(events)>1:
            raise access.AccessDenied("duplicate guided approval audit")
        if not events:
            access._audit(conn,actor["user_id"],"guided-collection-approved",obj)
        return {"collection_id":row["id"],"state":row["state"],"binding_hash":row["binding_hash"],
                "decision_grade":False,"network_authorized":False}
