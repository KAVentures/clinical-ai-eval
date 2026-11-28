"""Role-scoped hospital referral policy browser."""
import json
from . import institution_hospital_referral as service, hospital_referral as h
from . import institution_imaging_web as forms


def home(directory,actor):
    from .institution_web import page,esc,_hidden
    body="<p><a href='/'>Dashboard</a></p><p>Hospital-specific administrative referral gates. No automated clinical acceptance or deployment authorization.</p>"
    if actor["role"]=="admin":
        body+=("<section><h2>Register policy before vendor evidence association</h2><p>Required gate IDs: "+esc(", ".join(h.MANDATORY))+". Assign at least two distinct active reviewer accounts.</p><form method='post' action='/hospital-referral-preview'>"+_hidden("csrf",actor["csrf"])+
               "<label>Policy specification JSON<textarea name='spec' rows='12' maxlength='65536' required></textarea></label><button>Preview policy</button></form></section>")
    for row in service.overview(directory,actor):
        body+="<section><h2>"+esc(row["id"])+"</h2><p>"+esc(row["state"])+"</p>"
        if actor["role"]=="reviewer":
            body+="<a href='/hospital-referral-assigned?id="+esc(row["id"])+"'>Review assigned gates</a>"
        else:
            body+="<p>"+esc(row["vendor_id"])+" · "+esc(row["target"]["version"])+" · "+esc(row["n_reviewed"])+" / "+esc(row["n_gates"])+" locked</p>"
            if row["disposition"]:
                body+="<p>Historical disposition: "+esc(row["disposition"])+" · snapshot current: "+esc(row["snapshot_current"])+"</p>"
            if actor["role"]=="admin":
                body+="<a href='/hospital-referral-detail?id="+esc(row["id"])+"'>Inspect policy</a>"
        body+="</section>"
    return page("Hospital referral policy",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"spec")
    p=service.preview(directory,actor,forms._parse(form["spec"]))
    body="<p>Exact policy digest "+esc(p["digest"])+"</p><pre>"+esc(json.dumps(p,indent=2))+"</pre>"
    body+=("<form method='post' action='/hospital-referral-freeze'>"+_hidden("csrf",actor["csrf"])+_hidden("spec",json.dumps(p["spec"]))+_hidden("approval",p["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Freeze exact vendor scope, intended use and all reviewer criteria.</label><button>Register policy</button></form>")
    return page("Approve hospital policy",body)


def detail(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    row=service.detail(directory,actor,ident)
    body="<p><a href='/hospital-referral'>Policies</a></p><p>Administrative referral only. External artifact integrity and clinical safety are not verified.</p><pre>"+esc(json.dumps(row["coverage"],indent=2))+"</pre><h2>Frozen policy</h2><pre>"+esc(json.dumps(row["policy"],indent=2))+"</pre>"
    if not row["snapshot"]:
        body+=("<form method='post' action='/hospital-referral-snapshot-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+"<button>Preview current evidence snapshot</button></form>")
    else:
        body+="<h2>Reviewed evidence conditions</h2><pre>"+esc(json.dumps(row["snapshot"],indent=2))+"</pre><h2>Locked gate ratings</h2><pre>"+esc(json.dumps(row["ratings"],indent=2))+"</pre>"
        if not row["decision"]:
            body+=("<form method='post' action='/hospital-referral-decision-preview'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+"<label>Disposition<select name='kind'>")
            for kind in h.DISPOSITIONS:
                body+="<option value='"+esc(kind)+"'>"+esc(kind)+"</option>"
            body+="</select></label><label>Rationale<textarea name='rationale' required maxlength='4000'></textarea></label><button>Preview disposition</button></form>"
        else:
            body+="<h2>Historical disposition</h2><pre>"+esc(json.dumps(row["decision"],indent=2))+"</pre>"
            body+=("<form method='post' action='/hospital-referral-export'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",row["decision"]["digest"])+
                   "<label><input type='checkbox' name='confirm' value='yes' required> Export restricted historical evidence and reviewer rationale.</label><button>Download evidence</button></form>")
    return page("Hospital gate review",body)


def snapshot_preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"id")
    p=service.snapshot_preview(directory,actor,form["id"])
    body="<p>One immutable snapshot per policy. If evidence changes later, referral is blocked; register a new programme and policy for reassessment.</p><pre>"+esc(json.dumps(p["payload"]["snapshot"],indent=2))+"</pre>"
    body+=("<form method='post' action='/hospital-referral-start'>"+_hidden("csrf",actor["csrf"])+_hidden("id",form["id"])+_hidden("approval",p["approval"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Freeze this exact evidence snapshot for assigned review.</label><button>Start review</button></form>")
    return page("Approve evidence snapshot",body)


def assigned(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    row=service.assigned(directory,actor,ident)
    body="<p>Assess your assigned gate using authorized external evidence. Artifact SHA-256 is a reviewer attestation, not verified by this application. Do not include patient information.</p><p>"+esc(row["target"]["name"])+" · "+esc(row["target"]["version"])+" · "+esc(row["intended_use"])+"</p><pre>"+esc(json.dumps(row["snapshot"],indent=2))+"</pre>"
    for gate in row["gates"]:
        body+="<section><h2>"+esc(gate["id"])+"</h2><p>"+esc(gate["criterion"])+"</p>"
        if gate["locked"]:
            body+="<p>Rating locked.</p>"
        else:
            body+=("<form method='post' action='/hospital-referral-rate'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("gate_id",gate["id"])+_hidden("snapshot_digest",row["snapshot"]["digest"])+
                   "<label>Verdict<select name='verdict'><option value='unresolved'>Unresolved</option><option value='not_met'>Not met</option><option value='attested_met'>Attested met</option></select></label><label>External artifact SHA-256<input name='artifact_digest' maxlength='64'></label><label>Rationale<textarea name='rationale' required maxlength='3000'></textarea></label>"+
                   "<label><input type='checkbox' name='confirm' value='yes' required> Lock my own assessment and external evidence attestation.</label><button>Lock gate rating</button></form>")
        body+="</section>"
    return page("Assigned hospital gate reviews",body)


def decision_preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"id kind rationale")
    p=service.decision_preview(directory,actor,form["id"],form["kind"],form["rationale"])
    body="<p>This is an administrative disposition, never permission to deploy.</p><pre>"+esc(json.dumps(p,indent=2))+"</pre>"
    body+=("<form method='post' action='/hospital-referral-decide'>"+_hidden("csrf",actor["csrf"])+_hidden("id",form["id"])+_hidden("kind",p["disposition"])+_hidden("rationale",p["rationale"])+_hidden("approval",p["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Lock exact disposition and rationale.</label><button>Record disposition</button></form>")
    return page("Approve hospital disposition",body)


def dispatch(directory,actor,path,form):
    forms._confirm(form)
    if path=="/hospital-referral-freeze":
        forms._exact(form,"spec approval confirm")
        return service.freeze(directory,actor,forms._parse(form["spec"]),form["approval"],confirmed=True)
    if path=="/hospital-referral-start":
        forms._exact(form,"id approval confirm")
        service.start_review(directory,actor,form["id"],form["approval"],confirmed=True)
    elif path=="/hospital-referral-rate":
        forms._exact(form,"id gate_id snapshot_digest verdict artifact_digest rationale confirm")
        doc={k:form[k] for k in ("gate_id","snapshot_digest","verdict","artifact_digest","rationale")}
        doc["artifact_digest"]=doc["artifact_digest"] or None
        service.submit(directory,actor,form["id"],doc,confirmed=True)
    elif path=="/hospital-referral-decide":
        forms._exact(form,"id kind rationale approval confirm")
        service.decide(directory,actor,form["id"],form["kind"],form["rationale"],form["approval"],confirmed=True)
    elif path=="/hospital-referral-export":
        forms._exact(form,"id approval confirm")
        return service.export(directory,actor,form["id"],form["approval"],confirmed=True)
    else:
        raise ValueError("unknown policy action")
    return form["id"]
