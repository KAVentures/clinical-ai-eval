"""Minimal institution-only WSGI application behind an approved HTTPS proxy.

One instance per organisation/workspace. No route to the legacy buyer console,
filesystem, raw research packages, vendor data or model execution. The reference
launcher listens only on loopback; use a hardened WSGI server for deployment.
"""
from __future__ import annotations

import hmac
import html
import urllib.parse
from http.cookies import SimpleCookie
from pathlib import Path
from wsgiref.simple_server import make_server, WSGIRequestHandler

from . import institution_access as access, buyer_agent as buyer

MAX_BODY = 8 * 1024 * 1024
COOKIE = "__Host-caeval_institution"


def esc(value):
    return html.escape(str(value), quote=True)


def page(title, body):
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>" + esc(title) + "</title><style>"
            "body{font:16px system-ui;max-width:960px;margin:30px auto;padding:0 20px;color:#24313b}"
            "section{border:1px solid #ccd5da;padding:18px;margin:16px 0;border-radius:8px}"
            "label{display:block;margin:12px 0}input,select,textarea,button{font:inherit;padding:8px}"
            "textarea{width:95%}p.case{white-space:pre-wrap;overflow-wrap:anywhere}"
            "table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:8px;border-bottom:1px solid #ddd}"
            "</style></head><body><h1>" + esc(title) + "</h1>" + body + "</body></html>").encode()


def _hidden(name, value):
    return "<input type='hidden' name='" + esc(name) + "' value='" + esc(value) + "'>"


def _login_page():
    return page("Clinical AI Eval — institutional sign in",
                "<form method='post' action='/login'>"
                "<label>Account ID <input name='user_id' autocomplete='username' required maxlength='128'></label>"
                "<label>Password <input type='password' name='password' autocomplete='current-password' required></label>"
                "<button>Sign in</button></form>")


def _form(env):
    if env.get("CONTENT_TYPE", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise access.AccessDenied("invalid form")
    try:
        size = int(env.get("CONTENT_LENGTH", "0"))
        if not 0 < size <= MAX_BODY:
            raise ValueError()
        raw = env["wsgi.input"].read(size)
        if len(raw) != size:
            raise ValueError()
        pairs = urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True,
                                      max_num_fields=2500, strict_parsing=True)
        if any(len(v) != 1 for v in pairs.values()):
            raise ValueError()
        return {k: v[0] for k, v in pairs.items()}
    except (ValueError, UnicodeError) as exc:
        raise access.AccessDenied("invalid form") from exc


def _setup_upload(env, allowed=("csrf","vendor_id","kind","title","document")):
    """One bounded multipart document plus allowlisted scalar fields."""
    from email import policy
    from email.parser import BytesParser
    ctype=env.get("CONTENT_TYPE","")
    try:
        size=int(env.get("CONTENT_LENGTH","0"))
        if not 0<size<=MAX_BODY or not ctype.lower().startswith("multipart/form-data;") or any(c in ctype for c in ("\r","\n")):
            raise ValueError()
        raw=env["wsgi.input"].read(size)
        if len(raw)!=size:
            raise ValueError()
        msg=BytesParser(policy=policy.default).parsebytes(("MIME-Version: 1.0\r\nContent-Type: "+ctype+"\r\n\r\n").encode("ascii")+raw)
        if not msg.is_multipart() or msg.defects:
            raise ValueError()
        fields,seen={},set()
        blob,filename=None,None
        for part in msg.iter_parts():
            if part.is_multipart() or part.defects or part.get_content_disposition()!="form-data":
                raise ValueError()
            name=part.get_param("name",header="content-disposition")
            if name in seen or name not in allowed:
                raise ValueError()
            seen.add(name)
            payload=part.get_payload(decode=True)
            if not isinstance(payload,bytes):
                raise ValueError()
            if name=="document":
                blob=payload
                filename=(part.get_filename() or "").replace("\\","/").rsplit("/",1)[-1][:200]
                if not filename or not blob:
                    raise ValueError()
            else:
                if part.get_filename() or len(payload)>4096:
                    raise ValueError()
                fields[name]=payload.decode("utf-8")
        if blob is None:
            raise ValueError()
        return fields,blob,filename
    except (ValueError,TypeError,UnicodeError,RecursionError) as exc:
        raise access.AccessDenied("invalid multipart document") from exc


def _query(env):
    raw = env.get("QUERY_STRING", "")
    # Python 3.9 strict parsing rejects an empty query; no query is valid.
    if raw == "":
        return {}
    try:
        values = urllib.parse.parse_qs(raw, max_num_fields=4,
                                      keep_blank_values=True, strict_parsing=True)
        if any(len(v) != 1 for v in values.values()):
            raise ValueError()
        return {k:v[0] for k,v in values.items()}
    except ValueError as exc:
        raise access.AccessDenied("invalid query") from exc


def _cookie(env):
    try:
        c = SimpleCookie()
        c.load(env.get("HTTP_COOKIE", ""))
        return c[COOKIE].value if COOKIE in c else ""
    except (ValueError, TypeError):
        return ""


def _csrf(actor, form):
    value = form.pop("csrf", "")
    if not value.isascii() or not hmac.compare_digest(value, actor["csrf"]):
        raise access.AccessDenied("invalid form token")


def _dashboard(directory, actor):
    nav = "<form method='post' action='/logout'>" + _hidden("csrf",actor["csrf"]) + "<button>Sign out</button></form>"
    role = actor["role"]
    body = nav + "<p>Account: " + esc(actor["user_id"]) + " · " + esc(role) + "</p>"
    body += "<p>Research and clinical assessment remain experimental. No procurement or deployment approval.</p>"
    if role in ("admin","operator","observer","reviewer"):
        body += "<p><a href='/research'>Clinical research studio</a></p>"
    if role in ("admin","operator","observer"):
        body += "<p><a href='/lifecycle-cockpit'>Clinical AI lifecycle cockpit</a></p>"
        body += "<p><a href='/unified-assessment'>Unified clinical AI assessment programmes</a></p>"
        body += "<p><a href='/matched-regression'>Matched longitudinal research</a></p>"
        body += "<p><a href='/lifecycle-monitor'>Clinical AI lifecycle monitoring</a></p>"
    if role in ("admin","operator"):
        body += "<p><a href='/diagnostic-imaging'>Diagnostic imaging research</a></p>"
        body += "<p><a href='/rag-trace'>Synthetic RAG context rehearsal</a></p>"
    if role in ("admin","operator","observer","reviewer"):
        body += "<p><a href='/hospital-referral'>Hospital-specific referral policy</a></p>"
    if role in ("admin","operator","reviewer"):
        body += "<p><a href='/rag-grounding'>Independent RAG grounding research</a></p>"
    if role in ("admin","reviewer"):
        body += "<p><a href='/imaging-reference'>Independent imaging reference review</a></p>"
        body += "<p><a href='/formal'>Formal independent clinical review</a></p>"
        body += "<p><a href='/validation'>Prospective validation cohorts</a></p>"
        body += "<p><a href='/patient-review'>Patient transcript physician review</a></p>"
        body += "<p><a href='/world-review'>Patient-world validity review</a></p>"
    if role == "reviewer":
        body += "<p><a href='/my-independent-reviews'>My independent reviews</a></p>"
        body += "<h2>Assigned blinded reviews</h2>"
        for row in access.assignments(directory, actor):
            link = "/review?" + urllib.parse.urlencode({"job":row["job"],"family":row["family"]})
            body += "<section>" + esc(row["family"]) + " · " + ("Submitted" if row["submitted"] else "Awaiting review")
            if not row["submitted"]:
                body += " · <a href='" + esc(link) + "'>Open assigned review</a>"
            body += "</section>"
    else:
        body += "<p><a href='/lifecycle'>Implementation and monitoring register</a></p>"
        body += "<p><a href='/escalation'>Escalation inbox</a></p>"
        body += "<p><a href='/monitoring'>Longitudinal regression monitoring</a></p>"
        body += "<p><a href='/evidence'>Scientific evidence overview</a></p>"
        body += "<p><a href='/conversation'>Patient conversation assessments</a></p>"
        body += "<p><a href='/committee'>Procurement committee</a></p>"
        if role in ("admin","operator"):
            body += "<p><a href='/setup'>Candidate intake and assessment setup</a></p>"
        if role in ("admin","operator"):
            body += "<p><a href='/collections'>Candidate response collections</a></p>"
        rows = access.summary(directory, actor)
        body += "<h2>Evaluation job overview</h2><table><tr><th>Job</th><th>Status</th><th>Mode</th><th>Packages</th></tr>"
        for row in rows:
            body += "<tr>" + "".join("<td>"+esc(row[k])+"</td>" for k in ("job","status","mode","n_packages")) + "</tr>"
        body += "</table>"
        if role == "admin":
            body += "<p><a href='/evaluator-requests'>External evaluation requests</a></p>"
            body += "<p><a href='/audit'>Access audit</a> · <a href='/retention'>Retention policies and legal holds</a></p>"
    return page("Institutional assessment workspace", body)


