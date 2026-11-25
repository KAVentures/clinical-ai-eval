"""Read-only browser cockpit for the institutional evaluation lifecycle."""
from . import institution_lifecycle_cockpit as cockpit, institution_access as access


LABELS={
    "programme_scope_stale":"Reassess stale programme scope",
    "canonical_suite_gaps":"Review uncovered required families",
    "locked_evidence_requires_investigation":"Investigate changed or unavailable evidence",
    "associate_verified_sources":"Associate verified evidence",
    "collect_native_evidence":"Collect native evaluation evidence",
    "policy_inventory_unavailable":"Investigate unavailable policy inventory",
    "consider_local_referral_policy":"Consider registering a local referral policy",
    "prepare_policy_evidence_review":"Prepare the frozen gate review",
    "policy_snapshot_stale":"Review stale gate snapshot",
    "await_assigned_gate_reviews":"Follow up assigned gate reviews",
    "review_administrative_disposition":"Review administrative disposition",
    "monitor_inventory_unavailable":"Investigate unavailable monitor inventory",
    "reported_version_change":"Investigate reported version change and reassessment",
    "monitor_attention_required":"Inspect monitoring alerts",
    "missing_reporting_windows":"Investigate missing reporting windows",
    "monitor_schedule_ended":"Review ended monitoring schedule",
    "version_lineage_unavailable":"Investigate unavailable version lineage",
}


def _link(route,title):
    from .institution_web import esc
    return "<a href='"+esc(route)+"'>"+esc(title)+"</a>"


def render(directory,actor,query):
    from .institution_web import page,esc
    if set(query)-{"programme","vendor","attention"} or query.get("attention","all") not in ("all","required"):
        raise access.AccessDenied("invalid cockpit filter")
    query={k:v for k,v in query.items() if v!=""}
    doc=cockpit.overview(directory,actor)
    rows=doc["vendors"]
    for field in ("programme","vendor"):
        if field in query:
            key=field+"_id"
            if query[field] not in {r[key] for r in doc["vendors"]}:
                raise access.AccessDenied("unknown cockpit filter")
            rows=[r for r in rows if r[key]==query[field]]
    if query.get("attention")=="required":
        rows=[r for r in rows if any(t["priority"] in ("high","critical") for t in r["tasks"])]
    body="<p>"+_link("/","Dashboard")+" · "+_link("/unified-assessment","Programmes")+" · "+_link("/committee","Committee")+"</p>"
    body+="<p>Administrative workflow overview. Independent source reads are not a single atomic snapshot. Open native records and reverify before acting. No clinical readiness, score or deployment decision is implied.</p>"
    body+="<h2>Source availability</h2><ul>"
    for name,state in doc["source_availability"].items():
        body+="<li>"+esc(name)+" · "+esc(state)+"</li>"
    body+="</ul>"
    if doc["partial"]:
        body+="<p><strong>Partial inventory: do not interpret omitted records as absent or safe.</strong></p>"
        for name,n in doc["unmatched_record_counts"].items():
            if n:
                body+="<p>"+esc(n)+" unmatched "+esc(name)+" records.</p>"
    body+="<form method='get' action='/lifecycle-cockpit'>"
    for field in ("programme","vendor"):
        body+="<label>"+esc(field)+"<select name='"+field+"'><option value=''>All</option>"
        for item in sorted({r[field+"_id"] for r in doc["vendors"]}):
            body+="<option value='"+esc(item)+"'"+(" selected" if query.get(field)==item else "")+">"+esc(item)+"</option>"
        body+="</select></label>"
    body+="<label>Attention<select name='attention'><option value='all'>All</option><option value='required'"+(" selected" if query.get("attention")=="required" else "")+">High or critical</option></select></label><button>Filter</button></form>"
    body+="<h2>Programme and version work queue</h2><p>"+esc(len(rows))+" vendor scopes shown.</p>"
    for r in rows:
        ident=r["programme_id"]
        body+="<section><h3>"+esc(r["target"]["name"])+" · "+esc(r["target"]["version"])+"</h3><p>"+esc(ident)+" · "+esc(r["vendor_id"])+" · "+esc(r["programme_state"])+"</p>"
        body+="<p>"+_link("/unified-assessment-detail?id="+ident,"Inspect evidence programme")+" · "+_link("/unified-assessment-lineage?id="+ident,"Version lineage")+"</p>"
        body+="<p>"+esc(r["source_counts"]["source_available"])+" / "+esc(len(r["requirements"]))+" planned sources available · "+esc(len(r["canonical_gaps"]))+" uncovered canonical families.</p>"
        body+="<table><tr><th>Requirement</th><th>Kind</th><th>Coverage</th><th>Evidence claim</th></tr>"
        for item in r["requirements"]:
            body+="<tr>"+"".join("<td>"+esc(v)+"</td>" for v in (item["id"],item["kind"],item["state"],item["claim"] or "not linked"))+"</tr>"
        body+="</table>"
        policy=r["policy"]
        if policy:
            body+="<p>Local policy: "+esc(policy["state"])+" · "+esc(policy["n_reviewed"])+" / "+esc(policy["n_gates"])+" ratings · historical disposition "+esc(policy["disposition"] or "none")+" · snapshot current "+esc(policy["snapshot_current"])+"</p>"
        else:
            body+="<p>Local policy: "+("not registered" if doc["source_availability"]["policies"]=="available" else "unavailable")+".</p>"
        mon=r["monitor"]
        if mon:
            body+="<p>Operational monitoring as of "+esc(mon["as_of"])+": "+esc(mon["n_missing_windows"])+" missing windows · "+esc(mon["n_alerts"])+" alerts · reassessment required "+esc(mon["version_reassessment_required"])+"</p>"
        else:
            body+="<p>Operational monitor: "+("not registered (external authorization required)" if doc["source_availability"]["monitors"]=="available" else "unavailable")+".</p>"
        for line in r["programme_lineage"]:
            body+="<p>Version-only lineage "+esc(line["baseline_id"])+" → "+esc(line["successor_id"])+". No evidence transfer.</p>"
        body+="<h4>Suggested next administrative actions</h4>"
        if not r["tasks"]:
            body+="<p>No actions derived from the available inventories. This is not a clinical safety finding.</p>"
        for t in r["tasks"]:
            label=LABELS[t["code"]]
            actionable=actor["role"]=="admin" or actor["role"]==t["role"]
            body+="<p>"+esc(t["priority"])+" · "+(_link(t["route"],label) if actionable else esc(label))+" · "+esc(t["role"])+"</p>"
        body+="</section>"
    return page("Clinical AI lifecycle cockpit",body)
