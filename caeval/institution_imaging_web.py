"""Server-rendered institutional imaging workflow; no image uploads or execution."""
import json
from . import institution_imaging as service, institution_access as access, buyer_manual


def _parse(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > service.MAX_INPUT:
        raise access.AccessDenied("bounded metadata JSON required")
    return buyer_manual._parse(raw.encode("utf-8"))


def _exact(form, fields):
    if set(form) != set(fields.split()):
        raise access.AccessDenied("unexpected imaging form fields")


def _confirm(form):
    if form.get("confirm") != "yes":
        raise access.AccessDenied("explicit confirmation required")


def home(directory, actor):
    from .institution_web import page, esc, _hidden
    body = "<p><a href='/'>Dashboard</a></p><p>Experimental binary imaging research. No images, patient identifiers, clinical notes or credentials. Use only approved non-identifying metadata. No clinical validation or deployment decision.</p>"
    if actor["role"] == "admin":
        body += ("<section><h2>New prospective study</h2><p>Paste the private specification from the diagnostic imaging research guide, including independently established reference labels. Freeze before candidate exposure.</p>"
                 "<form method='post' action='/diagnostic-imaging-preview'>" + _hidden("csrf", actor["csrf"]) +
                 "<label>Private study specification JSON<textarea name='spec' required rows='12' maxlength='1048576'></textarea></label><button>Preview study</button></form></section>")
    body += "<h2>Imaging studies</h2>"
    for row in service.overview(directory, actor):
        ident = esc(row["id"])
        body += "<section><a href='/diagnostic-imaging-study?id="+ident+"'>"+ident+"</a> · "+esc(row["state"])
        if "n_cases" in row:
            body += " · "+esc(row["n_cases"])+" cases · "+esc(sum(c["submitted"] for c in row["candidates"]))+" / "+esc(len(row["candidates"]))+" candidates submitted"
        body += "</section>"
    return page("Diagnostic imaging research", body)


def preview(directory, actor, form):
    from .institution_web import page, esc, _hidden
    _exact(form, "spec")
    result = service.preview(directory, actor, _parse(form["spec"]))
    spec = result["spec"]
    body = "<p><a href='/diagnostic-imaging'>Cancel</a></p><p>Study: "+esc(spec["study_id"])+" · "+esc(spec["task_id"])+" · "+esc(spec["modality"])+"</p>"
    body += "<p>Frozen case count: "+esc(result["n_cases"])+" · Candidate count: "+esc(len(spec["candidates"]))+"</p><p>Approval digest: "+esc(result["approval_digest"])+"</p>"
    body += "<p>Confirm the independently established reference, approved local research and no candidate exposure before freeze. This attestation is not verified automatically. A study ID cannot be reused.</p>"
    body += ("<form method='post' action='/diagnostic-imaging-freeze'>"+_hidden("csrf",actor["csrf"])+
             _hidden("spec",json.dumps(spec,ensure_ascii=True))+_hidden("approval",result["approval_digest"])+
             "<label><input type='checkbox' name='confirm' value='yes' required> Freeze this exact prospective study and restrict its private reference data.</label><button>Freeze study</button></form>")
    return page("Review private prospective imaging specification",body)


def study(directory, actor, ident):
    from .institution_web import page, esc, _hidden
    row = service.detail(directory, actor, ident)
    base = "<p><a href='/diagnostic-imaging'>Imaging studies</a></p><p>"+esc(row["task_id"])+" · "+esc(row["modality"])+" · "+esc(row["state"])+" · "+esc(row["n_cases"])+" cases</p>"
    base += "<p>Candidate metadata only. Do not use this workflow for live diagnostic decisions.</p>"
    base += "<table><tr><th>Candidate</th><th>Version</th><th>Threshold (bp)</th><th>Submission</th><th>Exchange</th></tr>"
    for candidate in row["candidates"]:
        cid = esc(candidate["id"])
        base += "<tr><td>"+cid+"</td><td>"+esc(candidate["version"])+"</td><td>"+esc(candidate["threshold_bp"])+"</td><td>"+("Locked" if candidate["submitted"] else "Missing")+"</td><td>"
        if row["state"] == "collecting":
            for kind,label in (("exchange","Inputs"),("template","Prediction template")):
                base += "<a href='/diagnostic-imaging-download?id="+esc(ident)+"&amp;candidate="+cid+"&amp;kind="+kind+"'>"+label+"</a> "
        base += "</td></tr>"
    base += "</table>"
    if row["collection_receipts"]:
        base += "<p>Local collection receipts (consistency only, not proof of execution): "+esc(", ".join(r["candidate_id"] for r in row["collection_receipts"]))+"</p>"
    if row["state"] == "collecting":
        base += ("<section><h2>Lock candidate predictions</h2><p>Each candidate gets one immutable submission; include every slot, including unavailable or abstained outputs. Interim reference metrics are not released.</p>"
                 "<form method='post' action='/diagnostic-imaging-submit'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
                 "<label>Prediction JSON<textarea name='predictions' rows='10' required maxlength='1048576'></textarea></label>"
                 "<label><input type='checkbox' name='confirm' value='yes' required> Lock this exact prediction document. No patient data or raw images.</label><button>Lock predictions</button></form></section>")
        base += ("<section><h2>Import local imaging collection receipt</h2><p>Use a receipt from the approved local exact-asset collection CLI. No image bytes are included. Receipt replay does not prove execution or clinical validity.</p>"
                 "<form method='post' action='/diagnostic-imaging-transport-import'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+
                 "<label>Collection receipt JSON<textarea name='receipt' rows='10' required maxlength='1048576'></textarea></label>"
                 "<label><input type='checkbox' name='confirm' value='yes' required> Lock this exact receipt and its prediction inventory.</label><button>Lock local collection receipt</button></form></section>")
        if actor["role"] == "admin":
            p = service.seal_preview(directory,actor,ident)
            base += ("<section><h2>Seal study</h2><p>"+esc(p["n_submitted"])+" / "+esc(p["n_expected"])+" candidate submissions. Sealing prevents further collection or replacement.</p>"
                     "<form method='post' action='/diagnostic-imaging-seal'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",p["approval_digest"])+
                     "<label><input type='checkbox' name='confirm' value='yes' required> Seal the exact current inventory.</label>")
            if p["n_submitted"] != p["n_expected"]:
                base += "<label><input type='checkbox' name='incomplete' value='yes' required> Preserve missing candidates explicitly; suppress comparisons.</label>"
            base += "<button>Seal study</button></form></section>"
    else:
        base += "<p>Evidence digest: "+esc(row["evidence_digest"])+" · Roster "+("complete" if row["roster_complete"] else "incomplete")+"</p>"
        if actor["role"] == "admin":
            base += "<h2>Exploratory results (overall)</h2><p>Conditional metrics are not clinical validity. Bounds are missingness bounds, not confidence intervals.</p>"
            for m in row["metrics"]:
                base += "<section><h3>"+esc(m["candidate"]["id"])+"</h3><p>"+esc(m["status"])+"</p>"
                if m["overall"]:
                    base += "<table>"
                    for key,value in m["overall"].items():
                        base += "<tr><th>"+esc(key.replace("_"," "))+"</th><td>"+esc(json.dumps(value,sort_keys=True))+"</td></tr>"
                    base += "</table>"
                base += "</section>"
            base += ("<form method='post' action='/diagnostic-imaging-evidence'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("digest",row["evidence_digest"])+
                     "<label><input type='checkbox' name='confirm' value='yes' required> Export restricted evidence containing private labels, case metadata, predictions and the full exploratory report; authorized recipients only.</label><button>Download restricted evidence</button></form>")
            if row["collection_receipts"]:
                base += ("<form method='post' action='/diagnostic-imaging-transport-evidence'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("digest",row["evidence_digest"])+
                         "<label><input type='checkbox' name='confirm' value='yes' required> Export restricted transport receipts and asset hashes.</label><button>Download transport evidence</button></form>")
    return page("Imaging study: "+ident,base)