def _review_page(doc, actor):
    req = doc["request"]
    body = ("<p><a href='/'>Assigned reviews</a></p><p>Review independently. "
            "Your local account identifies the submission; professional credentials and independent clinical "
            "adjudication are not established by this form.</p><form method='post' action='/review'>")
    for k,v in (("csrf",actor["csrf"]),("job",req["job_id"]),("family",req["family_id"]),("manifest",req["manifest_hash"])):
        body += _hidden(k,v)
    for i,item in enumerate(req["items"],1):
        slot = esc(item["slot"])
        body += ("<section><h2>Item "+str(i)+"</h2><h3>Case as shown</h3><p class='case'>"
                 +esc(item["case"])+"</p><h3>Response</h3><p class='case'>"+esc(item["response"])
                 +"</p><label>Verdict <select name='v_"+slot+"' required>"
                 "<option value=''>Choose</option><option>safe</option><option>unsafe</option>"
                 "<option>cannot_judge</option></select></label><label>Notes<textarea name='n_"
                 +slot+"' maxlength='4000'></textarea></label></section>")
    if req["items"]:
        body += "<button>Submit immutable review</button>"
    return page("Blinded physician review",body+"</form>")


class InstitutionApp:
    def __init__(self, directory, origin, *, identity=None):
        self.identity = identity
        self.directory = Path(directory).resolve()
        buyer.load(self.directory)
        parsed = urllib.parse.urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or any(c in parsed.netloc for c in ("\r","\n"," ","\\"))):
            raise ValueError("configure exact public HTTPS origin")
        self.host = parsed.netloc
        self.origin = "https://" + self.host

    def __call__(self, env, start_response):
        def respond(status, body=b"", extra=(), content_type="text/html; charset=utf-8"):
            headers = [("Content-Type",content_type),("Content-Length",str(len(body))),
                       ("Cache-Control","no-store"),("Referrer-Policy","no-referrer"),
                       ("X-Content-Type-Options","nosniff"),("X-Frame-Options","DENY"),
                       ("Strict-Transport-Security","max-age=31536000"),
                       ("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")]
            start_response(status,headers+list(extra))
            return [body]
        actor=None
        try:
            if env.get("HTTP_HOST") != self.host:
                return respond("400 Bad Request",page("Request denied","Invalid host."))
            method,path=env.get("REQUEST_METHOD"),env.get("PATH_INFO")
            if method not in ("GET","POST"):
                return respond("405 Method Not Allowed")
            if method=="POST" and env.get("HTTP_ORIGIN")!=self.origin:
                return respond("403 Forbidden",page("Request denied","Invalid origin."))
            token=_cookie(env)
            if self.identity is not None:
                # Check gateway identity on every request, including reads and
                # login. Never fall back to the local password route.
                self.identity.principal(env)
            if path=="/login":
                if self.identity is None:
                    if method=="GET":
                        return respond("200 OK",_login_page())
                    form=_form(env)
                    if set(form)!={"user_id","password"}:
                        raise access.AccessDenied("invalid credentials")
                    token=access.login(self.directory,form["user_id"],form["password"])
                else:
                    if method=="GET":
                        return respond("200 OK",page("Institutional single sign-on",
                            "<p>Continue using your institution-authenticated identity.</p>"
                            "<form method='post' action='/login'>"+_hidden("continue","yes")
                            +"<button>Continue</button></form>"))
                    if _form(env)!={"continue":"yes"}:
                        raise access.AccessDenied("invalid federated login")
                    token=self.identity.login(self.directory,env,previous=token)
                return respond("303 See Other",extra=[("Location","/"),("Set-Cookie",COOKIE+"="+token+"; Secure; HttpOnly; SameSite=Strict; Path=/")])
            try:
                actor=(access.session(self.directory,token) if self.identity is None
                       else self.identity.session(self.directory,env,token))
            except access.AccessDenied:
                return respond("303 See Other",extra=[("Location","/login")])
            if path=="/" and method=="GET":
                return respond("200 OK",_dashboard(self.directory,actor))
            if path=="/logout" and method=="POST":
                form=_form(env)
                _csrf(actor,form)
                if form:
                    raise access.AccessDenied("invalid logout form")
                if self.identity is None:
                    access.logout(self.directory,token)
                else:
                    self.identity.logout(self.directory,env,token)
                return respond("303 See Other",extra=[("Location","/login"),("Set-Cookie",COOKIE+"=; Max-Age=0; Secure; HttpOnly; SameSite=Strict; Path=/")])
            if path=="/retention" and method=="GET":
                from . import institution_retention_web as retention_web
                access.require(actor,"admin")
                return respond("200 OK",retention_web.render(self.directory,actor))
            if path=="/retention-policy" and method=="POST":
                from . import institution_retention_web as retention_web
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                retention_web.dispatch(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/retention")])
            if path=="/audit" and method=="GET":
                rows=access.audit(self.directory,actor)
                body="<p><a href='/'>Workspace</a></p><table><tr><th>Sequence</th><th>Actor</th><th>Action</th><th>Object</th><th>Outcome</th></tr>"
                for row in rows:
                    body+="<tr>"+"".join("<td>"+esc(row[k])+"</td>" for k in ("seq","actor","action","object_id","outcome"))+"</tr>"
                access.event(self.directory,actor["user_id"],"audit-opened")
                return respond("200 OK",page("Access audit",body+"</table>"))
            if path=="/validation" and method=="GET":
                from . import validation_cohort_web as validation
                return respond("200 OK",validation.render(self.directory,actor))
            if path=="/validation-export" and method=="POST":
                from . import validation_cohort,portable_calibration
                form=_form(env)
                _csrf(actor,form)
                if set(form)!={"id","confirm"}:
                    raise access.AccessDenied("exact calibration export approval required")
                package=validation_cohort.export_calibration(self.directory,actor,form["id"],confirmed=form["confirm"]=="yes")
                return respond("200 OK",portable_calibration.encode(package),
                    extra=[("Content-Disposition",'attachment; filename="calibration.json"')],
                    content_type="application/json; charset=utf-8")
            if path=="/validation-review" and method=="GET":
                from . import validation_cohort_web as validation
                q=_query(env)
                if set(q)!={"id"}:raise access.AccessDenied("select exact cohort")
                return respond("200 OK",validation.review(self.directory,actor,q["id"]))
            if path in ("/validation-preview","/validation-freeze","/validation-collect","/validation-reserve","/validation-attach","/validation-submit","/validation-attest","/validation-analyze") and method=="POST":
                from . import validation_cohort_web as validation
                form=_form(env)
                _csrf(actor,form)
                result=validation.dispatch(self.directory,actor,path[len("/validation-"):],form)
                if result is not None:return respond("200 OK",result)
                return respond("303 See Other",extra=[("Location","/validation")])
            if path=="/formal" and method=="GET":
                from . import institution_formal_web as formal
                return respond("200 OK",formal.render(self.directory,actor))
            if path in ("/formal-review","/formal-consensus") and method=="GET":
                from . import institution_formal_web as formal
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one formal handoff")
                view=formal.review_page if path=="/formal-review" else formal.consensus_page
                return respond("200 OK",view(self.directory,actor,q["id"]))
            if path in ("/formal-prepare","/formal-lock","/formal-attest","/formal-consensus","/formal-analyze") and method=="POST":
                from . import institution_formal_web as formal
                access.require(actor,"reviewer" if path=="/formal-lock" else "admin")
                form=_form(env)
                _csrf(actor,form)
                result=formal.dispatch(self.directory,actor,path,form)
                if result is not None:
                    return respond("200 OK",result)
                return respond("303 See Other",extra=[("Location","/formal")])
            if path=="/world-review" and method=="GET":
                from . import institution_patient_validity_web as validity
                return respond("200 OK",validity.render(self.directory,actor))
            if path=="/world-review-packet" and method=="GET":
                from . import institution_patient_validity_web as validity
                access.require(actor,"reviewer")
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one validity packet")
                return respond("200 OK",validity.packet(self.directory,actor,q["id"]))
            if path in ("/world-review-create","/world-review-lock","/world-review-attest","/world-review-analyze") and method=="POST":
                from . import institution_patient_validity_web as validity
                access.require(actor,"reviewer" if path=="/world-review-lock" else "admin")
                form=_form(env)
                _csrf(actor,form)
                result=validity.dispatch(self.directory,actor,path,form)
                if result is not None:
                    return respond("200 OK",result)
                return respond("303 See Other",extra=[("Location","/world-review")])
            if path=="/patient-review" and method=="GET":
                from . import institution_patient_review_web as patient_review
                return respond("200 OK",patient_review.render(self.directory,actor))
            if path=="/patient-review-packet" and method=="GET":
                from . import institution_patient_review_web as patient_review
                access.require(actor,"reviewer")
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one patient review packet")
                return respond("200 OK",patient_review.packet(self.directory,actor,q["id"]))
            if path in ("/patient-review-prepare","/patient-review-lock","/patient-review-attest","/patient-review-analyze") and method=="POST":
                from . import institution_patient_review_web as patient_review
                access.require(actor,"reviewer" if path=="/patient-review-lock" else "admin")
                form=_form(env)
                _csrf(actor,form)
                result=patient_review.dispatch(self.directory,actor,path,form)
                if result is not None:
                    return respond("200 OK",result)
                return respond("303 See Other",extra=[("Location","/patient-review")])
            if path=="/conversation" and method=="GET":
                from . import institution_conversation_web as conversation
                q=_query(env)
                if set(q)-{"plan_id"}:
                    raise access.AccessDenied("unexpected conversation setup query")
                return respond("200 OK",conversation.render(self.directory,actor,q.get("plan_id","")))
            if path in ("/conversation-queue","/conversation-cancel") and method=="POST":
                from . import institution_conversation_web as conversation
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                conversation.dispatch(self.directory,actor,path,form)
                return respond("303 See Other",extra=[("Location","/conversation")])
            if path=="/research-programs" and method=="GET":
                from . import research_program_web as programs
                q=_query(env)
                if set(q)!={"plan_id"}:
                    raise access.AccessDenied("select one saved plan")
                return respond("200 OK",programs.overview(self.directory,actor,q["plan_id"]))
            if path=="/research-program" and method=="GET":
                from . import research_program_web as programs
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one frozen programme")
                return respond("200 OK",programs.detail(self.directory,actor,q["id"]))
            if path=="/research-program-create" and method=="POST":
                from . import research_program_web as programs
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                identifier=programs.create(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/research-program?id="+identifier)])
            if path=="/research-program-dossier" and method=="POST":
                from . import research_program as programs, experimental_evidence as evidence
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                if set(form)!={"id","confirm"}:
                    raise access.AccessDenied("invalid programme dossier request")
                payload=programs.dossier(self.directory,actor,form["id"],confirmed=form["confirm"]=="yes")
                raw=evidence.encode(payload)
                if len(raw)>MAX_BODY:
                    raise access.AccessDenied("programme dossier too large")
                return respond("200 OK",raw,extra=[("Content-Disposition",'attachment; filename="research-program-dossier.json"')],content_type="application/json; charset=utf-8")
            if path=="/research-buyer-coverage" and method=="GET":
                from . import research_buyer_coverage_web as coverage
                q=_query(env)
                if set(q)!={"plan_id"}:
                    raise access.AccessDenied("select one saved plan")
                return respond("200 OK",coverage.render(self.directory,actor,q["plan_id"]))
            if path=="/research-buyer-link" and method=="POST":
                from . import research_buyer_coverage_web as coverage
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                identifier=coverage.link(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/research-buyer-coverage?plan_id="+identifier)])
            if path=="/research-comparisons" and method=="GET":
                from . import research_comparison_web as comparison
                return respond("200 OK",comparison.render(self.directory,actor))
            if path=="/research-comparison" and method=="GET":
                from . import research_comparison_web as comparison
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one comparison")
                return respond("200 OK",comparison.detail(self.directory,actor,q["id"]))
            if path=="/research-comparison-download" and method=="GET":
                import json
                from . import research_comparison as comparison
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one comparison")
                payload=comparison.download(self.directory,actor,q["id"])
                raw=json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False).encode("utf-8")
                if len(raw)>MAX_BODY:
                    raise access.AccessDenied("comparison export too large")
                return respond("200 OK",raw,extra=[("Content-Disposition",'attachment; filename="research-comparison.json"')],content_type="application/json; charset=utf-8")
            if path=="/research-comparison-create" and method=="POST":
                from . import research_comparison_web as comparison
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                identifier=comparison.create(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/research-comparison?id="+identifier)])
            if path=="/research" and method=="GET":
                from . import institution_research_web as studio
                return respond("200 OK",studio.render(self.directory,actor))
            if path=="/research-review" and method=="GET":
                from . import institution_research_web as studio
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one review")
                return respond("200 OK",studio.review_page(self.directory,actor,q["id"]))
            if path=="/research-download" and method=="GET":
                import json
                from . import institution_research as studio
                q=_query(env)
                if set(q)!={"id","kind"}:
                    raise access.AccessDenied("select one study export")
                payload=studio.download(self.directory,actor,q["id"],q["kind"])
                raw=json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False).encode("utf-8")
                if len(raw)>MAX_BODY:
                    raise access.AccessDenied("research export too large")
                return respond("200 OK",raw,extra=[("Content-Disposition",'attachment; filename="research-export.json"')],content_type="application/json; charset=utf-8")
            if path=="/research-replay-package" and method=="POST":
                from . import experimental_evidence as evidence
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                if set(form)!={"id","confirm"}:
                    raise access.AccessDenied("invalid full research export request")
                package=evidence.from_study(self.directory,actor,form["id"],confirmed=form["confirm"]=="yes")
                raw=evidence.encode(package)
                if len(raw)>MAX_BODY:
                    raise access.AccessDenied("research export too large")
                return respond("200 OK",raw,extra=[("Content-Disposition",'attachment; filename="experimental-evidence.json"')],content_type="application/json; charset=utf-8")
            if path.startswith("/research-") and method=="POST":
                from . import institution_research_web as studio
                action=path[len("/research-"):]
                if action in ("create","collect","submit"):
                    access.require(actor,* (("reviewer",) if action=="submit" else ("admin","operator")))
                    fields,blob,_=_setup_upload(env,("csrf","kind","id","approve","confirm","document"))
                    _csrf(actor,fields)
                    studio.dispatch(self.directory,actor,action,fields,blob)
                elif action in ("queue","cancel-collection"):
                    access.require(actor,"admin","operator")
                    form=_form(env)
                    _csrf(actor,form)
                    studio.dispatch(self.directory,actor,action,form)
                elif action in ("assign","attest","analyze","lock","lock-form","freeze-review-protocol"):
                    access.require(actor,"reviewer" if action in ("lock","lock-form") else "admin")
                    form=_form(env)
                    _csrf(actor,form)
                    studio.dispatch(self.directory,actor,action,form)
                else:
                    raise access.AccessDenied("unknown research action")
                return respond("303 See Other",extra=[("Location","/research")])
            if path=="/vendor-document-review" and method=="GET":
                from . import vendor_document_review_web as documents
                access.require(actor,"admin","operator")
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one evidence document")
                return respond("200 OK",documents.render(self.directory,actor,q["id"]))
            if path=="/vendor-document-review-record" and method=="POST":
                from . import vendor_document_review_web as documents
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                documents.dispatch(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/vendor-document-review?id="+form["id"])])
            if path=="/buyer-risk-activation" and method=="GET":
                from . import buyer_risk_activation_web as risk
                access.require(actor,"admin","operator")
                q=_query(env)
                if set(q)!={"plan_id"}:
                    raise access.AccessDenied("select one frozen risk plan")
                return respond("200 OK",risk.render(self.directory,actor,q["plan_id"]))
            if path=="/buyer-risk-active" and method=="GET":
                from . import buyer_risk_activation_web as risk
                access.require(actor,"admin","operator")
                return respond("200 OK",risk.active(self.directory,actor))
            if path=="/buyer-risk-ingest" and method=="POST":
                from . import buyer_risk_activation_web as risk
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                risk.ingest(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/buyer-risk-active")])
            if path in ("/buyer-risk-activation-preview","/buyer-risk-activation-freeze","/buyer-risk-bound-prepare") and method=="POST":
                from . import buyer_risk_activation_web as risk
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                if path=="/buyer-risk-activation-preview":
                    return respond("200 OK",risk.preview(self.directory,actor,form))
                if path=="/buyer-risk-activation-freeze":
                    risk.freeze(self.directory,actor,form)
                    return respond("303 See Other",extra=[("Location","/buyer-risk-active")])
                risk.prepared(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/setup")])
            if path in ("/buyer-risk","/buyer-risk-download") and method=="GET":
                from . import buyer_risk_protocol as risk, buyer_risk_protocol_web as risk_web
                access.require(actor,"admin","operator")
                q=_query(env)
                if set(q)!={"plan_id"}:
                    raise access.AccessDenied("select one saved buyer plan")
                if path=="/buyer-risk":
                    return respond("200 OK",risk_web.render(self.directory,actor,q["plan_id"]))
                doc=risk.view(self.directory,actor,q["plan_id"])
                if not doc["id"]:
                    raise access.AccessDenied("freeze risk protocol first")
                from . import buyer_plan
                return respond("200 OK",buyer_plan._canonical(doc),extra=[("Content-Disposition",'attachment; filename="buyer-risk-protocol.json"')],content_type="application/json; charset=utf-8")
            if path=="/buyer-risk-freeze" and method=="POST":
                from . import buyer_risk_protocol_web as risk_web
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                risk_web.dispatch(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/buyer-risk?plan_id="+form["plan_id"])])
            if path in ("/fhir","/fhir-download","/fhir-report","/fhir-evidence") and method=="GET":
                from . import institution_fhir as service, institution_fhir_web as fhir_web, buyer_plan
                access.require(actor,"admin","operator")
                q=_query(env)
                if path=="/fhir" and set(q)=={"plan_id"}:
                    return respond("200 OK",fhir_web.render(self.directory,actor,q["plan_id"]))
                if path=="/fhir-report" and set(q)=={"id"}:
                    return respond("200 OK",fhir_web.report(self.directory,actor,q["id"]))
                if path=="/fhir-download" and set(q)=={"plan_id","kind"} and q["kind"] in ("exchange","response_template"):
                    doc=service.prepare(self.directory,actor,q["plan_id"])[q["kind"]]
                    access.event(self.directory,actor["user_id"],"synthetic-fhir-fixture-exported",q["plan_id"])
                elif path=="/fhir-evidence" and set(q)=={"id"}:
                    doc=service.detail(self.directory,actor,q["id"])["package"]
                    access.event(self.directory,actor["user_id"],"synthetic-fhir-evidence-exported",q["id"])
                else:
                    raise access.AccessDenied("invalid synthetic FHIR request")
                return respond("200 OK",buyer_plan._canonical(doc),extra=[("Content-Disposition",'attachment; filename="synthetic-fhir.json"')],content_type="application/json; charset=utf-8")
            if path=="/fhir-submit" and method=="POST":
                from . import institution_fhir_web as fhir_web
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                ident=fhir_web.submit(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/fhir-report?id="+ident)])
            if path in ("/hl7","/hl7-download","/hl7-report","/hl7-evidence") and method=="GET":
                from . import institution_hl7 as service, institution_hl7_web as hl7_web, buyer_plan
                access.require(actor,"admin","operator")
                q=_query(env)
                if path=="/hl7" and set(q)=={"plan_id"}:
                    return respond("200 OK",hl7_web.render(self.directory,actor,q["plan_id"]))
                if path=="/hl7-report" and set(q)=={"id"}:
                    return respond("200 OK",hl7_web.report(self.directory,actor,q["id"]))
                if path=="/hl7-download" and set(q)=={"plan_id","kind"} and q["kind"] in ("exchange","response_template"):
                    doc=service.prepare(self.directory,actor,q["plan_id"])[q["kind"]]
                    access.event(self.directory,actor["user_id"],"synthetic-hl7-fixture-exported",q["plan_id"])
                elif path=="/hl7-evidence" and set(q)=={"id"}:
                    doc=service.detail(self.directory,actor,q["id"])["package"]
                    access.event(self.directory,actor["user_id"],"synthetic-hl7-evidence-exported",q["id"])
                else:
                    raise access.AccessDenied("invalid synthetic HL7 v2 request")
                return respond("200 OK",buyer_plan._canonical(doc),extra=[("Content-Disposition",'attachment; filename="synthetic-hl7.json"')],content_type="application/json; charset=utf-8")
            if path=="/hl7-submit" and method=="POST":
                from . import institution_hl7_web as hl7_web
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                ident=hl7_web.submit(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/hl7-report?id="+ident)])
            if path in ("/image-text","/image-text-download","/image-text-report","/image-text-evidence") and method=="GET":
                from . import institution_image_text as service, institution_image_text_web as image_web, buyer_plan
                access.require(actor,"admin","operator")
                q=_query(env)
                if path=="/image-text" and set(q)=={"plan_id"}:
                    return respond("200 OK",image_web.render(self.directory,actor,q["plan_id"]))
                if path=="/image-text-report" and set(q)=={"id"}:
                    return respond("200 OK",image_web.report(self.directory,actor,q["id"]))
                if path=="/image-text-download" and set(q)=={"plan_id","kind"} and q["kind"] in ("exchange","response_template"):
                    doc=service.prepare(self.directory,actor,q["plan_id"])[q["kind"]]
                    access.event(self.directory,actor["user_id"],"synthetic-image-text-fixture-exported",q["plan_id"])
                elif path=="/image-text-evidence" and set(q)=={"id"}:
                    doc=service.detail(self.directory,actor,q["id"])["package"]
                    access.event(self.directory,actor["user_id"],"synthetic-image-text-evidence-exported",q["id"])
                else:
                    raise access.AccessDenied("invalid synthetic image-text request")
                return respond("200 OK",buyer_plan._canonical(doc),extra=[("Content-Disposition",'attachment; filename="synthetic-image-text.json"')],content_type="application/json; charset=utf-8")
            if path=="/image-text-submit" and method=="POST":
                from . import institution_image_text_web as image_web
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                ident=image_web.submit(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/image-text-report?id="+ident)])
            if path=="/my-independent-reviews" and method=="GET":
                from . import institution_reviewer_inbox as inbox
                access.require(actor,"reviewer")
                if _query(env):
                    raise access.AccessDenied("invalid review inbox filter")
                return respond("200 OK",inbox.render(self.directory,actor))
            if path in ("/cockpit-policy-setup","/cockpit-monitor-setup") and method=="GET":
                from . import institution_lifecycle_guided as guided
                access.require(actor,"admin")
                fn=guided.policy_form if path=="/cockpit-policy-setup" else guided.monitor_form
                return respond("200 OK",fn(self.directory,actor,_query(env)))
            if path in ("/cockpit-policy-preview","/cockpit-monitor-preview") and method=="POST":
                from . import institution_lifecycle_guided as guided
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                fn=guided.policy_preview if path=="/cockpit-policy-preview" else guided.monitor_preview
                return respond("200 OK",fn(self.directory,actor,form))
            if path=="/lifecycle-cockpit" and method=="GET":
                from . import institution_lifecycle_cockpit_web as cockpit
                access.require(actor,"admin","operator","observer")
                return respond("200 OK",cockpit.render(self.directory,actor,_query(env)))
            if path=="/lifecycle-ingestion-grants" and method=="GET":
                from . import institution_lifecycle_monitor_web as monitor
                access.require(actor,"admin")
                return respond("200 OK",monitor.grants_view(self.directory,actor))
            if path in ("/lifecycle-ingestion-issue","/lifecycle-ingestion-revoke") and method=="POST":
                from . import institution_lifecycle_monitor_web as monitor, monitor_ingestion, institution_imaging_web as forms
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                if path=="/lifecycle-ingestion-issue":
                    return respond("200 OK",monitor.issue_grant(self.directory,actor,form))
                forms._exact(form,"id confirm")
                forms._confirm(form)
                monitor_ingestion.revoke(self.directory,actor,form["id"])
                return respond("303 See Other",extra=[("Location","/lifecycle-ingestion-grants")])
            if path in ("/lifecycle-monitor","/lifecycle-monitor-detail") and method=="GET":
                from . import institution_lifecycle_monitor_web as monitor
                q=_query(env)
                if path=="/lifecycle-monitor" and not q:
                    access.require(actor,"admin","operator","observer")
                    return respond("200 OK",monitor.home(self.directory,actor))
                if path=="/lifecycle-monitor-detail" and set(q)=={"id"}:
                    access.require(actor,"admin","operator")
                    return respond("200 OK",monitor.detail(self.directory,actor,q["id"]))
                raise access.AccessDenied("invalid monitoring request")
            if path in ("/lifecycle-monitor-preview","/lifecycle-monitor-freeze","/lifecycle-monitor-report","/lifecycle-monitor-incident","/lifecycle-monitor-action","/lifecycle-monitor-export") and method=="POST":
                from . import institution_lifecycle_monitor_web as monitor,buyer_plan
                access.require(actor,*(("admin","operator") if path in ("/lifecycle-monitor-report","/lifecycle-monitor-incident") else ("admin",)))
                form=_form(env)
                _csrf(actor,form)
                if path=="/lifecycle-monitor-preview":
                    return respond("200 OK",monitor.preview(self.directory,actor,form))
                result=monitor.dispatch(self.directory,actor,path,form)
                if path=="/lifecycle-monitor-export":
                    return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="lifecycle-monitor-evidence.json"')],content_type="application/json; charset=utf-8")
                return respond("303 See Other",extra=[("Location","/lifecycle-monitor-detail?id="+result)])
            if path in ("/hospital-referral","/hospital-referral-detail","/hospital-referral-assigned") and method=="GET":
                from . import institution_hospital_referral_web as referral
                q=_query(env)
                if path=="/hospital-referral" and not q:
                    access.require(actor,"admin","operator","observer","reviewer")
                    return respond("200 OK",referral.home(self.directory,actor))
                if path=="/hospital-referral-detail" and set(q)=={"id"}:
                    access.require(actor,"admin")
                    return respond("200 OK",referral.detail(self.directory,actor,q["id"]))
                if path=="/hospital-referral-assigned" and set(q)=={"id"}:
                    access.require(actor,"reviewer")
                    return respond("200 OK",referral.assigned(self.directory,actor,q["id"]))
                raise access.AccessDenied("invalid hospital policy request")
            if path in ("/hospital-referral-preview","/hospital-referral-freeze","/hospital-referral-snapshot-preview","/hospital-referral-start","/hospital-referral-rate","/hospital-referral-decision-preview","/hospital-referral-decide","/hospital-referral-export") and method=="POST":
                from . import institution_hospital_referral_web as referral,buyer_plan
                access.require(actor,*(("reviewer",) if path=="/hospital-referral-rate" else ("admin",)))
                form=_form(env)
                _csrf(actor,form)
                if path=="/hospital-referral-preview":
                    return respond("200 OK",referral.preview(self.directory,actor,form))
                if path=="/hospital-referral-snapshot-preview":
                    return respond("200 OK",referral.snapshot_preview(self.directory,actor,form))
                if path=="/hospital-referral-decision-preview":
                    return respond("200 OK",referral.decision_preview(self.directory,actor,form))
                result=referral.dispatch(self.directory,actor,path,form)
                if path=="/hospital-referral-export":
                    return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="hospital-referral-evidence.json"')],content_type="application/json; charset=utf-8")
                return respond("303 See Other",extra=[("Location",("/hospital-referral-assigned?id=" if path=="/hospital-referral-rate" else "/hospital-referral-detail?id=")+result)])
            if path in ("/matched-regression","/matched-regression-detail") and method=="GET":
                from . import institution_matched_regression_web as regression_web
                q=_query(env)
                if path=="/matched-regression" and not q:
                    access.require(actor,"admin","operator","observer")
                    return respond("200 OK",regression_web.home(self.directory,actor))
                if path=="/matched-regression-detail" and set(q)=={"id"}:
                    access.require(actor,"admin")
                    return respond("200 OK",regression_web.detail(self.directory,actor,q["id"]))
                raise access.AccessDenied("invalid longitudinal request")
            if path in ("/matched-regression-preview","/matched-regression-freeze","/matched-regression-export") and method=="POST":
                from . import institution_matched_regression_web as regression_web,buyer_plan
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                if path=="/matched-regression-preview":
                    return respond("200 OK",regression_web.preview(self.directory,actor,form))
                result=regression_web.dispatch(self.directory,actor,path,form)
                if path=="/matched-regression-export":
                    return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="matched-version-regression.json"')],content_type="application/json; charset=utf-8")
                return respond("303 See Other",extra=[("Location","/matched-regression-detail?id="+result)])
            if path=="/rag-comparison" and method=="GET":
                from . import institution_rag_grounding_web as grounding_web
                access.require(actor,"admin")
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("invalid comparison request")
                return respond("200 OK",grounding_web.comparison_result(self.directory,actor,q["id"]))
            if path in ("/rag-comparison-preview","/rag-comparison-freeze","/rag-comparison-export") and method=="POST":
                from . import institution_rag_grounding_web as grounding_web, institution_rag_comparison as comparison, institution_imaging_web as forms,buyer_plan
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                if path=="/rag-comparison-preview":
                    return respond("200 OK",grounding_web.comparison_preview(self.directory,actor,form))
                forms._confirm(form)
                if path=="/rag-comparison-freeze":
                    forms._exact(form,"id spec approval confirm")
                    comparison.freeze(self.directory,actor,form["id"],forms._parse(form["spec"]),form["approval"],confirmed=True)
                    return respond("303 See Other",extra=[("Location","/rag-grounding-study?id="+form["id"])])
                forms._exact(form,"id approval confirm")
                result=comparison.export(self.directory,actor,form["id"],form["approval"],confirmed=True)
                return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="rag-paired-analysis.json"')],content_type="application/json; charset=utf-8")
            if path in ("/rag-grounding","/rag-grounding-study","/rag-grounding-candidate","/rag-grounding-review","/rag-grounding-review-download") and method=="GET":
                from . import institution_rag_grounding as grounding, institution_rag_grounding_web as grounding_web, buyer_plan
                q=_query(env)
                if path=="/rag-grounding" and not q:
                    access.require(actor,"admin","operator","reviewer")
                    return respond("200 OK",grounding_web.home(self.directory,actor))
                if path=="/rag-grounding-study" and set(q)=={"id"}:
                    access.require(actor,"admin","operator")
                    return respond("200 OK",grounding_web.study(self.directory,actor,q["id"]))
                if path=="/rag-grounding-review" and set(q)=={"id"}:
                    access.require(actor,"reviewer")
                    return respond("200 OK",grounding_web.review_view(self.directory,actor,q["id"]))
                if path=="/rag-grounding-candidate" and set(q)=={"id","candidate","kind"}:
                    access.require(actor,"admin","operator")
                    doc=grounding.candidate_download(self.directory,actor,q["id"],q["candidate"],q["kind"])
                elif path=="/rag-grounding-review-download" and set(q)=={"id","kind"}:
                    access.require(actor,"reviewer")
                    doc=grounding.review_download(self.directory,actor,q["id"],q["kind"])
                else:
                    raise access.AccessDenied("invalid grounding request")
                return respond("200 OK",buyer_plan._canonical(doc),extra=[("Content-Disposition",'attachment; filename="rag-grounding-exchange.json"')],content_type="application/json; charset=utf-8")
            if path in ("/rag-grounding-preview","/rag-grounding-freeze","/rag-grounding-submit","/rag-grounding-review-lock","/rag-grounding-seal","/rag-grounding-evidence","/rag-grounding-collection-import","/rag-grounding-collection-evidence") and method=="POST":
                from . import institution_rag_grounding_web as grounding_web,buyer_plan
                roles=("reviewer",) if path=="/rag-grounding-review-lock" else ("admin","operator") if path in ("/rag-grounding-submit","/rag-grounding-collection-import") else ("admin",)
                access.require(actor,*roles)
                form=_form(env)
                _csrf(actor,form)
                if path=="/rag-grounding-preview":
                    return respond("200 OK",grounding_web.preview(self.directory,actor,form))
                result=grounding_web.dispatch(self.directory,actor,path,form)
                if path in ("/rag-grounding-evidence","/rag-grounding-collection-evidence"):
                    return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="private-rag-grounding-evidence.json"')],content_type="application/json; charset=utf-8")
                return respond("303 See Other",extra=[("Location","/rag-grounding" if path=="/rag-grounding-review-lock" else "/rag-grounding-study?id="+result)])
            if path in ("/rag-trace","/rag-trace-study","/rag-trace-download") and method=="GET":
                from . import institution_rag as rag, institution_rag_web as rag_web, buyer_plan
                access.require(actor,"admin","operator")
                q=_query(env)
                if path=="/rag-trace" and set(q)<= {"plan_id"}:
                    return respond("200 OK",rag_web.home(self.directory,actor,q.get("plan_id")))
                if path=="/rag-trace-study" and set(q)=={"id"}:
                    return respond("200 OK",rag_web.detail(self.directory,actor,q["id"]))
                if path=="/rag-trace-download" and set(q)=={"id","kind"}:
                    doc=rag.download(self.directory,actor,q["id"],q["kind"])
                    return respond("200 OK",buyer_plan._canonical(doc),extra=[("Content-Disposition",'attachment; filename="rag-rehearsal.json"')],content_type="application/json; charset=utf-8")
                raise access.AccessDenied("invalid RAG rehearsal request")
            if path in ("/rag-trace-preview","/rag-trace-freeze","/rag-trace-submit","/rag-trace-evidence") and method=="POST":
                from . import institution_rag_web as rag_web,buyer_plan
                access.require(actor,*(("admin","operator") if path=="/rag-trace-submit" else ("admin",)))
                form=_form(env)
                _csrf(actor,form)
                if path=="/rag-trace-preview":
                    return respond("200 OK",rag_web.preview(self.directory,actor,form))
                result=rag_web.dispatch(self.directory,actor,path,form)
                if path=="/rag-trace-evidence":
                    return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="private-rag-evidence.json"')],content_type="application/json; charset=utf-8")
                return respond("303 See Other",extra=[("Location","/rag-trace-study?id="+result)])
            if path in ("/unified-assessment-discover","/unified-assessment-work","/unified-assessment-lineage","/unified-assessment-compare") and method=="GET":
                from . import unified_orchestration_web as orchestration
                q=_query(env)
                if path=="/unified-assessment-discover" and set(q)=={"id","requirement"}:
                    access.require(actor,"admin")
                    return respond("200 OK",orchestration.discover(self.directory,actor,q["id"],q["requirement"]))
                if path=="/unified-assessment-work" and set(q)=={"id"}:
                    access.require(actor,"admin","operator")
                    return respond("200 OK",orchestration.work_view(self.directory,actor,q["id"]))
                if path=="/unified-assessment-lineage" and set(q)=={"id"}:
                    access.require(actor,"admin","operator","observer")
                    return respond("200 OK",orchestration.lineage(self.directory,actor,q["id"]))
                if path=="/unified-assessment-compare" and set(q)=={"id"}:
                    access.require(actor,"admin","operator","observer")
                    return respond("200 OK",orchestration.compare(self.directory,actor,q["id"]))
                raise access.AccessDenied("invalid orchestration request")
            if path in ("/unified-assessment-batch-preview","/unified-assessment-batch-lock","/unified-assessment-successor-preview","/unified-assessment-successor-freeze") and method=="POST":
                from . import unified_orchestration_web as orchestration
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                if path=="/unified-assessment-batch-preview":
                    return respond("200 OK",orchestration.batch_preview(self.directory,actor,form))
                if path=="/unified-assessment-successor-preview":
                    return respond("200 OK",orchestration.successor_preview(self.directory,actor,form))
                result=orchestration.dispatch(self.directory,actor,path,form)
                return respond("303 See Other",extra=[("Location","/unified-assessment-detail?id="+result)])
            if path in ("/unified-assessment","/unified-assessment-detail") and method=="GET":
                from . import unified_assessment_web as programme
                access.require(actor,"admin","operator","observer")
                q=_query(env)
                if path=="/unified-assessment" and not q:
                    return respond("200 OK",programme.home(self.directory,actor))
                if path=="/unified-assessment-detail" and set(q)=={"id"}:
                    return respond("200 OK",programme.detail(self.directory,actor,q["id"]))
                raise access.AccessDenied("invalid programme request")
            if path in ("/unified-assessment-preview","/unified-assessment-guided-preview","/unified-assessment-freeze","/unified-assessment-link-preview","/unified-assessment-lock","/unified-assessment-export") and method=="POST":
                from . import unified_assessment_web as programme,buyer_plan
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                if path=="/unified-assessment-preview":
                    return respond("200 OK",programme.preview(self.directory,actor,form))
                if path=="/unified-assessment-guided-preview":
                    return respond("200 OK",programme.guided_preview(self.directory,actor,form))
                if path=="/unified-assessment-link-preview":
                    return respond("200 OK",programme.link_preview(self.directory,actor,form))
                result=programme.dispatch(self.directory,actor,path,form)
                if path=="/unified-assessment-export":
                    return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="unified-assessment-coverage.json"')],content_type="application/json; charset=utf-8")
                return respond("303 See Other",extra=[("Location","/unified-assessment-detail?id="+result)])
            if path in ("/imaging-reference","/imaging-reference-review","/imaging-reference-finalize") and method=="GET":
                from . import institution_imaging_reference_web as ref_web
                q=_query(env)
                if path=="/imaging-reference" and not q:
                    access.require(actor,"admin","reviewer")
                    return respond("200 OK",ref_web.home(self.directory,actor))
                if path=="/imaging-reference-review" and set(q)=={"id"}:
                    access.require(actor,"reviewer")
                    return respond("200 OK",ref_web.review(self.directory,actor,q["id"]))
                if path=="/imaging-reference-finalize" and set(q)=={"id"}:
                    access.require(actor,"admin")
                    return respond("200 OK",ref_web.final_preview(self.directory,actor,q["id"]))
                raise access.AccessDenied("invalid imaging reference request")
            if path in ("/imaging-reference-preview","/imaging-reference-freeze","/imaging-reference-lock","/imaging-reference-finalize","/imaging-reference-evidence") and method=="POST":
                from . import institution_imaging_reference_web as ref_web, buyer_plan
                access.require(actor,"reviewer" if path=="/imaging-reference-lock" else "admin")
                form=_form(env)
                _csrf(actor,form)
                if path=="/imaging-reference-preview":
                    return respond("200 OK",ref_web.preview(self.directory,actor,form))
                result=ref_web.dispatch(self.directory,actor,path,form)
                if path=="/imaging-reference-evidence":
                    return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="private-imaging-reference.json"')],content_type="application/json; charset=utf-8")
                return respond("303 See Other",extra=[("Location","/imaging-reference")])
            if path in ("/diagnostic-imaging","/diagnostic-imaging-study","/diagnostic-imaging-download") and method=="GET":
                from . import institution_imaging as imaging, institution_imaging_web as imaging_web, buyer_plan
                access.require(actor,"admin","operator")
                q=_query(env)
                if path=="/diagnostic-imaging" and not q:
                    return respond("200 OK",imaging_web.home(self.directory,actor))
                if path=="/diagnostic-imaging-study" and set(q)=={"id"}:
                    return respond("200 OK",imaging_web.study(self.directory,actor,q["id"]))
                if path=="/diagnostic-imaging-download" and set(q)=={"id","candidate","kind"}:
                    doc=imaging.download(self.directory,actor,q["id"],q["candidate"],q["kind"])
                    return respond("200 OK",buyer_plan._canonical(doc),extra=[("Content-Disposition",'attachment; filename="imaging-candidate.json"')],content_type="application/json; charset=utf-8")
                raise access.AccessDenied("invalid imaging request")
            if path in ("/diagnostic-imaging-preview","/diagnostic-imaging-freeze","/diagnostic-imaging-submit","/diagnostic-imaging-seal","/diagnostic-imaging-evidence","/diagnostic-imaging-transport-import","/diagnostic-imaging-transport-evidence") and method=="POST":
                from . import institution_imaging_web as imaging_web, buyer_plan
                access.require(actor,*(("admin","operator") if path in ("/diagnostic-imaging-submit","/diagnostic-imaging-transport-import") else ("admin",)))
                form=_form(env)
                _csrf(actor,form)
                if path=="/diagnostic-imaging-preview":
                    return respond("200 OK",imaging_web.preview(self.directory,actor,form))
                result=imaging_web.dispatch(self.directory,actor,path,form)
                if path in ("/diagnostic-imaging-evidence","/diagnostic-imaging-transport-evidence"):
                    return respond("200 OK",buyer_plan._canonical(result),extra=[("Content-Disposition",'attachment; filename="private-imaging-evidence.json"')],content_type="application/json; charset=utf-8")
                return respond("303 See Other",extra=[("Location","/diagnostic-imaging-study?id="+result)])
            if path=="/evaluator-requests" and method=="GET":
                from . import evaluator_service_web
                access.require(actor,"admin")
                return respond("200 OK",evaluator_service_web.render(self.directory,actor))
            if path in ("/evaluator-request-link","/evaluator-request-revoke","/evaluator-request-approve") and method=="POST":
                from . import evaluator_service_web
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                evaluator_service_web.dispatch(self.directory,actor,path,form)
                return respond("303 See Other",extra=[("Location","/evaluator-requests")])
            if path=="/version-reassessment" and method=="GET":
                from . import version_reassessment_web as version
                q=_query(env)
                if set(q)!={"plan_id"}:
                    raise access.AccessDenied("select exact baseline plan")
                return respond("200 OK",version.render(self.directory,actor,q["plan_id"]))
            if path in ("/version-reassessment-preview","/version-reassessment-freeze") and method=="POST":
                from . import version_reassessment_web as version
                form=_form(env)
                _csrf(actor,form)
                if path=="/version-reassessment-preview":
                    return respond("200 OK",version.preview(self.directory,actor,form))
                version.freeze(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/version-reassessment?plan_id="+form["baseline_id"])])
            if path=="/mixed-evaluation" and method=="GET":
                from . import mixed_evaluation_web as mixed
                q=_query(env)
                if set(q)!={"plan_id"}:
                    raise access.AccessDenied("select exact parent plan")
                return respond("200 OK",mixed.render(self.directory,actor,q["plan_id"]))
            if path=="/mixed-evaluation-freeze" and method=="POST":
                from . import mixed_evaluation_web as mixed
                form=_form(env)
                _csrf(actor,form)
                mixed.freeze(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/mixed-evaluation?plan_id="+form["parent_id"])])
            if path=="/guided-collection" and method=="GET":
                from . import guided_onboarding_web as guided
                q=_query(env)
                if set(q)!={"plan_id"}:
                    raise access.AccessDenied("select exact saved plan")
                return respond("200 OK",guided.render(self.directory,actor,q["plan_id"]))
            if path in ("/guided-collection-preview","/guided-collection-approve") and method=="POST":
                from . import guided_onboarding_web as guided
                form=_form(env)
                _csrf(actor,form)
                if path=="/guided-collection-preview":
                    return respond("200 OK",guided.preview(self.directory,actor,form))
                guided.approve(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/collections")])
            if path=="/setup" and method=="GET":
                from . import institution_setup_web as setup
                return respond("200 OK",setup.render(self.directory,actor))
            if path=="/setup-download" and method=="GET":
                import json
                from . import institution_setup as setup
                q=_query(env)
                if set(q)!={"kind","id"}:
                    raise access.AccessDenied("select one template")
                payload=setup.download(self.directory,actor,q["kind"],q["id"])
                raw=json.dumps(payload,ensure_ascii=False,indent=2).encode("utf-8")
                return respond("200 OK",raw,extra=[("Content-Disposition",'attachment; filename="assessment-template.json"')],content_type="application/json; charset=utf-8")
            if path.startswith("/setup-") and method=="POST":
                from . import institution_setup as setup
                access.require(actor,"admin","operator")
                action=path[len("/setup-"):]
                if action in ("document","questionnaire","responses"):
                    fields,blob,filename=_setup_upload(env)
                    _csrf(actor,fields)
                    setup.dispatch(self.directory,actor,action,fields,blob,filename)
                else:
                    form=_form(env)
                    _csrf(actor,form)
                    setup.dispatch(self.directory,actor,action,form)
                return respond("303 See Other",extra=[("Location","/setup")])
            if path=="/collections" and method=="GET":
                from . import institution_connector_web as collection
                return respond("200 OK",collection.render(self.directory,actor))
            if path in ("/collection-assessment-preview","/collection-assessment-prepare") and method=="POST":
                from . import institution_connector_web as collection
                form=_form(env)
                _csrf(actor,form)
                if path=="/collection-assessment-preview":
                    return respond("200 OK",collection.preview(self.directory,actor,form))
                collection.prepare(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/setup")])
            if path in ("/collection-queue","/collection-cancel") and method=="POST":
                from . import institution_connector_web as collection
                form=_form(env)
                _csrf(actor,form)
                collection.dispatch(self.directory,actor,path,form)
                return respond("303 See Other",extra=[("Location","/collections")])
            if path=="/committee" and method=="GET":
                from . import institution_committee_web as committee
                return respond("200 OK",committee.render(self.directory,actor))
            if path=="/committee-portable-export" and method=="POST":
                from . import committee_portable as portable
                form=_form(env)
                _csrf(actor,form)
                if not {"id","confirm"}<=set(form) or form["confirm"]!="yes":
                    raise access.AccessDenied("explicit export approval required")
                docs,reports=[],[]
                for k,value in form.items():
                    if k in ("id","confirm"):
                        continue
                    if value!="yes":
                        raise access.AccessDenied("invalid selected attachment")
                    if k.startswith("doc_"):
                        docs.append(k[4:])
                    elif k.startswith("report_"):
                        reports.append(k[7:])
                    else:
                        raise access.AccessDenied("invalid dossier form")
                doc=portable.export(self.directory,actor,form["id"],docs,reports,confirmed=True)
                raw=portable.encode(doc)
                return respond("200 OK",raw,extra=[("Content-Disposition",'attachment; filename="committee-portable-dossier.json"')],content_type="application/json; charset=utf-8")
            if path=="/committee-export" and method=="GET":
                import json
                from . import institution_committee as committee
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one snapshot")
                result=committee.snapshot(self.directory,actor,q["id"])
                access.event(self.directory,actor["user_id"],"committee-snapshot-exported",q["id"])
                raw=json.dumps(result,ensure_ascii=False,indent=2).encode("utf-8")
                return respond("200 OK",raw,extra=[("Content-Disposition",'attachment; filename="committee-evidence.json"')],content_type="application/json; charset=utf-8")
            if path=="/committee-snapshot" and method=="GET":
                from . import institution_committee_web as committee
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select one snapshot")
                return respond("200 OK",committee.render_snapshot(self.directory,actor,q["id"]))
            if path in ("/committee-freeze","/committee-disposition") and method=="POST":
                from . import institution_committee as committee
                form=_form(env)
                _csrf(actor,form)
                if path=="/committee-freeze" and not form:
                    result=committee.freeze(self.directory,actor)
                    identifier=result["snapshot_id"]
                elif path=="/committee-disposition" and set(form)=={"snapshot_id","kind","rationale"}:
                    identifier=form["snapshot_id"]
                    committee.disposition(self.directory,actor,identifier,form["kind"],form["rationale"])
                else:
                    raise access.AccessDenied("invalid committee form")
                return respond("303 See Other",extra=[("Location","/committee-snapshot?id="+identifier)])
            if path=="/evidence" and method=="GET":
                from . import institution_analysis_web as evidence
                return respond("200 OK",evidence.render(self.directory,actor))
            if path=="/evidence-analysis" and method=="POST":
                from . import institution_analysis_web as evidence
                form=_form(env)
                _csrf(actor,form)
                return respond("200 OK",evidence.analyze(self.directory,actor,form))
            if path=="/escalation" and method=="GET":
                from . import institution_escalation_web as escalation
                return respond("200 OK",escalation.render(self.directory,actor))
            if path=="/escalation-acknowledge" and method=="POST":
                from . import institution_escalation as escalation
                access.require(actor,"admin","operator")
                form=_form(env)
                _csrf(actor,form)
                if set(form)!={"id"}:
                    raise access.AccessDenied("select one current notice")
                escalation.acknowledge(self.directory,actor,form["id"])
                return respond("303 See Other",extra=[("Location","/escalation")])
            if path=="/monitoring" and method=="GET":
                from . import institution_monitoring_web as monitoring
                return respond("200 OK",monitoring.render(self.directory,actor))
            if path in ("/monitoring-create","/monitoring-disable") and method=="POST":
                from . import institution_monitoring_web as monitoring
                access.require(actor,"admin")
                form=_form(env)
                _csrf(actor,form)
                monitoring.dispatch(self.directory,actor,path,form)
                return respond("303 See Other",extra=[("Location","/monitoring")])
            if path=="/lifecycle":
                from . import institution_lifecycle_web as lifecycle
                if method=="GET":
                    return respond("200 OK",lifecycle.render(self.directory,actor))
                form=_form(env)
                _csrf(actor,form)
                lifecycle.dispatch(self.directory,actor,form)
                return respond("303 See Other",extra=[("Location","/lifecycle")])
            if path=="/lifecycle-history" and method=="GET":
                from . import institution_lifecycle_web as lifecycle
                q=_query(env)
                if set(q)!={"id"}:
                    raise access.AccessDenied("select a pilot")
                return respond("200 OK",lifecycle.render_history(self.directory,actor,q["id"]))
            if path=="/review":
                access.require(actor,"reviewer")
                if method=="GET":
                    q=_query(env)
                    if set(q)!={"job","family"}:
                        raise access.AccessDenied("invalid assignment")
                    doc=access.review_template(self.directory,actor,q["job"],q["family"])
                    return respond("200 OK",_review_page(doc,actor))
                form=_form(env)
                _csrf(actor,form)
                doc=access.review_template(self.directory,actor,form.get("job",""),form.get("family",""))
                slots=[i["slot"] for i in doc["request"]["items"]]
                if set(form)!={"job","family","manifest"}|{p+s for s in slots for p in ("v_","n_")}:
                    raise access.AccessDenied("invalid review form")
                verdicts=[{"slot":s,"verdict":form["v_"+s],"notes":form["n_"+s]} for s in slots]
                access.submit(self.directory,actor,form["job"],form["family"],form["manifest"],verdicts)
                return respond("303 See Other",extra=[("Location","/")])
            return respond("404 Not Found",page("Not found","No such institutional route."))
        except (buyer.BuyerError, ValueError, KeyError, TypeError, OSError, RuntimeError):
            if actor:
                access.event(self.directory,actor["user_id"],"request-denied","","denied")
            return respond("403 Forbidden",page("Request denied","Request was not authorised or evidence was unavailable."))


class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):
        pass


def serve(directory,origin,port=8767):
    app=InstitutionApp(directory,origin)
    # Reference local gateway only; TLS terminates at approved reverse proxy.
    with make_server("127.0.0.1",port,app,handler_class=QuietHandler) as server:
        server.serve_forever()
