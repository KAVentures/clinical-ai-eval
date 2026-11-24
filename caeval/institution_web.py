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


def _query(env):
    try:
        values = urllib.parse.parse_qs(env.get("QUERY_STRING", ""), max_num_fields=4,
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
    if role == "reviewer":
        body += "<h2>Assigned blinded reviews</h2>"
        for row in access.assignments(directory, actor):
            link = "/review?" + urllib.parse.urlencode({"job":row["job"],"family":row["family"]})
            body += "<section>" + esc(row["family"]) + " · " + ("Submitted" if row["submitted"] else "Awaiting review")
            if not row["submitted"]:
                body += " · <a href='" + esc(link) + "'>Open assigned review</a>"
            body += "</section>"
    else:
        body += "<p><a href='/lifecycle'>Implementation and monitoring register</a></p>"
        rows = access.summary(directory, actor)
        body += "<h2>Evaluation job overview</h2><table><tr><th>Job</th><th>Status</th><th>Mode</th><th>Packages</th></tr>"
        for row in rows:
            body += "<tr>" + "".join("<td>"+esc(row[k])+"</td>" for k in ("job","status","mode","n_packages")) + "</tr>"
        body += "</table>"
        if role == "admin":
            body += "<p><a href='/audit'>Access audit</a></p>"
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
    def __init__(self, directory, origin):
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
        def respond(status, body=b"", extra=()):
            headers = [("Content-Type","text/html; charset=utf-8"),("Content-Length",str(len(body))),
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
            if path=="/login":
                if method=="GET":
                    return respond("200 OK",_login_page())
                form=_form(env)
                if set(form)!={"user_id","password"}:
                    raise access.AccessDenied("invalid credentials")
                token=access.login(self.directory,form["user_id"],form["password"])
                return respond("303 See Other",extra=[("Location","/"),("Set-Cookie",COOKIE+"="+token+"; Secure; HttpOnly; SameSite=Strict; Path=/")])
            try:
                actor=access.session(self.directory,token)
            except access.AccessDenied:
                return respond("303 See Other",extra=[("Location","/login")])
            if path=="/" and method=="GET":
                return respond("200 OK",_dashboard(self.directory,actor))
            if path=="/logout" and method=="POST":
                form=_form(env)
                _csrf(actor,form)
                if form:
                    raise access.AccessDenied("invalid logout form")
                access.logout(self.directory,token)
                return respond("303 See Other",extra=[("Location","/login"),("Set-Cookie",COOKIE+"=; Max-Age=0; Secure; HttpOnly; SameSite=Strict; Path=/")])
            if path=="/audit" and method=="GET":
                rows=access.audit(self.directory,actor)
                body="<p><a href='/'>Workspace</a></p><table><tr><th>Sequence</th><th>Actor</th><th>Action</th><th>Object</th><th>Outcome</th></tr>"
                for row in rows:
                    body+="<tr>"+"".join("<td>"+esc(row[k])+"</td>" for k in ("seq","actor","action","object_id","outcome"))+"</tr>"
                access.event(self.directory,actor["user_id"],"audit-opened")
                return respond("200 OK",page("Access audit",body+"</table>"))
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
