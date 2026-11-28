"""Stopped-service integrity preflight for institutional lifecycle records.

Checks local storage and native audit inventories without creating tables,
opening review packets, running models, or changing evidence. A successful
preflight is neither a full evidence replay nor clinical/deployment approval.
"""
import sqlite3
from contextlib import closing
from . import institution_backup as backup, buyer_agent as buyer
from . import institution_access as access


def _groups():
    from . import unified_assessment as u, unified_successor as successor
    from . import institution_imaging as imaging, institution_imaging_reference as reference
    from . import institution_imaging_transport as transport, institution_rag as rag
    from . import institution_rag_grounding as grounding, institution_rag_collection as collection
    from . import institution_rag_comparison as comparison, institution_matched_regression as matched
    from . import institution_hospital_referral as referral, institution_lifecycle_monitor as monitor
    from . import monitor_ingestion as ingestion, monitor_reassessment as handoff
    programme=("unified_assessment_programmes","unified_assessment_links")
    monitoring=("lifecycle_monitors","lifecycle_monitor_events")
    return (
        ("unified_programmes",programme,(u.FREEZE,u.LINK),programme,u._inventory),
        ("version_successors",("unified_assessment_successors",),(successor.EVENT,),
         programme+("unified_assessment_successors",),successor._inventory),
        ("diagnostic_imaging",("diagnostic_imaging_studies","diagnostic_imaging_submissions",
            "diagnostic_imaging_seals","imaging_reference_protocols","imaging_reference_ratings",
            "imaging_reference_links","diagnostic_imaging_collection_receipts"),
         (imaging.FROZEN,imaging.SUBMITTED,imaging.SEALED,reference.PROTOCOL,reference.RATING,
          reference.LINK,transport.EVENT),(),imaging._inventory),
        ("rag_rehearsal",("institution_rag_studies","institution_rag_reports"),
         (rag.FROZEN,rag.LOCKED),(),rag._inventory),
        ("rag_grounding",("rag_grounding_studies","rag_grounding_traces","rag_grounding_reviews",
            "rag_grounding_seals","rag_grounding_collection_receipts","rag_paired_analysis_plans"),
         (grounding.FROZEN,grounding.TRACE,grounding.REVIEW,grounding.SEAL,collection.EVENT,
          comparison.EVENT),(),grounding._inventory),
        ("matched_version_regression",("matched_version_regressions",),(matched.EVENT,),(),matched.inventory),
        ("hospital_referral",("hospital_referral_policies","hospital_referral_snapshots",
            "hospital_referral_ratings","hospital_referral_decisions"),
         (referral.POLICY,referral.SNAPSHOT,referral.RATING,referral.DECISION),programme,referral._inventory),
        ("lifecycle_monitoring",monitoring,(monitor.FROZEN,monitor.EVENT),programme,monitor._inventory),
        ("monitor_ingestion",("lifecycle_ingestion_grants","lifecycle_ingestion_requests"),
         (ingestion.ISSUE,ingestion.REVOKE,ingestion.USE),programme+monitoring,ingestion._inventory),
        ("monitor_version_handoffs",("monitor_version_handoffs",),(handoff.EVENT,),
         programme+monitoring+("unified_assessment_successors",),handoff._inventory),
    )


def _check_groups(conn,tables):
    results=[]
    for name,owned,events,dependencies,verify in _groups():
        present=set(owned)&tables
        placeholders=",".join("?" for _ in events)
        event_count=conn.execute("SELECT count(*) FROM audit WHERE outcome='ok' AND action IN ("+
                                 placeholders+")",events).fetchone()[0]
        if not present and not event_count:
            state="not_initialized"
        elif not (set(owned)|set(dependencies))<=tables:
            state="incomplete_schema"
        else:
            try:
                verify(conn)
                state="inventory_verified"
            except (sqlite3.Error,ValueError,RuntimeError,KeyError,TypeError,IndexError,
                    AttributeError,StopIteration,RecursionError):
                state="inventory_mismatch_or_unavailable"
        results.append({"ledger":name,"state":state,"audit_events":event_count})
    return results


def _count(conn,tables,table,predicate="1=1"):
    # All identifiers and predicates are fixed by callers below.
    if table not in tables:
        return 0
    return conn.execute("SELECT count(*) FROM "+table+" WHERE "+predicate).fetchone()[0]


def check(directory,*,services_stopped=False,expect_restored=False):
    if services_stopped is not True or type(expect_restored) is not bool:
        raise access.AccessDenied("explicit stopped-service confirmation required")
    root=backup._path(directory)
    buyer.load(root)
    before=backup._inventory(root)
    backup._sqlite_check(root)
    path=root/"institution"/"access.sqlite3"
    if not path.is_file() or path.is_symlink():
        raise access.AccessDenied("institutional access database is missing")
    try:
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=1)) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"users","sessions","audit"}<=tables:
                raise access.AccessDenied("institutional access schema is incomplete")
            foreign_keys_ok=conn.execute("PRAGMA foreign_key_check").fetchone() is None
            ledgers=_check_groups(conn,tables)
            authorization={
                "stored_sessions":_count(conn,tables,"sessions"),
                "unrevoked_agent_grants":_count(conn,tables,"agent_grants","revoked=0"),
                "unrevoked_evaluator_grants":_count(conn,tables,"evaluator_service_grants","revoked=0"),
                "unrevoked_monitor_ingestion_grants":_count(conn,tables,"lifecycle_ingestion_grants","revoked_seq IS NULL"),
                "enabled_monitoring_schedules":_count(conn,tables,"monitoring_schedules","enabled=1"),
            }
            # Roll back the read transaction explicitly; never write to this DB.
            conn.rollback()
    except sqlite3.Error as exc:
        raise access.AccessDenied("institutional lifecycle database could not be checked") from exc
    after=backup._inventory(root)
    if before!=after:
        raise access.AccessDenied("workspace inventory changed during preflight")
    restored_ok=not any(authorization.values()) if expect_restored else None
    ok=foreign_keys_ok and restored_ok is not False and all(
        r["state"] in ("inventory_verified","not_initialized") for r in ledgers)
    return {"schema":"institution-lifecycle-preflight/1","ok":ok,
            "storage_integrity_checked":True,"foreign_keys_ok":foreign_keys_ok,
            "ledgers":ledgers,"authorization_counts":authorization,
            "expect_restored":expect_restored,"restored_authorization_cleared":restored_ok,
            "source_payloads_replayed":False,"decision_grade":False,"deployment_authorized":False}
