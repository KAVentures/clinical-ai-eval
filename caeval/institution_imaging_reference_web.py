"""No-install structured imaging reference forms with role-scoped assignments."""
import json
from . import (institution_imaging_reference as service, institution_imaging_web as forms,
               imaging_reference as ref, diagnostic_imaging as imaging, institution_access as access)


def home(directory,actor):
    from .institution_web import page,esc,_hidden
    body="<p><a href='/'>Dashboard</a></p><p>Experimental binary reference review. Read authorized exact assets in the institution's approved external imaging viewer. Do not upload images or identifying information. This is not a clinical diagnostic workflow.</p>"
    if actor["role"]=="admin":
        body+=("<section><h2>Reserve prospective reference cohort</h2><form method='post' action='/imaging-reference-preview'>"+_hidden("csrf",actor["csrf"])+
               "<label>Unlabeled imaging specification JSON<textarea name='spec' required rows='10' maxlength='1048576'></textarea></label>"+
               "<label>Primary reader 1 account<input name='reader_a' required maxlength='80'></label>"+
               "<label>Primary reader 2 account<input name='reader_b' required maxlength='80'></label>"+
               "<label>Independent adjudicator account<input name='adjudicator' required maxlength='80'></label><button>Preview cohort and assignments</button></form></section>")
    for row in service.overview(directory,actor):
        ident=esc(row["id"])
        body+="<section><h3>"+ident+"</h3><p>"+esc(row["state"])+" · "+esc(row["n_cases"])+" cases</p>"
        if actor["role"]=="admin":
            body+="<p>"+esc(row["n_primary_locked"])+" / 2 primary reviews locked</p>"
            if row["state"]=="ready":
                body+="<a href='/imaging-reference-finalize?id="+ident+"'>Review and finalize reference cohort</a>"
            if row["state"]=="finalized":
                body+=("<p><a href='/diagnostic-imaging-study?id="+ident+"'>Open final imaging study</a></p>"+
                       "<form method='post' action='/imaging-reference-evidence'>"+_hidden("csrf",actor["csrf"])+
                       _hidden("id",row["id"])+_hidden("digest",row["reference_digest"])+
                       "<label><input type='checkbox' name='confirm' value='yes' required> Export restricted reference evidence including reader identities, labels and private cohort.</label><button>Download reference evidence</button></form>")
        elif not row["locked"] and row["state"]!="finalized":
            body+="<p>"+esc(row["role"])+" assignment. Adjudication opens only after both independent reads and only if there are disagreements.</p><a href='/imaging-reference-review?id="+ident+"'>Open assigned review</a>"
        else:
            body+="<p>Review locked or finalized.</p>"
        body+="</section>"
    return page("Independent imaging reference review",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"spec reader_a reader_b adjudicator")
    result=service.preview(directory,actor,forms._parse(form["spec"]),[form["reader_a"],form["reader_b"]],form["adjudicator"])
    body="<p>Study "+esc(result["spec"]["study_id"])+" · "+esc(result["n_cases"])+" cases</p><p>Two distinct primary reader accounts: "+esc(", ".join(result["readers"]))+"; adjudicator: "+esc(result["adjudicator"])+"</p>"
    body+="<p>Approval digest: "+esc(result["approval_digest"])+"</p><p>Confirm the exact cohort, candidate roster and reviewer assignments before exposure. Reviewers must be appropriately qualified and independent; the application cannot verify those facts.</p>"
    body+=("<form method='post' action='/imaging-reference-freeze'>"+_hidden("csrf",actor["csrf"])+
           _hidden("spec",json.dumps(result["spec"],ensure_ascii=True))+_hidden("reader_a",result["readers"][0])+_hidden("reader_b",result["readers"][1])+
           _hidden("adjudicator",result["adjudicator"])+_hidden("approval",result["approval_digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Reserve this exact prospective review cohort and independent assignments.</label><button>Reserve cohort</button></form>")
    return page("Approve independent imaging reference protocol",body)


def review(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    packet=service.packet(directory,actor,ident)
    body="<p>Task: "+esc(packet["task_id"])+" · "+esc(packet["modality"])+"</p><p>Match each exact SHA-256 asset using the approved external viewer. No default ratings are selected. Mark unresolved when you cannot establish the reference. Do not consult candidate predictions.</p>"
    body+=("<form method='post' action='/imaging-reference-lock'>"+_hidden("csrf",actor["csrf"])+
           _hidden("id",ident)+_hidden("packet",imaging._digest(packet)))
    for i,case in enumerate(packet["cases"],1):
        slot=case["slot"]
        body+="<section><h3>Case "+str(i)+"</h3><p class='case'>Asset SHA-256: "+esc(case["asset_sha256"])+"</p>"
        if "prior_labels" in case:
            body+="<p>Disagreeing primary labels (unordered): "+esc(", ".join(case["prior_labels"]))+"</p>"
        body+="<label>Reference label<select required name='r_"+esc(slot)+"'><option value='' selected disabled>Choose a label</option>"
        for label in ref.LABELS:
            body+="<option value='"+label+"'>"+label+"</option>"
        body+="</select></label></section>"
    body+="<label><input type='checkbox' name='confirm' value='yes' required> I independently inspected the authorized assets, did not consult candidate predictions, and lock these exact ratings.</label><button>Lock review permanently</button></form>"
    return page("Assigned independent reference review",body)


def final_preview(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    result=service.final_preview(directory,actor,ident)
    body="<p>Private reference summary. Finalizing creates the immutable candidate imaging study; no labels are sent in candidate exchanges.</p><table>"
    for key,value in result["summary"].items():
        body+="<tr><th>"+esc(key.replace("_"," "))+"</th><td>"+esc(value)+"</td></tr>"
    body+=("</table><form method='post' action='/imaging-reference-finalize'>"+_hidden("csrf",actor["csrf"])+
           _hidden("id",ident)+_hidden("approval",result["approval_digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Finalize the exact independent ratings; preserve unresolved cases.</label><button>Finalize reference cohort</button></form>")
    return page("Finalize reviewed imaging reference",body)


def dispatch(directory,actor,path,form):
    forms._confirm(form)
    if path=="/imaging-reference-freeze":
        forms._exact(form,"spec reader_a reader_b adjudicator approval confirm")
        service.freeze(directory,actor,forms._parse(form["spec"]),[form["reader_a"],form["reader_b"]],
                       form["adjudicator"],form["approval"],confirmed=True)
    elif path=="/imaging-reference-lock":
        if not {"id","packet","confirm"}<=set(form) or any(not k.startswith("r_") for k in set(form)-{"id","packet","confirm"}):
            raise access.AccessDenied("unexpected review fields")
        service.lock(directory,actor,form["id"],form["packet"],{k[2:]:v for k,v in form.items() if k.startswith("r_")},confirmed=True)
    elif path=="/imaging-reference-finalize":
        forms._exact(form,"id approval confirm")
        service.finalize(directory,actor,form["id"],form["approval"],confirmed=True)
    elif path=="/imaging-reference-evidence":
        forms._exact(form,"id digest confirm")
        return service.export(directory,actor,form["id"],form["digest"],confirmed=True)
    else:
        raise access.AccessDenied("unknown reference action")
