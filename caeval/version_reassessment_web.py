"""Browser version reassessment planning and exact-scope progress."""
from . import version_reassessment as version, institution_access as access


def _coverage(doc):
    from .institution_web import esc
    return "".join("<p>"+esc(r["suite"])+" · "+esc(r["state"])+" · "+str(len(r["packages"]))+" verified packages</p>" for r in doc["suites"])


def render(directory,actor,baseline_id):
    from .institution_web import page,esc,_hidden
    d=version.history(directory,actor,baseline_id)
    body="<p><a href='/setup'>Assessment setup</a></p><p>"+esc(d["target_name"])+" · baseline "+esc(d["baseline_version"])+"</p>"
    body+="<p>Versions are opaque vendor identifiers, not inferred chronological order. No previous evidence, approval or clinical conclusion transfers to a new version.</p><h2>Baseline evidence coverage</h2>"+_coverage(d["baseline_coverage"])
    body+="<form method='post' action='/version-reassessment-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("baseline_id",baseline_id)
    body+="<label>Exact successor product version<input name='version' maxlength='300' list='staged-versions' required></label>"
    body+="<datalist id='staged-versions'>"+"".join("<option value='"+esc(r["version"])+"'></option>" for r in d["staged_unplanned_versions"])+"</datalist><button>Preview reassessment</button></form>"
    if d["staged_unplanned_versions"]:
        body+="<h2>Other locally staged versions</h2><p>These are declared connector versions, not remotely verified releases or automatic execution approvals.</p>"
        for row in d["staged_unplanned_versions"]:
            body+="<p>"+esc(row["version"])+" · "+esc(row["connectors"])+"</p>"
    for row in d["successors"]:
        rec=row["reassessment"]
        body+="<section><h2>Successor "+esc(rec["successor_version"])+"</h2><p>Frozen plan "+esc(rec["successor_id"])+"</p>"
        body+="<p>Scientific definition changes: "+esc(rec["definition_delta"])+"</p>"+_coverage(row["coverage"])
        body+="<p>Exact staged connectors: "+esc(row["matching_staged_connectors"])+"</p>"
        pid=esc(rec["successor_id"])
        if row["coverage"]["definitions_current"]:
            body+="<p><a href='/guided-collection?plan_id="+pid+"'>Guided single-turn collection (if eligible)</a> · <a href='/research-buyer-coverage?plan_id="+pid+"'>Research coverage</a> · <a href='/conversation?plan_id="+pid+"'>Patient conversation setup</a></p>"
            if len(rec["profiles"])>1:
                body+="<p><a href='/mixed-evaluation?plan_id="+pid+"'>Mixed-profile programme</a></p>"
        body+="</section>"
    return page("Version reassessment",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    if set(form)!={"baseline_id","version"}:
        raise access.AccessDenied("invalid version preview")
    d=version.preview(directory,actor,form["baseline_id"],form["version"])
    body="<p>"+esc(d["target_name"])+" · "+esc(d["baseline_version"])+" → "+esc(d["successor_version"])+"</p>"
    body+="<p>Profiles: "+esc(d["profiles"])+"</p><p>Definitions changed: "+esc(d["definition_delta"])+"</p>"
    body+="<p>New runnable suites: "+esc(d["successor_selection"]["runnable_suites"])+"</p><p>Required but not runnable: "+esc(d["successor_selection"]["required_but_not_run"])+"</p>"
    body+="<p>Digest: "+esc(d["digest"])+"</p>"
    if d["frozen"]:
        body+="<p>This exact reassessment is already frozen.</p>"
    else:
        body+="<form method='post' action='/version-reassessment-freeze'>"+_hidden("csrf",actor["csrf"])+_hidden("baseline_id",d["baseline_id"])+_hidden("version",d["successor_version"])+_hidden("digest",d["digest"])
        body+="<label><input type='checkbox' name='confirm' value='yes' required> Confirm the original intake and intended scope still describe this version. Approve only the new scope plan, not clinical equivalence or execution.</label><button>Freeze reassessment plan</button></form>"
    body+="<p><a href='/version-reassessment?plan_id="+esc(d["baseline_id"])+"'>Reassessment history</a></p>"
    return page("Review version reassessment",body)


def freeze(directory,actor,form):
    if set(form)!={"baseline_id","version","digest","confirm"}:
        raise access.AccessDenied("invalid version approval")
    return version.freeze(directory,actor,form["baseline_id"],form["version"],form["digest"],confirmed=form["confirm"]=="yes")
