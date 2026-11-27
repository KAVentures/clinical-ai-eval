"""Buyer-scope research evidence coverage view (not a procurement gate)."""
from . import research_buyer_coverage as coverage, institution_access as access


def render(directory, actor, plan_id):
    from .institution_web import page, esc
    doc=coverage.coverage(directory,actor,plan_id)
    target=doc["target"]
    body="<p><a href='/setup'>Assessment setup</a> · <a href='/research'>Research studio</a></p>"
    body+="<p>"+esc(target["name"])+" · version "+esc(target["version"])+"</p>"
    body+="<p>Saved plan: "+esc(plan_id)+"</p>"
    body+="<p><a href='/research-programs?plan_id="+esc(plan_id)+"'>Coordinate a frozen multi-module assessment programme</a></p>"
    body+="<p>Experimental related evidence only. No canonical execution, clinical approval or procurement gate is satisfied by this view. Visibility: "+esc(doc["scope_visibility"])+"</p>"
    body+="<table><thead><tr><th>Required suite</th><th>Canonical availability</th><th>Related studies</th><th>Verified experimental reports</th></tr></thead><tbody>"
    for row in doc["suites"]:
        body+="<tr><td>"+esc(row["suite"])+"</td><td>"+esc(row["canonical_status"])+"</td><td>"+str(len(row["related_studies"]))+"</td><td>"+str(row["verified_experimental_reports"])+" ("+str(row["non_synthetic_experimental_reports"])+" non-synthetic)</td></tr>"
    body+="</tbody></table>"
    for row in doc["studies"]:
        body+="<section><h3>"+esc(row["study_id"])+"</h3><p>"+esc(row["evidence_state"])+"</p>"
        if row["evidence_state"]!="unavailable":
            body+="<p>"+esc(", ".join(row["related_suites"]))+" · "+("synthetic" if row["synthetic"] else "declared non-synthetic")+" · "+("prospective association" if row["prospective_link"] else "retrospective association")+"</p>"
            if row["report_hash"]:
                body+="<p>Verified research report: "+esc(row["report_hash"])+"</p>"
                body+="<p><a href='/research-download?kind=report&amp;id="+esc(row["study_id"])+"'>Download report</a></p>"
        body+="</section>"
    body+="<h3>Limitations</h3><ul>"+"".join("<li>"+esc(x)+"</li>" for x in doc["limitations"])+"</ul>"
    return page("Buyer plan research coverage",body)


def link(directory,actor,form):
    if set(form)!={"study","plan_id","confirm"}:
        raise access.AccessDenied("invalid research association form")
    coverage.link(directory,actor,form["study"],form["plan_id"],confirmed=form["confirm"]=="yes")
    return form["plan_id"]
