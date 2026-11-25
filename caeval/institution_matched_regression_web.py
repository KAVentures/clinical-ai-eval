"""Browser interface for prospective successor registration and restricted results."""
import json
from . import institution_matched_regression as service, matched_version_regression as regression
from . import institution_imaging_web as forms, institution_access as access


def home(directory,actor):
    from .institution_web import page,esc,_hidden
    body="<p><a href='/'>Dashboard</a></p><p>Compare exact declared versions on the same frozen historical cohort. Results are descriptive only, not clinical acceptance or statistical noninferiority.</p>"
    if actor["role"]=="admin":
        body+=("<section><h2>Register a matched version study</h2><p>Seal the baseline, freeze the successor with identical cohort and reference definitions, then register before exporting any successor candidate material or submitting traces.</p>"
               "<form method='post' action='/matched-regression-preview'>"+_hidden("csrf",actor["csrf"])+
               "<label>Modality<select name='kind'><option value='imaging'>Diagnostic imaging</option><option value='rag_grounding'>RAG grounding</option></select></label>"
               "<label>Sealed baseline study ID<input name='baseline' maxlength='80' required></label><label>Unexposed successor study ID<input name='successor' maxlength='80' required></label>"
               "<label>Registered vendor ID (same in both studies)<input name='vendor_id' maxlength='80' required></label><label>Primary endpoint<select name='endpoint'>")
        for kind,endpoints in regression.ENDPOINTS.items():
            body+="<optgroup label='"+esc(kind)+"'>"
            for endpoint in endpoints:
                body+="<option value='"+esc(endpoint)+"'>"+esc(endpoint)+"</option>"
            body+="</optgroup>"
        body+=("</select></label><label>Descriptive margin (basis points, successor minus baseline)<input type='number' name='margin_bp' min='-10000' max='10000' value='0' required></label>"
               "<label>Maximum unknown pair fraction (basis points)<input type='number' name='max_unknown_bp' min='0' max='10000' value='1000' required></label>"
               "<button>Preview exact matched cohort</button></form></section>")
    for row in service.overview(directory,actor):
        body+="<section><h2>"+esc(row["kind"])+" · "+esc(row["baseline"]["id"])+"</h2><p>"+esc(row["baseline"]["version"])+" → "+esc(row["successor"]["version"])+" · "+esc(row["state"])+"</p>"
        body+="<p>"+esc(row["baseline_study"])+" → "+esc(row["successor_study"])+" · "+esc(row["primary_endpoint"])+"</p>"
        if actor["role"]=="admin":
            body+="<a href='/matched-regression-detail?id="+esc(row["id"])+"'>Inspect research protocol and result</a>"
        body+="</section>"
    return page("Matched longitudinal research",body)


def preview(directory,actor,form):
    from .institution_web import page,esc,_hidden
    forms._exact(form,"kind baseline successor vendor_id endpoint margin_bp max_unknown_bp")
    spec={k:form[k] for k in ("vendor_id","endpoint")}
    spec.update({k:int(form[k]) for k in ("margin_bp","max_unknown_bp")})
    doc=service.preview(directory,actor,form["kind"],form["baseline"],form["successor"],spec)
    body="<p>Exact cohort digest "+esc(doc["cohort_digest"])+"</p><p>Baseline "+esc(doc["baseline"]["version"])+" → successor "+esc(doc["successor"]["version"])+"</p><pre>"+esc(json.dumps(doc["parameters"],indent=2))+"</pre><ul>"
    for limit in doc["limitations"]:
        body+="<li>"+esc(limit)+"</li>"
    body+=("</ul><form method='post' action='/matched-regression-freeze'>"+_hidden("csrf",actor["csrf"])+
           _hidden("kind",doc["kind"])+_hidden("baseline",doc["baseline_study"])+_hidden("successor",doc["successor_study"])+
           _hidden("spec",json.dumps(spec))+_hidden("approval",doc["digest"])+
           "<label><input type='checkbox' name='confirm' value='yes' required> Register the exact historical cohort comparison before recorded successor exposure; no clinical or statistical acceptance claim.</label><button>Freeze comparison</button></form>")
    return page("Approve matched version comparison",body)


def detail(directory,actor,ident):
    from .institution_web import page,esc,_hidden
    row=service.detail(directory,actor,ident)
    body="<p><a href='/matched-regression'>Matched studies</a></p><h2>Frozen protocol</h2><pre>"+esc(json.dumps(row["protocol"],indent=2))+"</pre>"
    result=row["result"]
    if result is None:
        body+="<p>Successor evidence is not yet sealed. Complete the native collection, review and sealing workflow.</p>"
    else:
        body+="<h2>Descriptive version contrast</h2><p>Successor minus baseline, equal-cluster weighted. Bounds are missingness envelopes, not confidence intervals.</p><p>"+esc(result["descriptive_research_signal"])+"</p>"
        for endpoint in result["endpoints"]:
            body+="<section><h3>"+esc(endpoint["endpoint"])+"</h3><pre>"+esc(json.dumps(endpoint,indent=2))+"</pre></section>"
        body+=("<form method='post' action='/matched-regression-export'>"+_hidden("csrf",actor["csrf"])+_hidden("id",ident)+_hidden("approval",result["digest"])+
               "<label><input type='checkbox' name='confirm' value='yes' required> Export this exact restricted research comparison.</label><button>Download comparison</button></form>")
    return page("Matched version research protocol",body)


def dispatch(directory,actor,path,form):
    forms._confirm(form)
    if path=="/matched-regression-freeze":
        forms._exact(form,"kind baseline successor spec approval confirm")
        return service.freeze(directory,actor,form["kind"],form["baseline"],form["successor"],forms._parse(form["spec"]),form["approval"],confirmed=True)
    if path=="/matched-regression-export":
        forms._exact(form,"id approval confirm")
        return service.export(directory,actor,form["id"],form["approval"],confirmed=True)
    raise access.AccessDenied("unknown longitudinal action")
