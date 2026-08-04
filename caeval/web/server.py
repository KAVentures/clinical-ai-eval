"""The shell's HTTP layer: routing and views. stdlib http.server only.

Routing is a plain dict of path -> handler returning (status, html). Handlers are
pure functions of the app state, so every view is testable without a socket —
which is the point: a guard that only holds when a real server is running is a
guard nobody tests.
"""
from __future__ import annotations

import json
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .. import procurement
from ..version import EVAL_STANDARD_VERSION, SCOPE, __version__
from .render import E, banner, headline, page, table

BIND_HOST = "127.0.0.1"          # never 0.0.0.0: there is no authentication to bind with

HONESTY_BANNER = (
    "Local single-tenant console. No accounts, no authentication, no access control: "
    "anyone who can reach this port can read every run here. Do not expose it to a network."
)


class App:
    """Holds the paths the shell reads. It owns no evaluation logic."""

    def __init__(self, runs_dir: str = "out", packs=None, comparisons=None):
        self.runs_dir = Path(runs_dir)
        self.packs = packs or {}
        self.comparisons = comparisons or {}
        self.review_sessions = {}

    # ---- data access ----
    def runs(self) -> list:
        if not self.runs_dir.exists():
            return []
        out = []
        for d in sorted(self.runs_dir.iterdir()):
            meta = d / "run_meta.json"
            if meta.is_file():
                try:
                    m = json.loads(meta.read_text())
                except Exception:  # noqa: BLE001
                    m = {"run_id": d.name, "error": "run_meta.json is unreadable"}
                m.setdefault("run_id", d.name)
                m["_path"] = str(d)
                out.append(m)
        return out

    def run(self, run_id: str):
        return next((r for r in self.runs() if r.get("run_id") == run_id), None)


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------

def _authority_of(meta: dict) -> dict:
    """Claim authority for a run. Fail closed: anything unknown is not decision grade."""
    claim = meta.get("claim", {}) or {}
    maturity = claim.get("family_maturity") or meta.get("family_maturity") or "unknown"
    conformance = claim.get("run_conformance") or meta.get("conformance") or "unknown"
    return {
        "maturity": maturity,
        "conformance": conformance,
        # L2 + a matured family is the ONLY decision-grade combination, and unknown
        # never qualifies. The shell must not be a second, laxer definition of a claim.
        "decision_grade": bool(conformance == "L2" and maturity in
                               ("validated", "surveillance_ready")),
    }


def view_runs(app: App, _q=None) -> tuple:
    runs = app.runs()
    body = ["<h1>Runs</h1>", banner("warn", HONESTY_BANNER)]
    if not runs:
        body.append(banner("warn", f"No runs found under {app.runs_dir}/. "
                                   f"Produce one with `clinical-ai-eval run`."))
        return 200, page("Runs", "".join(body))
    rows = []
    for r in runs:
        a = _authority_of(r)
        rows.append([r.get("run_id", "?"), r.get("family_id", "?"),
                     (r.get("subject", {}) or {}).get("kind", "?"),
                     a["conformance"], a["maturity"],
                     "yes" if a["decision_grade"] else "no"])
    body.append(table(["run", "family", "subject", "conformance", "maturity",
                       "decision grade"], rows))
    body.append("<p class='muted'>Every run above is listed with its claim authority. "
                "A run that is not decision grade may not support a published finding, a "
                "procurement decision, or a release gate, whatever its numbers say.</p>")
    for r in runs:
        body.append(f"<p><a href='/run?id={urllib.parse.quote(str(r.get('run_id')))}'>"
                    f"{E(str(r.get('run_id')))}</a></p>")
    return 200, page("Runs", "".join(body))


def view_run(app: App, q: dict) -> tuple:
    rid = (q.get("id") or [""])[0]
    meta = app.run(rid)
    if not meta:
        return 404, page("Not found", f"<h1>No run {E(rid)}</h1>"
                                      f"<p class='muted'>Unknown run ids are not guessed at.</p>")
    a = _authority_of(meta)
    body = [f"<h1>Run {E(rid)}</h1>"]
    if (meta.get("subject", {}) or {}).get("kind") == "mock":
        body.append(banner("bad", "The subject of this run is a MOCK. Its numbers are software "
                                  "fixtures demonstrating pipeline wiring, not evidence about "
                                  "any product."))
    summary = meta.get("summary", {}) or {}
    for key, label in (("panel_any_unsafe_rate", "panel-any unsafe rate"),
                       ("excessive_abstention_rate", "excessive abstention rate")):
        if key in summary:
            try:
                body.append(headline(f"{float(summary[key]):.1%}", label, a))
            except Exception:  # noqa: BLE001
                body.append(banner("bad", f"Refusing to render {label}: claim authority missing."))
    body.append("<h2>Run metadata</h2>")
    body.append(f"<pre>{E(json.dumps({k: v for k, v in meta.items() if not k.startswith('_')}, indent=2)[:6000])}</pre>")
    return 200, page(f"Run {rid}", "".join(body))


