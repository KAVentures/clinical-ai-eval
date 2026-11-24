"""Private, loopback-only browser for the local Clinical AI Eval buyer workspace.

Deliberately separate from the existing unauthenticated developer console.
No cloud dependency, external assets, remote requests or user tracking.
A per-process bearer token grants access to this *single-user* local interface.
This is not production hospital IAM or a multi-user service.
"""
from __future__ import annotations

import hmac
import html
import json
import os
import secrets
import tempfile
import threading
import urllib.parse
import webbrowser
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import buyer_agent as buyer

HOST = "127.0.0.1"
MAX_REQUEST_BYTES = buyer.MAX_DOCUMENT_BYTES + 128 * 1024
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
STYLE = """
:root{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
 color:#202d37;background:#f4f7f8;color-scheme:light}
*{box-sizing:border-box}body{margin:0}.shell{max-width:1150px;margin:auto;padding:28px 24px 60px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;
padding-bottom:24px;border-bottom:1px solid #d8e2e5}
h1{font-size:27px;line-height:1.2;margin:8px 0}h2{font-size:18px;margin:0 0 14px}
h3{font-size:16px;margin:0 0 10px}p{line-height:1.5}
.kicker{font-size:12px;font-weight:700;letter-spacing:.09em;color:#53747e;text-transform:uppercase}
.muted{font-size:13px;color:#667981}.grid{display:grid;grid-template-columns:2fr 1fr;gap:18px;margin-top:22px}
.panel{padding:22px;border:1px solid #dbe4e7;border-radius:12px;background:white;box-shadow:0 1px 2px #d6e2e433}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}
.metric{background:white;border:1px solid #dbe4e7;border-radius:12px;padding:17px}
.value{font-size:25px;font-weight:730;margin-top:5px}.small{font-size:12px;color:#627781}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:11px 8px;
border-bottom:1px solid #ecf0f2;vertical-align:top}th{color:#667981;font-weight:600}
input,select{display:block;width:100%;padding:10px 11px;border:1px solid #c5d5db;border-radius:7px;
background:white;font:inherit;font-size:14px;margin-top:5px;margin-bottom:13px}
label{font-size:12px;font-weight:650;color:#344d57}
button{border:0;background:#165e62;color:white;border-radius:8px;padding:11px 14px;
font-weight:650;cursor:pointer}button:hover{background:#0d474c}
.tag{display:inline-block;border-radius:5px;background:#e9f2f0;color:#215b4f;font-size:11px;
padding:5px 8px;font-weight:650}.warning{background:#fff1db;color:#806021}
.notice{padding:12px 15px;background:#f0f6f6;border-left:3px solid #377e7a;border-radius:6px;
font-size:13px;margin:20px 0}.danger{background:#fff2eb;border-left-color:#d46d4e}
.row{display:flex;justify-content:space-between;align-items:center;gap:10px}
section{margin-top:18px}.caps{white-space:nowrap}
@media(max-width:770px){.grid{grid-template-columns:1fr}.cards{grid-template-columns:1fr}
.shell{padding:15px}header{align-items:flex-start;flex-direction:column}}
"""


def esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def render_page(data: dict, message: str = "", error: str = "") -> bytes:
    vendors = data["vendors"]
    n_docs = sum(len(v["documents"]) for v in vendors)
    incomplete = sum(bool(v["missing_evidence_kinds"]) for v in vendors)
    rows = []
    for v in vendors:
        bad = sum(d["integrity"] != "intact" for d in v["documents"])
        label = ("Integrity issue" if bad else
                 "Evidence incomplete" if v["missing_evidence_kinds"] else "Documents present")
        test_state = v.get("evaluation_status", "not_configured")
        test_label = {
            "verified_package_present": str(v.get("verified_package_count", 0)) + " verified package(s)",
            "awaiting_verified_evidence": "Awaiting verified evidence",
            "not_registered_for_testing": "Not in testing study",
            "not_configured": "Testing not configured",
        }.get(test_state, "Unknown")
        style = "warning" if bad or v["missing_evidence_kinds"] else ""
        rows.append(
            "<tr><td>" + esc(v["name"]) + "<div class='small'>" + esc(v["vendor_id"]) +
            "</div></td><td>" + str(len(v["documents"])) +
            "</td><td>" + str(len(v["missing_evidence_kinds"])) +
            "</td><td><span class='tag " + style + "'>" + label + "</span></td>" +
            "<td>" + esc(test_label) + "</td></tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='5' class='muted'>No vendors added yet.</td></tr>")
    evidence_rows = []
    for v in vendors:
        for doc in v["documents"]:
            integrity = doc["integrity"]
            evidence_rows.append(
                "<tr><td>" + esc(v["name"]) + "</td><td>" +
                esc(doc["kind"].replace("_", " ")) + "</td><td>" +
                esc(doc["title"]) + "</td><td>" +
                esc(integrity) + "</td><td>" +
                esc(doc["review_status"]) + "</td></tr>"
            )
    if not evidence_rows:
        evidence_rows.append("<tr><td colspan='5' class='muted'>No documents uploaded.</td></tr>")
    options = "".join(
        "<option value='" + esc(v["vendor_id"]) + "'>" + esc(v["name"]) + "</option>"
        for v in vendors
    )
    kind_options = "".join("<option value='" + esc(k) + "'>" +
                           esc(k.replace("_", " ").title()) + "</option>"
                           for k in buyer.EVIDENCE_KINDS)
    todo = "".join("<li>" + esc(a["description"]) + "</li>" for a in data["next_actions"])
    notices = ""
    if message == "project-created":
        notices += "<div class='notice'>Private buyer workspace created on this machine.</div>"
    elif message == "vendor-added":
        notices += "<div class='notice'>Vendor registered locally.</div>"
    elif message == "evidence-added":
        notices += "<div class='notice'>Document copied into the private local evidence store.</div>"
    if error:
        notices += "<div class='notice danger'>Error: " + esc(error) + "</div>"
    assessment = data["assessment"]
    assessment_status = esc(assessment["status"].replace("_", " "))
    content = f"""<!doctype html><html lang='en'><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Clinical AI Eval — Local procurement</title><style>{STYLE}</style></head>
<body><div class='shell'><header><div>
<div class='kicker'>Clinical AI Eval · Local workspace</div>
<h1>{esc(data["project_name"])}</h1>
<div class='muted'>On-device procurement evidence register · No cloud connection</div>
</div><span class='tag'>Local session</span></header>
{notices}
<div class='notice'><b>Administrative screening only.</b> Uploaded documents are not
automatically verified for accuracy, and this view is not a clinical validation,
procurement recommendation or deployment approval.</div>
<div class='cards'><div class='metric'><div class='small'>Candidate vendors</div>
<div class='value'>{len(vendors)}</div></div>
<div class='metric'><div class='small'>Documents indexed</div>
<div class='value'>{n_docs}</div></div>
<div class='metric'><div class='small'>Vendors with evidence gaps</div>
<div class='value'>{incomplete}</div></div></div>
<div class='grid'><div><div class='panel'>
<div class='row'><h2>Vendor assessment</h2><span class='tag'>Local data</span></div>
<table><thead><tr><th>Candidate</th><th>Docs</th><th>Missing</th><th>Evidence status</th><th>Testing</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<section class='panel'><h2>Evidence inventory</h2>
<table><thead><tr><th>Vendor</th><th>Type</th><th>Document</th><th>Integrity</th><th>Review</th>
</tr></thead><tbody>{''.join(evidence_rows)}</tbody></table>
<p class='small'>Intact means the file hash matches its submitted copy.
It does not verify that the document's assertions are true.</p></section>
<section class='panel'><h2>Next steps</h2>
<ul>{todo or "<li>No automated administrative next steps recorded.</li>"}</ul>
<p class='small'>Clinical testing and verified comparative results remain in the
existing evaluation engine.</p>
<div class='row'><span>Evaluation workspace</span>
<span class='tag'>{assessment_status}</span></div>
<p><a href='/dossier' download='clinical-ai-procurement.md'>Download combined evidence dossier</a></p>
<p class='small'>Export rechecks recorded clinical packages locally and lists missing or invalid evidence.
No clinical approval is implied.</p></section>
<section class='panel'><h2>Add a vendor</h2>
<form action='/vendor-add' method='post'>
<label>Vendor identifier<input type='text' name='vendor_id' required
pattern='[a-z0-9][a-z0-9_-]{{0,63}}' placeholder='vendor_a' maxlength='64'></label>
<label>Vendor display name<input type='text' name='name' required
maxlength='200' placeholder='Candidate supplier'></label>
<button type='submit'>Register vendor</button></form></section></div>
<div><div class='panel'><h2>Add evidence</h2>
<p class='muted'>Upload a local document. It is copied into the workspace and
hashed; nothing is sent to a cloud service.</p>
<form action='/evidence-add' method='post' enctype='multipart/form-data'>
<label>Candidate<select name='vendor_id' required>{options}</select></label>
<label>Evidence category<select name='kind' required>{kind_options}</select></label>
<label>Document title<input type='text' name='title' maxlength='200'
placeholder='e.g. Validation report'></label>
<label>Local file<input type='file' name='document' required></label>
<button type='submit' {'disabled' if not vendors else ''}>Add document</button>
</form><p class='small'>Maximum file size: 20 MiB. Do not upload identifiable
patient records unless the organisation has approved the local data environment.</p>
</div><section class='panel'><h2>Assessment scope</h2>
<div class='small'>Intended use</div><p>{esc(data["intended_use"]) or "Not yet specified"}</p>
<div class='small'>Missing intake fields</div>
<p>{esc(', '.join(data["missing_intake"])) or "None"}</p>
<div class='small'>Clinical evaluation</div>
<p>{assessment_status}</p></section></div></div></div></body></html>"""
    return content.encode("utf-8")


