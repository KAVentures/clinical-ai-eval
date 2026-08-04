"""HTML rendering. No external assets, no JS framework, no CDN — one file.

The rendering rule that matters: `headline()` will not render a rate without its
claim authority. A number stripped of "experimental, mock subject, not decision
grade" is the single most likely artifact of this repo to end up quoted out of
context, so the coupling is enforced in code rather than left to the caller.
"""
from __future__ import annotations

import html

CSS = """
:root{--fg:#16181d;--mut:#5b6472;--bd:#dfe3ea;--bg:#fff;--warn:#8a5a00;--warnbg:#fff6e5;
--bad:#8d2323;--badbg:#fdeded;--ok:#14532d;--okbg:#eaf6ee;--accent:#1f3a68}
@media(prefers-color-scheme:dark){:root{--fg:#e8eaed;--mut:#9aa4b2;--bd:#2c313a;--bg:#14161a;
--warnbg:#332a12;--warn:#f0c26b;--bad:#f2a0a0;--badbg:#3a1f1f;--ok:#a5d6b7;--okbg:#17301f;
--accent:#8fb3e8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:1.5rem}
nav{border-bottom:1px solid var(--bd);padding:.75rem 0;margin-bottom:1.5rem}
nav a{color:var(--accent);text-decoration:none;margin-right:1.25rem;font-weight:500}
h1{font-size:1.5rem;margin:0 0 .25rem}h2{font-size:1.15rem;margin:1.75rem 0 .5rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:14px}
th,td{border:1px solid var(--bd);padding:.4rem .55rem;text-align:left;vertical-align:top}
th{background:rgba(127,127,127,.08);font-weight:600}
.scroll{overflow-x:auto;max-width:100%}
.muted{color:var(--mut);font-size:13px}
.banner{padding:.7rem .9rem;border-radius:6px;margin:.75rem 0;font-size:14px;border:1px solid}
.warn{background:var(--warnbg);color:var(--warn);border-color:currentColor}
.bad{background:var(--badbg);color:var(--bad);border-color:currentColor}
.ok{background:var(--okbg);color:var(--ok);border-color:currentColor}
.big{font-size:2rem;font-weight:600;letter-spacing:-.02em}
.card{border:1px solid var(--bd);border-radius:8px;padding:1rem;margin:.75rem 0}
pre{background:rgba(127,127,127,.08);padding:.75rem;border-radius:6px;overflow-x:auto;
font-size:13px;white-space:pre-wrap}
label{display:block;margin:.9rem 0 .3rem;font-weight:500}
select,textarea,input{width:100%;padding:.45rem;border:1px solid var(--bd);border-radius:5px;
background:var(--bg);color:var(--fg);font:inherit}
button{margin-top:1rem;padding:.55rem 1.1rem;border-radius:6px;border:1px solid var(--accent);
background:var(--accent);color:#fff;font:inherit;font-weight:500;cursor:pointer}
"""

E = html.escape


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{E(title)}</title><style>{CSS}</style></head><body><div class='wrap'>"
            f"<nav><a href='/'>Runs</a><a href='/families'>Families</a>"
            f"<a href='/compare'>Comparison</a><a href='/packs'>Case packs</a>"
            f"<a href='/review'>Review</a><a href='/about'>About</a></nav>"
            f"{body}</div></body></html>")


class MissingAuthorityError(ValueError):
    """Raised when a headline number is rendered without its claim authority."""


def headline(value: str, label: str, authority: dict) -> str:
    """Render a headline number. REFUSES without claim authority.

    Not defensive programming for its own sake: a bare rate is the artifact most
    likely to be screenshotted, and by then the caveats live only in a document
    nobody opened.
    """
    required = ("maturity", "conformance", "decision_grade")
    missing = [k for k in required if k not in (authority or {})]
    if missing:
        raise MissingAuthorityError(
            f"refusing to render headline {label!r} without {missing}. A rate without its "
            f"maturity, conformance level and decision-grade flag is exactly the artifact "
            f"that gets quoted out of context.")
    grade = authority["decision_grade"]
    cls = "ok" if grade else "warn"
    msg = ("This number is decision grade within its audited scope." if grade else
           f"NOT decision grade — family maturity `{authority['maturity']}`, run conformance "
           f"`{authority['conformance']}`. It may not support a published finding, a "
           f"procurement decision, or a release gate.")
    return (f"<div class='card'><div class='big'>{E(value)}</div>"
            f"<div class='muted'>{E(label)}</div>"
            f"<div class='banner {cls}'>{E(msg)}</div></div>")


def table(headers: list, rows: list) -> str:
    h = "".join(f"<th>{E(str(x))}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{E(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='scroll'><table><tr>{h}</tr>{body}</table></div>"


def banner(kind: str, text: str) -> str:
    return f"<div class='banner {kind}'>{E(text)}</div>"