def view_families(app: App, _q=None) -> tuple:
    from .. import family_sdk
    body = ["<h1>Test families</h1>"]
    rows = []
    for row in family_sdk.family_status():
        rows.append([row.get("family_id"), "yes" if row.get("runnable") else "no",
                     row.get("maturity", "experimental"),
                     row.get("reason", "")[:120]])
    body.append(table(["family", "runnable", "maturity", "blocked reason"], rows))
    body.append(banner("warn", "Runnable is not validated. Every family shipped in this build "
                               "is `experimental`: the measurement itself has not been "
                               "calibrated against clinician judgement."))
    return 200, page("Families", "".join(body))


def view_compare(app: App, q: dict) -> tuple:
    name = (q.get("id") or [""])[0]
    body = ["<h1>Product comparison</h1>"]
    if not app.comparisons:
        body.append(banner("warn", "No comparisons loaded."))
        return 200, page("Comparison", "".join(body))
    if name not in app.comparisons:
        body.append("<ul>" + "".join(
            f"<li><a href='/compare?id={urllib.parse.quote(k)}'>{E(k)}</a></li>"
            for k in app.comparisons) + "</ul>")
        return 200, page("Comparison", "".join(body))
    result = app.comparisons[name]
    c = result["comparability"]
    body.append(banner("ok" if c["status"] == procurement.COMPARABLE else "bad", c["note"]))
    body.append(banner("warn", "This page shows no combined score and no buy/no-buy "
                               "recommendation, by design."))
    for h in result["per_hazard"]:
        crit = h["acceptance_criterion"]
        body.append(f"<h2>{E(h['hazard_id'])} — {E(h['description'])}</h2>")
        body.append(f"<p class='muted'>Your predeclared bar: "
                    f"<code>{E(crit['metric'])} {E(crit['operator'])} {E(str(crit['threshold']))}</code></p>")
        rows = [[r["product_id"],
                 "—" if r["rate"] is None else f"{r['rate']:.1%}",
                 "—" if not r.get("ci") else f"[{r['ci'][0]:.1%}, {r['ci'][1]:.1%}]",
                 r["n"], r["status"]] for r in h["products"]]
        body.append(table(["product", "rate", "95% CI", "n", "status"], rows))
    body.append("<h2>What this does not tell you</h2><ul>")
    body += [f"<li>{E(x)}</li>" for x in result["what_this_does_not_tell_you"]]
    body.append("</ul>")
    return 200, page("Comparison", "".join(body))


def view_packs(app: App, _q=None) -> tuple:
    body = ["<h1>Case packs</h1>"]
    if not app.packs:
        body.append(banner("warn", "No case packs loaded."))
        return 200, page("Case packs", "".join(body))
    rows = []
    for pid, built in app.packs.items():
        v, m, s = built["validation"], built["meta"], built["signatures"]
        rows.append([pid, m["kind"], m["visibility"], built["pack_hash"][:12],
                     v["n_cases"], "yes" if v["valid"] else "NO",
                     len(v["warnings"]), s["review_status_effective"],
                     "yes" if built["usable_for_qualification"] else "no"])
    body.append(table(["pack", "kind", "visibility", "hash", "cases", "structurally valid",
                       "warnings", "review status", "usable for qualification"], rows))
    body.append(banner("warn", "Structural validation is not clinical review. A pack is "
                               "`unreviewed` until a named clinician signs that exact content "
                               "hash; editing a case afterwards invalidates the signature."))
    for pid, built in app.packs.items():
        for w in built["validation"]["warnings"]:
            body.append(f"<p class='muted'>{E(pid)} — {E(w['where'])}: {E(w['message'])}</p>")
    return 200, page("Case packs", "".join(body))


def view_review(app: App, q: dict) -> tuple:
    rid = (q.get("session") or [""])[0]
    body = ["<h1>Clinician review</h1>"]
    if rid not in app.review_sessions:
        body.append(banner("warn",
                           "No active review session. A session requires a verified review "
                           "packet issued for a specific run; there is no anonymous review."))
        body.append(banner("warn",
                           "The packet HMAC protects a submission against modification and "
                           "replay between the workspace and this process. It is NOT PKI and "
                           "NOT proof of clinician identity: anyone holding the run secret can "
                           "mint a packet."))
        return 200, page("Review", "".join(body))
    sess = app.review_sessions[rid]
    prog = sess.progress()
    if prog["synthetic_packet"]:
        body.append(banner("bad", prog["note"]))
    body.append(f"<p class='muted'>Reviewer <b>{E(prog['reviewer_id'])}</b> — "
                f"{prog['completed_by_me']} / {prog['queue_size']} done; "
                f"{prog['cells_with_two_reviewers']} cells have two independent reviewers.</p>")
    item = sess.next_item()
    if item is None:
        body.append(banner("ok", "Queue complete for this reviewer."))
        return 200, page("Review", "".join(body))
    body.append(banner("warn", "You are seeing a blinded response. The perturbation applied, "
                               "the expected missing evidence and the automated judge's label "
                               "are not sent to this page — reviewing with them visible "
                               "changed labels by 64 percentage points."))
    body.append(f"<div class='card'><pre>{E(str(item.get('response_text', '')))[:8000]}</pre></div>")
    body.append(f"<form method='post' action='/review/submit'>"
                f"<input type='hidden' name='session' value='{E(rid)}'>"
                f"<input type='hidden' name='cell_id' value='{E(str(item.get('cell_id','')))}'>")
    from .review import REVIEW_FIELDS
    for f, kind, opts, question in REVIEW_FIELDS:
        body.append(f"<label for='{E(f)}'>{E(question)}</label>")
        if kind == "select":
            body.append(f"<select name='{E(f)}' id='{E(f)}'>"
                        + "<option value=''>— select —</option>"
                        + "".join(f"<option value='{E(o)}'>{E(o)}</option>" for o in opts)
                        + "</select>")
        else:
            body.append(f"<textarea name='{E(f)}' id='{E(f)}' rows='3'></textarea>")
    body.append("<button type='submit'>Submit</button></form>")
    return 200, page("Review", "".join(body))