def render_setup() -> bytes:
    """First-run wizard: create a private workspace without learning CLI syntax."""
    return (f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Clinical AI Eval — Start local assessment</title><style>{STYLE}</style></head>
<body><main class='shell' style='max-width:690px'>
<div class='kicker'>Clinical AI Eval · local setup</div>
<h1>Start a healthcare AI assessment</h1>
<p class='muted'>Create a workspace on this machine. Vendor documents and
administrative records stay here; this step uses no cloud account.</p>
<div class='panel'><h2>Organisation's assessment</h2>
<form action='/project-init' method='post'>
<label>Project name<input name='name' required maxlength='200'
placeholder='Clinical AI procurement pilot'></label>
<label>Intended use<input name='intended_use' required maxlength='1500'
placeholder='Clinician-facing decision support for emergency care'></label>
<label>Care setting<input name='setting' required maxlength='500'
placeholder='Emergency department'></label>
<label>Jurisdiction<input name='jurisdiction' required maxlength='80'
placeholder='SE / Sweden'></label>
<button type='submit'>Create private workspace</button></form></div>
<div class='notice'>This is an administrative workspace. Clinical evaluation
is experimental unless separately validated and physician-reviewed.
This browser is for one local user, not a shared hospital installation.</div>
</main></body></html>""").encode("utf-8")


def parse_upload(body: bytes, content_type: str):
    if not content_type.lower().startswith("multipart/form-data;"):
        raise buyer.BuyerError("document upload requires multipart/form-data")
    message = BytesParser(policy=policy.default).parsebytes(
        ("MIME-Version: 1.0\r\nContent-Type: " + content_type +
         "\r\n\r\n").encode("ascii") + body
    )
    if not message.is_multipart():
        raise buyer.BuyerError("invalid multipart document upload")
    fields, file_data, filename = {}, None, None
    seen = set()
    parts = list(message.iter_parts())
    if len(parts) > 8:
        raise buyer.BuyerError("too many multipart fields")
    for part in parts:
        if part.get_content_disposition() != "form-data":
            raise buyer.BuyerError("invalid multipart field")
        name = part.get_param("name", header="content-disposition")
        if name not in ("vendor_id", "kind", "title", "document") or name in seen:
            raise buyer.BuyerError("unknown or duplicate multipart field")
        seen.add(name)
        payload = part.get_payload(decode=True) or b""
        if name == "document":
            file_data = payload
            filename = part.get_filename() or ""
            if not filename.strip():
                raise buyer.BuyerError("a filename is required")
            if len(file_data) > buyer.MAX_DOCUMENT_BYTES:
                raise buyer.BuyerError("document exceeds 20 MiB limit")
        else:
            if len(payload) > 4096:
                raise buyer.BuyerError("form field exceeds limit")
            try:
                fields[name] = payload.decode("utf-8")
            except UnicodeError as exc:
                raise buyer.BuyerError("form field must be UTF-8") from exc
    if file_data is None:
        raise buyer.BuyerError("missing document file")
    return fields, file_data, filename.replace("\\", "/").rsplit("/", 1)[-1][:200]


class BuyerHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, directory, port: int = 0):
        self.directory = Path(directory).expanduser().resolve()
        # A new workspace can be created through the browser onboarding wizard.
        self.session_token = secrets.token_urlsafe(32)
        self.mutation_lock = threading.Lock()
        super().__init__((HOST, port), BuyerHandler)

    @property
    def launch_url(self):
        return f"http://{HOST}:{self.server_port}/?token={self.session_token}"


class BuyerHandler(BaseHTTPRequestHandler):
    server: BuyerHTTPServer

    def log_message(self, _format, *args):
        # Never log session tokens, document paths or clinical file metadata.
        return

    def _headers(self, status=HTTPStatus.OK, content_type="text/html; charset=utf-8",
                 extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _host_ok(self) -> bool:
        return self.headers.get("Host") == f"{HOST}:{self.server.server_port}"

    def _authenticated(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            supplied = cookie.get("caeval_local")
            return bool(supplied and hmac.compare_digest(
                supplied.value, self.server.session_token))
        except (ValueError, TypeError):
            return False

    def _error(self, status, message):
        body = ("<!doctype html><html><body><h1>Request denied</h1><p>" +
                esc(message) + "</p></body></html>").encode("utf-8")
        self._headers(status, extra={"Content-Length": str(len(body))})
        self.wfile.write(body)

    def _redirect(self, query=""):
        self._headers(HTTPStatus.SEE_OTHER, extra={"Location": "/" + query,
                       "Content-Length": "0"})

    def do_GET(self):
        if not self._host_ok():
            return self._error(HTTPStatus.BAD_REQUEST, "invalid local Host")
        parts = urllib.parse.urlsplit(self.path)
        if parts.path not in ("/", "/dossier"):
            return self._error(HTTPStatus.NOT_FOUND, "unknown route")
        supplied = urllib.parse.parse_qs(parts.query).get("token", [""])[0]
        if parts.path == "/" and supplied and hmac.compare_digest(supplied, self.server.session_token):
            self._headers(HTTPStatus.SEE_OTHER, extra={
                "Location": "/", "Set-Cookie":
                    f"caeval_local={self.server.session_token}; HttpOnly; SameSite=Strict; Path=/",
                "Content-Length": "0",
            })
            return
        if not self._authenticated():
            return self._error(HTTPStatus.UNAUTHORIZED,
                               "Launch with the private URL shown in your terminal.")
        query = urllib.parse.parse_qs(parts.query)
        try:
            if parts.path == "/dossier":
                if not (self.server.directory / buyer.STATE_FILE).is_file():
                    return self._error(HTTPStatus.NOT_FOUND, "create a buyer project first")
                with self.server.mutation_lock:
                    document = buyer.render_dossier(self.server.directory).encode("utf-8")
                self._headers(content_type="text/plain; charset=utf-8", extra={
                    "Content-Disposition": 'attachment; filename="clinical-ai-procurement.md"',
                    "Content-Length": str(len(document)),
                })
                self.wfile.write(document)
                return
            if not (self.server.directory / buyer.STATE_FILE).exists():
                content = render_setup()
            else:
                data = buyer.status(self.server.directory)
                content = render_page(
                    data, query.get("message", [""])[0],
                    query.get("error", [""])[0])
        except (buyer.BuyerError, OSError, ValueError) as exc:
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR,
                               "Could not read local buyer workspace: " + str(exc))
        self._headers(extra={"Content-Length": str(len(content))})
        self.wfile.write(content)

    def do_POST(self):
        if not self._host_ok() or not self._authenticated():
            return self._error(HTTPStatus.FORBIDDEN, "not authenticated")
        expected_origin = f"http://{HOST}:{self.server.server_port}"
        if self.headers.get("Origin") != expected_origin:
            return self._error(HTTPStatus.FORBIDDEN,
                               "cross-origin local writes are not permitted")
        path = urllib.parse.urlsplit(self.path).path
        if path not in ("/project-init", "/vendor-add", "/evidence-add"):
            return self._error(HTTPStatus.NOT_FOUND, "unknown route")
        try:
            n = int(self.headers.get("Content-Length", "-1"))
            if n < 1 or n > MAX_REQUEST_BYTES:
                raise buyer.BuyerError("request exceeds local upload limits")
            ctype = self.headers.get("Content-Type", "")
            body = self.rfile.read(n)
            if len(body) != n:
                raise buyer.BuyerError("incomplete request body")
            if path in ("/project-init", "/vendor-add"):
                if not ctype.startswith("application/x-www-form-urlencoded"):
                    raise buyer.BuyerError("invalid local form content type")
                if n > 8192:
                    raise buyer.BuyerError("local form exceeds allowed size")
                fields = urllib.parse.parse_qs(body.decode("utf-8"), keep_blank_values=True)
                required = (("name", "intended_use", "setting", "jurisdiction")
                            if path == "/project-init" else ("vendor_id", "name"))
                for key in required:
                    if len(fields.get(key, [])) != 1 or not fields[key][0].strip():
                        raise buyer.BuyerError(f"one nonempty {key} is required")
                if path == "/project-init":
                    with self.server.mutation_lock:
                        buyer.init(self.server.directory, fields["name"][0],
                                   fields["intended_use"][0], fields["setting"][0],
                                   fields["jurisdiction"][0])
                    return self._redirect("?message=project-created")
                if not (self.server.directory / buyer.STATE_FILE).is_file():
                    raise buyer.BuyerError("create a buyer project before registering vendors")
                with self.server.mutation_lock:
                    buyer.add_vendor(self.server.directory, fields["vendor_id"][0], fields["name"][0])
                return self._redirect("?message=vendor-added")
            fields, payload, filename = parse_upload(body, ctype)
            if not fields.get("vendor_id") or not fields.get("kind"):
                raise buyer.BuyerError("vendor and evidence category are required")
            tmp = tempfile.NamedTemporaryFile(prefix=".upload-", dir=self.server.directory,
                                               delete=False)
            try:
                os.chmod(tmp.name, 0o600)
                with tmp:
                    tmp.write(payload)
                with self.server.mutation_lock:
                    buyer.add_evidence(self.server.directory, fields["vendor_id"],
                                       fields["kind"], tmp.name,
                                       fields.get("title") or filename,
                                       source_filename=filename)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
            return self._redirect("?message=evidence-added")
        except (buyer.BuyerError, ValueError, UnicodeError, OSError) as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))


def serve(directory, port: int = 0, open_browser: bool = True):
    """Block until interrupted. No remote interface and no external calls."""
    with BuyerHTTPServer(directory, port) as app:
        print(json.dumps({"ok": True, "result": {
            "url": app.launch_url, "host": HOST, "port": app.server_port,
            "network_scope": "loopback_only", "cloud_required": False,
            "security": "single_user_session_token_not_enterprise_IAM",
        }}), flush=True)
        if open_browser:
            webbrowser.open(app.launch_url)
        try:
            app.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
        finally:
            app.shutdown()
