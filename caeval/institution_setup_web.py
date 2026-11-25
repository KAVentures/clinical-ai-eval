"""Institutional setup views; all network execution remains operator CLI only."""
from . import institution_setup as setup, institution_access as access, intake, buyer_agent as buyer


def render(directory,actor):
    from .institution_web import esc,page,_hidden
    data=setup.overview(directory,actor)
    def form(action,fields,button,upload=False,hidden=()):
        return ("<form method='post' action='/setup-"+action+"'"+(" enctype='multipart/form-data'" if upload else "")+">"
                +_hidden("csrf",actor["csrf"])+"".join(_hidden(k,v) for k,v in hidden)
                +fields+"<button>"+esc(button)+"</button></form>")
    def text(name,label,limit=300,area=False):
        return "<label>"+esc(label)+(" <textarea required name='"+name+"' maxlength='"+str(limit)+"'></textarea>" if area else " <input required name='"+name+"' maxlength='"+str(limit)+"'>")+"</label>"
    def select(name,label,options):
        return "<label>"+esc(label)+" <select required name='"+name+"'><option value=''>Select</option>"+"".join("<option value='"+esc(v)+"'>"+esc(t)+"</option>" for v,t in options)+"</select></label>"
    def upload():
        return "<label>Document <input type='file' name='document' required></label>"
    body="<p><a href='/'>Workspace</a></p><p>Operator preparation only. Do not upload patient identifiers or secrets. No model calls, clinical approvals or purchasing decisions occur here.</p>"
    body+="<p><a href='/buyer-risk-active'>Active risk-bound assessment (if configured)</a></p>"
    body+="<section><h2>1. Register candidates</h2>"+form("vendor",text("vendor_id","Candidate ID",64)+text("name","Candidate name"),"Register candidate")+"</section>"
    vendor_options=[(v["vendor_id"],v["name"]) for v in data["vendors"]]
    for v in data["vendors"]:
        vid=v["vendor_id"]
        body+="<section><h3>"+esc(v["name"])+"</h3><p>Missing evidence categories: "+esc(", ".join(v["missing_evidence_kinds"]) or "None recorded")+"</p>"
        body+="<p><a href='/setup-download?kind=questionnaire&amp;id="+esc(vid)+"'>Download vendor questionnaire</a></p>"
        body+=form("questionnaire",upload(),"Import completed questionnaire",True,(("vendor_id",vid),))
        body+=form("document",select("kind","Evidence category",[(k,k) for k in buyer.EVIDENCE_KINDS])+text("title","Document title")+upload(),"Store evidence document",True,(("vendor_id",vid),))
        for document in v["documents"]:
            body+="<p>"+esc(document["title"])+" · "+esc(document["integrity"])+" · <a href='/vendor-document-review?id="+esc(document["evidence_id"])+"'>Review original source and freshness</a></p>"
        body+="</section>"
    if vendor_options:
        body+="<section><h2>2. Scientific evaluation plan</h2>"
        profiles="".join("<label><input type='checkbox' name='profile_"+esc(p)+"' value='yes'> "+esc(p.replace("_"," "))+"</label>" for p in intake.TARGET_PROFILES)
        questions="".join(text(q,q.replace("_"," "),4000,True) for q in intake.INTAKE_QUESTIONS)
        body+=form("plan",select("vendor_id","Candidate",vendor_options)+text("target_name","Product name")+text("target_version","Exact version")+profiles+questions,"Save draft evaluation plan")+"</section>"
    for plan in data["plans"]:
        body+="<section><h3>Plan "+esc(plan["id"][:12])+"</h3>"
        if plan.get("invalid"):
            body+="<p>Invalid plan.</p></section>"
            continue
        body+="<p>"+esc(plan["target"]["name"])+" "+esc(plan["target"]["version"])+" · "+("Current definitions" if plan["current"] else "Stale definitions")+"</p>"
        body+="<p><a href='/version-reassessment?plan_id="+esc(plan["id"])+"'>Plan version reassessment and review history</a></p>"
        if plan["current"]:
            if len(plan["profiles"])>1:
                body+="<p><a href='/mixed-evaluation?plan_id="+esc(plan["id"])+"'>Mixed-profile evaluation programme</a></p>"
            body+="<p><a href='/guided-collection?plan_id="+esc(plan["id"])+"'>Guided candidate onboarding and collection</a></p>"
            body+="<p><a href='/research-buyer-coverage?plan_id="+esc(plan["id"])+"'>View related experimental research coverage</a></p>"
        body+="<p><a href='/buyer-risk?plan_id="+esc(plan["id"])+"'>Buyer clinical risk protocol</a></p>"
        if plan["current"]:
            body+="<p><a href='/fhir?plan_id="+esc(plan["id"])+"'>Synthetic FHIR interoperability rehearsal</a></p>"
            body+="<p><a href='/hl7?plan_id="+esc(plan["id"])+"'>Synthetic HL7 v2 interoperability rehearsal</a></p>"
        for r in plan["selection"]["required_suites"]:
            body+="<p>"+esc(r["suite"])+" — "+("Runnable" if r["implemented"] else "Required, not runnable")+"</p>"
        if plan["current"] and data["packs"]:
            body+=form("exchange",select("pack_id","Approved case pack",[(p,p) for p in data["packs"]]),"Prepare blinded prompts",hidden=(("plan_id",plan["id"]),))
        body+="</section>"
    body+="<section><h2>3. Collect responses</h2>"
    for e in data["exchanges"]:
        body+="<p>"+esc(e["id"])+(" — invalid" if e.get("invalid") else " · "+str(e["n_slots"])+" prompts <a href='/setup-download?kind=prompts&amp;id="+esc(e["id"])+"'>Download blinded prompts</a>")+"</p>"
    body+="<p>Use the approved collection queue for automated local testing, or import a completed offline template.</p><p><a href='/collections'>Automated candidate collections</a></p>"
    body+=form("responses",upload(),"Import response template",True)+"</section>"
    body+="<section><h2>4. Prepare assessment</h2>"
    for s in data["submissions"]:
        body+="<p>"+esc(s["id"])+(" — invalid" if s.get("invalid") else " · "+esc(s["status"])+" · "+str(s["provided"])+"/"+str(s["expected"]))+"</p>"
        if s.get("status")=="complete_unscored" and data["panels"]:
            options=[("yes","Yes"),("no","No")]
            gov=select("influences_diagnosis_or_treatment","Influences diagnosis or treatment?",options)+select("clinician_reviews_every_response","Clinician reviews every response?",options)+select("regulated_medical_device","Regulated medical device?",options+[("unsure","Unsure")])+text("deployment_jurisdictions","Deployment jurisdictions",500)
            body+=form("assessment",select("panel_id","Approved judge panel",[(p,p) for p in data["panels"]])+gov,"Prepare scientific assessment",hidden=(("submission_id",s["id"]),))
    body+="</section><section><h2>5. Queue assessment</h2>"
    for p in data["prepared"]:
        body+="<p>"+esc(p["id"])+(" — invalid" if p.get("invalid") else " · "+esc(p["mode"])+" · "+str(p["n_responses"])+" responses")+"</p>"
        if not p.get("invalid"):
            body+=form("queue","","Queue without executing",hidden=(("assessment_id",p["id"]),))
    for job in data["jobs"]:
        body+="<p>"+esc(job["id"])+" · "+esc(job["status"])+"</p>"
        if job["status"]=="queued":
            body+=form("cancel","","Cancel queued job",hidden=(("job_id",job["id"]),))
    return page("Candidate intake and assessment setup",body+"</section><p>IT stages case packs, connectors and judge panels. Approved local CLI operators execute jobs.</p>")