def post_review_submit(app: App, form: dict) -> tuple:
    rid = (form.get("session") or [""])[0]
    sess = app.review_sessions.get(rid)
    if sess is None:
        return 403, page("Rejected", "<h1>Rejected</h1>"
                                     "<p>No verified review session for this submission.</p>")
    try:
        sess.submit((form.get("cell_id") or [""])[0],
                    {k: (v[0] if isinstance(v, list) else v) for k, v in form.items()})
    except (ValueError, PermissionError) as e:
        return 400, page("Rejected", f"<h1>Submission rejected</h1>"
                                     f"<p>{E(str(e))}</p>"
                                     f"<p class='muted'>The submission was not stored. "
                                     f"Incomplete review answers are never saved as "
                                     f"'cannot determine'.</p>")
    return 200, page("Saved", f"<h1>Saved</h1><p><a href='/review?session={E(rid)}'>Next</a></p>")


def view_about(app: App, _q=None) -> tuple:
    body = [f"<h1>clinical-ai-eval {E(__version__)}</h1>",
            f"<p class='muted'>EVAL_STANDARD v{E(EVAL_STANDARD_VERSION)}</p>",
            f"<p>{E(SCOPE)}</p>",
            banner("warn", HONESTY_BANNER),
            banner("bad",
                   "This is a candidate protocol and a reference harness, not a validated "
                   "standard. Every test family is `experimental`. No real-judge L1 or "
                   "real-clinician L2 run has been performed. Do not describe it publicly as "
                   "a standard, a validated harness, or an L2 framework."),
            "<h2>What this console cannot do</h2><ul>",
            "<li>Start, modify or re-score a run — it renders artifacts the CLI produced.</li>",
            "<li>Raise a maturity level, a conformance level, or a claim.</li>",
            "<li>Authenticate anyone. It has no accounts and no access control.</li>",
            "<li>Rank products or recommend a purchase.</li></ul>"]
    return 200, page("About", "".join(body))


ROUTES = {
    "/": view_runs,
    "/run": view_run,
    "/families": view_families,
    "/compare": view_compare,
    "/packs": view_packs,
    "/review": view_review,
    "/about": view_about,
}
POST_ROUTES = {"/review/submit": post_review_submit}


def build_app(runs_dir: str = "out", packs=None, comparisons=None) -> App:
    return App(runs_dir, packs, comparisons)


def handle(app: App, method: str, path: str, query: dict, form: dict | None = None) -> tuple:
    """Pure request handling — no socket required, so every view is unit-testable."""
    try:
        if method == "POST":
            fn = POST_ROUTES.get(path)
            if fn is None:
                return 405, page("Not allowed", "<h1>405</h1>")
            return fn(app, form or {})
        fn = ROUTES.get(path)
        if fn is None:
            return 404, page("Not found", "<h1>404</h1>")
        return fn(app, query)
    except Exception:  # noqa: BLE001
        # Fail visibly. A console that swallows an error renders a blank panel that
        # reads as "nothing wrong here".
        return 500, page("Error", f"<h1>Internal error</h1><pre>{E(traceback.format_exc())}</pre>")


def serve(app: App, port: int = 8765, host: str = BIND_HOST):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, html_text):
            body = html_text.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # No third-party assets are used, so the policy can be maximally strict.
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            u = urllib.parse.urlparse(self.path)
            self._send(*handle(app, "GET", u.path, urllib.parse.parse_qs(u.query)))

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            form = urllib.parse.parse_qs(self.rfile.read(n).decode())
            self._send(*handle(app, "POST", urllib.parse.urlparse(self.path).path, {}, form))

        def log_message(self, *a):
            pass

    if host != BIND_HOST:
        print(f"WARNING: binding {host} — this console has no authentication. "
              f"Anyone who can reach this port can read every run.")
    srv = HTTPServer((host, port), Handler)
    print(f"clinical-ai-eval console on http://{host}:{port}  (Ctrl-C to stop)")
    print(HONESTY_BANNER)
    srv.serve_forever()
