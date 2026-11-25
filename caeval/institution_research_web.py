"""Institutional research studio pages. All input is escaped, all POSTs CSRF checked."""
import json
import urllib.parse

from . import institution_research as studio, institution_access as access
from . import buyer_manual as manual, buyer_plan


def _link(path, **query):
    return path+"?"+urllib.parse.urlencode(query)


def _json(value):
    return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)


def render(directory,actor):
    from .institution_web import page,esc,_hidden
    role=actor["role"]
    def form(action,fields,button,upload=False):
        return ("<form method='post' action='/research-"+action+"'"+
                (" enctype='multipart/form-data'" if upload else "")+">"+
                _hidden("csrf",actor["csrf"])+fields+"<button>"+esc(button)+"</button></form>")
    def upload():
        return "<label>JSON document <input type='file' name='document' accept='.json,application/json' required></label>"
    def checkbox(label):
        return "<label><input type='checkbox' name='confirm' value='yes' required> "+esc(label)+"</label>"
    def link(path,label,**params):
        return "<a href='"+esc(_link(path,**params))+"'>"+esc(label)+"</a>"
    body="<p><a href='/'>Workspace</a></p><p>Experimental research only. Use approved non-identifiable data. No model calls, clinical certification or deployment approval.</p>"
    if role in ("admin","operator"):
        body+="<p><a href='/research-comparisons'>Matched clinical research comparisons</a></p>"
        options="".join("<option value='"+esc(k)+"'>"+esc(v)+"</option>" for k,v in studio.LABELS.items())
        body+="<section><h2>1. Freeze a study</h2>"+form("create","<label>Assessment type <select name='kind' required>"+options+"</select></label>"+upload(),"Freeze specification",True)+"</section>"
    for row in studio.overview(directory,actor):
        ident=row["id"]
        state=row["state"]
        body+="<section><h2>"+esc(studio.LABELS[row["kind"]])+"</h2><p>"+esc(ident)+" · "+esc(state)+"</p>"
        if role=="reviewer":
            body+="<p>Assigned role: "+esc(row["role"])+"</p>"
            if state=="reviewing" and not row["pending_hash"]:
                body+=link("/research-review","Open your review",id=ident)+" · "+link("/research-download","Download own packet",id=ident,kind="review")
                body+=form("submit",_hidden("id",ident)+upload(),"Lock completed packet",True)
            else:
                body+="<p>Submission locked or no longer open.</p>"
        elif role in ("admin","operator"):
            if "plan_hash" in row:
                body+="<p>Plan approval hash: "+esc(row["plan_hash"])+"</p>"
            protocol_status=row.get("review_protocol_status","not_preregistered")
            body+="<p>Review protocol: "+esc(protocol_status)+"</p>"
            if role=="admin" and row["kind"]!="decision":
                if protocol_status=="available":
                    fields=_hidden("id",ident)+"<label>Independent reviewer IDs (comma-separated)<input name='reviewers' required maxlength='1000'></label>"
                    if row["kind"]=="robustness":
                        fields+="<label>Separate reference-validity reviewer IDs<input name='validity' required maxlength='1000'></label>"
                    else:
                        fields+=_hidden("validity","")
                    fields+="<label>Research question<textarea name='question' required maxlength='2000'></textarea></label><label>Planned descriptive analysis<textarea name='intent' required maxlength='2000'></textarea></label>"
                    body+=form("freeze-review-protocol",fields+checkbox("I confirm external reviewer independence, reference provenance and no known prior candidate exposure."),"Freeze review protocol")
                if "review_protocol" in row:
                    body+="<p>Frozen protocol digest: "+esc(row["review_protocol"]["digest"])+"</p>"
                body+=link("/research-download","Download review provenance",id=ident,kind="review-provenance")+" · "
            body+="<form method='post' action='/research-buyer-link'>"+_hidden("csrf",actor["csrf"])+_hidden("study",ident)
            body+="<label>Saved buyer plan ID <input name='plan_id' required minlength='64' maxlength='64'></label>"+checkbox("I confirm this study belongs to the exact buyer scope and version. This association is immutable and not a canonical pass.")+"<button>Associate with buyer plan</button></form>"
            for kind,label in (("plan","Frozen plan"),("exchange","Candidate inputs"),("responses","Response template")):
                body+=link("/research-download",label,id=ident,kind=kind)+" · "
            if state=="planned" and row.get("connectors"):
                options="".join("<option value='"+esc(k)+"'>"+esc(k)+"</option>" for k in row["connectors"])
                fields=_hidden("id",ident)+_hidden("approve",row["plan_hash"])+"<label>Approved local connector <select name='connector' required>"+options+"</select></label>"
                body+=form("queue",fields+checkbox("I approve this exact plan for supervised local candidate collection."),"Queue collection")
            if "collection" in row:
                job=row["collection"]
                body+="<p>Collection: "+esc(job["state"])+" · "+esc(job["n_recorded"])+"/"+esc(job["n_expected"])+" outcomes recorded</p>"
                if job["state"]=="queued":
                    body+="<p>Trusted worker approval digest: "+esc(job["binding_hash"])+"</p>"
                    body+=form("cancel-collection",_hidden("id",ident),"Cancel queued collection")
                if job["state"]=="raw_collected":
                    body+=link("/research-download","Exact raw collection receipt",id=ident,kind="collection-receipt")+" · "
            if state=="raw_collected":
                body+="<p>Download the response template now populated with exact raw outcomes. Add only required claim annotations; do not edit candidate answers or failure states.</p>"
            if state in ("planned","raw_collected"):
                body+=form("collect",_hidden("id",ident)+_hidden("approve",row["plan_hash"])+upload()+
                    checkbox("I approve this exact frozen plan and confirm these are the recorded candidate responses."),"Import outcomes",True)
            if role=="admin" and state=="collected" and row["kind"]!="decision":
                fields=_hidden("id",ident)+"<label>Independent reviewer account IDs (comma-separated, 2–5)<input name='reviewers' maxlength='1000' required></label>"
                if row["kind"]=="robustness":
                    fields+="<label>Separate reference-validity reviewer account IDs (comma-separated, 2–5)<input name='validity' maxlength='1000' required></label>"
                else:
                    fields+=_hidden("validity","")
                if "review_protocol" in row:
                    body+="<p>Use the exact preregistered roster: "+esc(", ".join(x["reviewer"]+" ("+x["role"]+")" for x in row["review_protocol"]["protocol"]["roster"]))+"</p>"
                body+=form("assign",fields+checkbox("I verified clinical credentials, independence, conflicts and reference provenance externally."),"Assign reviewers")
            if role=="admin" and state=="reviewing":
                for rev in row.get("reviews",[]):
                    body+="<p>"+esc(rev["reviewer"])+" · "+esc(rev["role"])+" · "+("attested" if rev["canonical_hash"] else "locked" if rev["pending_hash"] else "awaiting submission")+"</p>"
                    if rev["pending_hash"] and not rev["canonical_hash"]:
                        body+="<p>Submission digest: "+esc(rev["pending_hash"])+"</p>"
                        fields=_hidden("id",ident)+_hidden("reviewer",rev["reviewer"])+_hidden("digest",rev["pending_hash"])
                        body+=form("attest",fields+checkbox("I independently confirmed the submitting clinician's identity and review independence for this exact locked submission."),"Attest")
            if role=="admin" and (state=="reviewing" or state=="collected" and row["kind"]=="decision"):
                body+=form("analyze",_hidden("id",ident)+checkbox("I approve descriptive analysis, not a clinical or procurement decision."),"Analyze")
            if state=="analyzed":
                body+=link("/research-download","Download verified report",id=ident,kind="report")
        if "summary" in row:
            body+="<h3>Descriptive results</h3><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"+esc(_json(row["summary"]))+"</pre>"
        body+="</section>"
    return page("Clinical research studio",body)


