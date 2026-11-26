"""Role-scoped procurement and frozen committee evidence views."""
from __future__ import annotations

from . import institution_committee as committee


def _inventory(data):
    from .institution_web import esc
    if data is None:
        return "<section><h2>Lifecycle inventory</h2><p>Not recorded in this historical snapshot.</p></section>"
    body="<section><h2>Evaluation and implementation inventory</h2><p>"+esc(data["interpretation"])+"</p>"
    for row in data["assessments"]:
        body+="<h3>"+esc(row["id"])+"</h3><p>"+esc(row["kind"])+" · "+esc(row["status"])+" · Candidate "+esc(row["vendor_id"] or "unavailable")+" · Version "+esc(row["target_version"] or "unavailable")+" · "+esc(row["mode"])+"</p>"
        for p in row["packages"]:
            body+="<p>"+esc(p["family_id"])+" · claim "+esc(p["claim"])+" · manifest "+esc(p["manifest_hash"])+"</p>"
        for gap in row["required_but_not_run"]:
            body+="<p>Required but not run: "+esc(gap["suite"])+"</p>"
    for row in data["reviews"]:
        body+="<h3>"+esc(row["kind"])+"</h3><p>"+esc(row["id"])+" · "+esc(row.get("job") or row.get("pack_content_hash") or "source unavailable")+" · "+esc(row["state"])+"</p>"
        if "assigned" in row:
            body+="<p>Attested "+str(row["attested"])+"/"+str(row["assigned"])+" · Submitted "+str(row["submitted"])+"/"+str(row["assigned"])+"</p>"
    for pilot in data["pilots"]:
        body+="<h3>Pilot "+esc(pilot["id"])+"</h3><p>"+esc(pilot["vendor_id"])+" · "+esc(pilot["product_version"])+" · "+esc(pilot["state"])+" · Open blocking items: "+str(pilot["blocking_open"])+"</p>"
        for item in pilot["open_items"]:
            body+="<p>"+esc(item["kind"])+" · "+esc(item["severity"])+" · Due "+esc(item["due"])+"</p>"
        for item in pilot["evidence"]:
            body+="<p>Linked "+esc(item["purpose"])+" "+esc(item["job_id"])+" · "+esc(item["integrity"])+"</p>"
    for s in data["schedules"]:
        body+="<h3>Regression schedule "+esc(s["id"])+"</h3><p>Pilot "+esc(s["pilot_id"])+" · "+("enabled" if s["enabled"] else "disabled")+"</p>"
        for occ in s["occurrences"]:
            body+="<p>"+esc(occ["id"])+" · "+esc(occ["state"])+" · Missed intervals "+str(occ["skipped_intervals"])+"</p>"
            for family,metrics in occ.get("metric_states",{}).items():
                body+="<p>"+esc(family)+": "+esc(", ".join(k+"="+v for k,v in metrics.items()))+"</p>"
    if not any(data[k] for k in ("assessments","reviews","pilots","schedules")):
        body+="<p>No lifecycle evidence recorded.</p>"
    return body+"</section>"