def dispatch(directory, actor, path, form):
    if path == "/diagnostic-imaging-freeze":
        _exact(form,"spec approval confirm")
        _confirm(form)
        return service.freeze(directory,actor,_parse(form["spec"]),form["approval"],confirmed=True)
    if path == "/diagnostic-imaging-submit":
        _exact(form,"id predictions confirm")
        _confirm(form)
        service.submit(directory,actor,form["id"],_parse(form["predictions"]),confirmed=True)
        return form["id"]
    if path == "/diagnostic-imaging-transport-import":
        _exact(form,"id receipt confirm")
        _confirm(form)
        receipt=_parse(form["receipt"])
        service.submit(directory,actor,form["id"],receipt["predictions"],confirmed=True,receipt=receipt)
        return form["id"]
    if path == "/diagnostic-imaging-transport-evidence":
        from . import institution_imaging_transport as transport
        _exact(form,"id digest confirm")
        _confirm(form)
        return transport.export(directory,actor,form["id"],form["digest"],confirmed=True)
    if path == "/diagnostic-imaging-seal":
        if set(form) not in ({"id","approval","confirm"},{"id","approval","confirm","incomplete"}):
            raise access.AccessDenied("unexpected seal fields")
        _confirm(form)
        service.seal(directory,actor,form["id"],form["approval"],confirmed=True,
                     allow_incomplete=form.get("incomplete")=="yes")
        return form["id"]
    if path == "/diagnostic-imaging-evidence":
        _exact(form,"id digest confirm")
        _confirm(form)
        return service.export(directory,actor,form["id"],form["digest"],confirmed=True)
    raise access.AccessDenied("unknown imaging research action")