def review_page(directory,actor,identifier):
    from .institution_web import page,esc,_hidden
    doc=studio.download(directory,actor,identifier,"review")
    body="<p><a href='/research'>Assigned studies</a></p><p>Review independently. Fill only the submission JSON. Do not change item identifiers. The submitted review is immutable.</p>"
    # All content comes from the role-filtered packet, not the full study.
    body+="<h2>Issued review material</h2><pre style='white-space:pre-wrap;overflow-wrap:anywhere'>"+esc(_json(doc["request"]))+"</pre>"
    body+="<form method='post' action='/research-lock'>"+_hidden("csrf",actor["csrf"])+_hidden("id",identifier)
    body+=_hidden("packet_hash",buyer_plan._digest(doc["request"]))
    body+="<label>Independent submission JSON<textarea name='submission' rows='24' required>"+esc(_json(doc["submission"]))+"</textarea></label><button>Lock submission</button></form>"
    return page("Your independent research review",body)


def dispatch(directory,actor,action,form,blob=None):
    def exact(keys):
        if set(form)!=set(keys):
            raise access.AccessDenied("invalid research form")
    def confirmed():
        return form.get("confirm")=="yes"
    def ids(value):
        return [x.strip() for x in value.split(",") if x.strip()]
    if action=="create":
        exact(("kind",))
        studio.create(directory,actor,form["kind"],manual._parse(blob))
    elif action=="collect":
        exact(("id","approve","confirm"))
        studio.collect(directory,actor,form["id"],form["approve"],manual._parse(blob),confirmed=confirmed())
    elif action=="queue":
        from . import research_collection as collection
        exact(("id","connector","approve","confirm"))
        collection.queue(directory,actor,form["id"],form["connector"],form["approve"],confirmed=confirmed())
    elif action=="cancel-collection":
        from . import research_collection as collection
        exact(("id",))
        collection.cancel(directory,actor,form["id"])
    elif action=="freeze-review-protocol":
        from . import research_review_protocol as protocol
        exact(("id","reviewers","validity","question","intent","confirm"))
        protocol.freeze(directory,actor,form["id"],ids(form["reviewers"]),ids(form["validity"]),form["question"],form["intent"],confirmed=confirmed())
    elif action=="assign":
        exact(("id","reviewers","validity","confirm"))
        studio.assign(directory,actor,form["id"],ids(form["reviewers"]),ids(form["validity"]),confirmed=confirmed())
    elif action=="submit":
        exact(("id",))
        studio.lock_review(directory,actor,form["id"],manual._parse(blob))
    elif action=="lock":
        exact(("id","packet_hash","submission"))
        doc=studio.download(directory,actor,form["id"],"review")
        if buyer_plan._digest(doc["request"])!=form["packet_hash"]:
            raise access.AccessDenied("issued packet changed")
        doc["submission"]=manual._parse(form["submission"].encode("utf-8"))
        studio.lock_review(directory,actor,form["id"],doc)
    elif action=="attest":
        exact(("id","reviewer","digest","confirm"))
        studio.attest(directory,actor,form["id"],form["reviewer"],form["digest"],confirmed=confirmed())
    elif action=="analyze":
        exact(("id","confirm"))
        studio.analyze(directory,actor,form["id"],confirmed=confirmed())
    else:
        raise access.AccessDenied("unknown research action")
