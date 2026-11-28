"""Server-rendered, administrator-only longitudinal evidence review."""
from . import institution_gap_review as service, institution_imaging_web as forms


def _link(ident):
    from .institution_web import esc
    return "/unified-assessment-gaps?id="+esc(ident)


def home(directory, actor, ident):
    from .institution_web import page, esc, _hidden
    from . import unified_assessment as u
    # Re-check active account and scope before rendering the form.
    from . import institution_access as access
    access.require(actor, "admin")
    status = u.status(directory, actor, ident)
    body = "<p><a href='"+_link(ident)+"'>Back to evidence gaps</a></p>"
    body += "<p>Compare an earlier approved local handoff with a fresh observation of "+esc(ident)+". Programme state: "+esc(status["state"])+".</p>"
    body += ("<p>Paste the historical handoff JSON (maximum 1 MiB) and enter the SHA-256 digest from your independent evidence register. "
             "Only previously audited local exports of the same frozen programme are accepted. No clinical acceptance, external authentication or deployment authorization.</p>")
    body += ("<form method='post' action='/unified-gap-review-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
             "<label>Baseline handoff JSON<textarea name='baseline' required rows='12' maxlength='1048576'></textarea></label>"
             "<label>Independently recorded baseline SHA-256 <input name='baseline_digest' required minlength='64' maxlength='64' pattern='[a-f0-9]{64}'></label>"
             "<button>Preview longitudinal review</button></form>")
    return page("Longitudinal programme evidence review",body)


def _table(headers, rows):
    from .institution_web import esc
    return "<table><tr>"+"".join("<th>"+esc(h)+"</th>" for h in headers)+"</tr>"+"".join(
        "<tr>"+"".join("<td>"+esc(v)+"</td>" for v in row)+"</tr>" for row in rows)+"</table>"


def _task_text(tasks):
    return "; ".join(t["role"]+" · "+t["priority"]+" · "+t["code"].replace("_"," ") for t in tasks) or "None projected"


def preview(directory, actor, form):
    from .institution_web import page, esc, _hidden
    forms._exact(form,"id baseline baseline_digest")
    baseline = service.parse_baseline(form["baseline"])
    bundle = service.preview(directory,actor,form["id"],baseline,form["baseline_digest"])
    result = bundle["comparison"]
    body = "<p><a href='"+_link(form["id"])+"'>Cancel and return to evidence gaps</a></p>"
    body += "<p>Programme "+esc(form["id"])+" · "+esc(result["baseline_programme_state"])+" → "+esc(result["followup_programme_state"])+"</p>"
    body += "<p>Baseline SHA-256: "+esc(result["baseline_digest"])+"</p><p>Follow-up SHA-256: "+esc(result["followup_digest"])+"</p>"
    body += "<p>Exact review bundle SHA-256: "+esc(bundle["digest"])+"</p>"
    if result["same_snapshot"]:
        body += "<p>The two handoffs are identical; no changed observation is established.</p>"
    body += "<h2>Administrative observations</h2>"+_table(("Measure","Count"),[(k.replace("_"," "),v) for k,v in result["summary"].items()])
    body += "<h2>Canonical suite inventory</h2>"+_table(("Vendor/version","Plan","Suite","Baseline condition","Follow-up condition"),[
        (r["vendor_id"]+" · "+r["target"]["version"],r["plan_id"],r["suite"],r["baseline_condition"],r["followup_condition"])
        for r in result["canonical_suites"]])
    body += "<h2>Requirement changes and follow-up tasks</h2>"
    for r in result["requirements"]:
        body += "<section><h3>"+esc(r["requirement_id"]+" · "+r["vendor_id"]+" · "+r["target"]["version"])+"</h3>"
        body += "<p>"+esc(r["baseline_coverage"]+" → "+r["followup_coverage"])+" · "+esc(r["baseline_condition"]+" → "+r["followup_condition"])+"</p>"
        body += "<p>Association: "+esc(r["association_change"])+" · Flags: "+esc(", ".join(r["followup_flags"]) or "None projected")+"</p>"
        body += _table(("Observation","Baseline","Follow-up","Observed delta"),[
            (k,v["baseline"],v["followup"],v["observed_delta"] if v["observed_delta"] is not None else "Not comparable")
            for k,v in r["work_observations"].items()])
        body += "<p>New tasks: "+esc(_task_text(r["new_tasks"]))+"</p><p>Persistent tasks: "+esc(_task_text(r["persistent_tasks"]))+"</p>"
        body += "<p>No longer projected (not proof of completion): "+esc(_task_text(r["no_longer_projected_tasks"]))+"</p></section>"
    body += "<h2>Limitations</h2>"+"".join("<p>"+esc(s)+"</p>" for s in result["limitations"])
    # Resubmit the bounded original baseline, not a user-editable derived report.
    # The backend recomputes the full bundle and checks the exact approval.
    body += ("<form method='post' action='/unified-gap-review-export'>"+_hidden("csrf",actor["csrf"])+_hidden("id",form["id"])+
             _hidden("baseline",form["baseline"])+_hidden("baseline_digest",form["baseline_digest"])+_hidden("approval",bundle["digest"])+
             "<label><input type='checkbox' name='confirm' value='yes' required> Export this exact private bundle, including both handoffs, for an authorized recipient.</label>"
             "<button>Download approved review bundle</button></form>")
    return page("Approve longitudinal evidence review",body)


def export(directory,actor,form):
    forms._exact(form,"id baseline baseline_digest approval confirm")
    forms._confirm(form)
    baseline = service.parse_baseline(form["baseline"])
    return service.export(directory,actor,form["id"],baseline,form["baseline_digest"],form["approval"],confirmed=True)