def _evidence(payload):
    from .institution_web import esc
    body="<p>Intended use: "+esc(payload["intended_use"])+"</p>"
    body+="<p>Missing intake: "+esc(", ".join(payload["missing_intake"]) or "None recorded")+"</p>"
    for v in payload["vendors"]:
        body+="<section><h2>"+esc(v["name"])+"</h2><p>Candidate ID: "+esc(v["vendor_id"])+"</p>"
        body+="<p>Missing evidence: "+esc(", ".join(v["missing_evidence_kinds"]) or "None recorded")+"</p>"
        body+="<table><tr><th>Document</th><th>Kind</th><th>Integrity</th><th>Review</th></tr>"
        for d in v["documents"]:
            body+="<tr>"+"".join("<td>"+esc(d[k])+"</td>" for k in ("title","kind","integrity","review_status"))+"</tr>"
        body+="</table><h3>Self-reported vendor disclosures (unverified)</h3>"
        for d in v["disclosures_self_reported_unverified"]:
            body+="<p>"+esc(d.get("product_version_self_reported"))+" · "+esc(d.get("integrity"))+" · "+esc(d.get("schema_status"))+"</p>"
        body+="</section>"
    comp=payload["clinical_comparison"]
    body+="<section><h2>Frozen scientific comparison</h2><p>"+esc(comp["status"])+"</p>"
    if comp.get("conditions_hash"):
        body+="<p>Conditions: "+esc(comp["conditions_hash"])+"</p>"
    for gap in comp["gaps"]:
        body+="<p>"+esc(gap)+"</p>"
    for family in comp["families"]:
        body+="<h3>"+esc(family["family_id"])+"</h3>"
        if family["status"]=="not_measured":
            body+="<p>Not measured.</p>"
            continue
        for p in family["verified_packages"]:
            body+="<p>"+esc(p["blinded_label"])+" — "+esc(p["target_name"])+" "+esc(p["target_version"])+" · manifest "+esc(p["manifest_hash"])+"</p>"
        result=family["comparison"]
        body+="<p>Comparability: "+esc(result["comparability"]["status"])+" — "+esc(result["comparability"]["note"])+"</p>"
        body+="<p>"+esc(result["claim_authority"]["reason"])+"</p>"
        for hazard in result["per_hazard"]:
            body+="<h4>"+esc(hazard["hazard_id"])+" — "+esc(hazard["description"])+"</h4>"
            crit=hazard["acceptance_criterion"]
            body+="<p>Predeclared criterion: "+esc(crit["metric"])+" "+esc(crit["operator"])+" "+esc(crit["threshold"])+"</p>"
            body+="<table><tr><th>Product</th><th>Rate</th><th>95% CI</th><th>Observed / eligible</th><th>Status</th></tr>"
            for row in hazard["products"]:
                rate="Not estimable" if row["rate"] is None else f'{row["rate"]:.1%}'
                ci="Not estimable" if not row.get("ci") else f'[{row["ci"][0]:.1%}, {row["ci"][1]:.1%}]'
                # Canonical comparator records missing cells separately. Do
                # not present n/n when eligible cells were not all observed.
                total=row.get("n_total")
                if total is None and "n_missing" in row:
                    total=row["n"]+row["n_missing"]
                coverage=str(row["n"])+"/"+(str(total) if total is not None else "unknown")
                values=(row["product_id"],rate,ci,coverage,row["status"])
                body+="<tr>"+"".join("<td>"+esc(value)+"</td>" for value in values)+"</tr>"
            body+="</table>"
        if result["vendors_without_results"]:
            body+="<p>Missing vendor results: "+esc(", ".join(result["vendors_without_results"]))+"</p>"
    body+="</section><section><h2>Limitations</h2><ul>"+"".join("<li>"+esc(x)+"</li>" for x in payload["limitations"])+"</ul></section>"
    return body+_inventory(payload.get("lifecycle_inventory"))


def render(directory,actor):
    from .institution_web import esc,page,_hidden
    payload=committee.overview(directory,actor)
    body="<p><a href='/'>Workspace</a></p><p>Committee evidence, not a purchase recommendation.</p>"
    if actor["role"] in ("admin","operator"):
        body+="<form action='/committee-freeze' method='post'>"+_hidden("csrf",actor["csrf"])+"<button>Freeze current evidence snapshot</button></form>"
    body+="<h2>Saved snapshots</h2>"
    for row in committee.list_snapshots(directory,actor):
        body+="<p><a href='/committee-snapshot?id="+esc(row["id"])+"'>"+esc(row["created_at"])+" · "+esc(row["id"][:12])+"</a></p>"
    return page("Institutional procurement committee",body+_evidence(payload))


def render_snapshot(directory,actor,identifier):
    from .institution_web import esc,page,_hidden
    saved=committee.snapshot(directory,actor,identifier)
    body="<p><a href='/committee'>Current evidence</a></p><p>Snapshot "+esc(identifier)+"</p>"
    body+="<p>"+("Matches current evidence." if saved["current"] else "Historical snapshot — current evidence has changed.")+"</p>"
    body+="<p><a href='/committee-export?id="+esc(identifier)+"'>Download frozen evidence JSON</a></p>"
    for row in saved["dispositions"]:
        body+="<section><p>"+esc(row["created_at"])+" · "+esc(row["disposition"])+"</p><p>"+esc(row["rationale"])+"</p></section>"
    if saved["current"] and actor["role"]=="admin":
        body+="<form action='/committee-disposition' method='post'>"+_hidden("csrf",actor["csrf"])+_hidden("snapshot_id",identifier)
        body+="<label>Administrative disposition<select name='kind'>"+"".join("<option>"+esc(k)+"</option>" for k in committee.DISPOSITIONS)+"</select></label>"
        body+="<label>Rationale<textarea name='rationale' required maxlength='4000'></textarea></label><button>Record disposition</button></form>"
    return page("Frozen committee evidence",body+_evidence(saved["payload"]))
