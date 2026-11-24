"""Institutional evidence and research statistics browser views."""
from __future__ import annotations
import json
import urllib.parse

from . import institution_analysis as analysis


def render(directory, actor):
    from .institution_web import esc,page,_hidden
    data=analysis.overview(directory,actor)
    body="<p><a href='/'>Workspace</a></p><p>Package integrity and review coverage are not evidence of clinical readiness.</p>"
    for job in data["jobs"]:
        body+="<section><h2>"+esc(job["job_id"])+"</h2><p>"+esc(job["mode"])+" · "+esc(job["status"])+"</p>"
        for family in job["families"]:
            body+="<h3>"+esc(family["family_id"])+"</h3><p>Recorded claim: "+esc(family["recorded_claim"])+"</p>"
            body+="<p>Review queue: "+str(family["review_count"])+" cells. Recorded submissions: "+str(family["submitted_accounts"])+" of "+str(family["assigned_accounts"])+" assigned accounts.</p>"
            if actor["role"] in ("admin","operator") and family["submitted_accounts"]>=2:
                body+="<form action='/evidence-analysis' method='post'>"+_hidden("csrf",actor["csrf"])+_hidden("job",job["job_id"])+_hidden("family",family["family_id"])+"<button>Compute exploratory statistics</button></form>"
        body+="</section>"
    return page("Scientific evidence overview",body)


def render_analysis(result):
    from .institution_web import page,esc
    def num(value):
        return "Not estimable" if value is None else esc(value)
    body="<p><a href='/evidence'>Evidence overview</a></p><p>Exploratory review analysis. Not L2 adjudication or procurement approval.</p>"
    body+="<section><h2>Review coverage and uncertainty</h2><p>Family: "+esc(result["family_id"])+"</p><table>"
    for k,title in (("n_reviewers","Reviewer accounts"),("queue_size","Selected cells"),("queue_completion","Resolved fraction"),("under_reviewed_cells","Under-reviewed cells"),("n_contested","Unresolved disagreements"),("mandatory_completion","Mandatory cells resolved"),("human_unsafe_rate","Unsafe fraction among resolved cells")):
        body+="<tr><th>"+title+"</th><td>"+num(result[k])+"</td></tr>"
    body+="</table></section><section><h2>Inter-rater agreement</h2>"
    body+="<p>Mean pairwise agreement: "+num(result["inter_rater"]["mean_pairwise_percent_agreement"])+"</p>"
    body+="<p>Krippendorff alpha: "+num(result["inter_rater"]["krippendorff_alpha"])+"</p><table><tr><th>Reviewer pair</th><th>Cohen kappa</th></tr>"
    for pair,val in result["inter_rater"]["pairwise_cohen_kappa"].items():
        body+="<tr><td>"+esc(pair)+"</td><td>"+num(val)+"</td></tr>"
    body+="</table></section><section><h2>Judge versus reviewer-majority operating points</h2>"
    columns=("n","sensitivity","specificity","ppv","npv","tp","fp","tn","fn")
    body+="<table><tr><th>Judge</th>"+"".join("<th>"+esc(k)+"</th>" for k in columns)+"</tr>"
    for name,values in result["judge_vs_human"].items():
        body+="<tr><td>"+esc(name)+"</td>"+"".join("<td>"+num(values[k])+"</td>" for k in columns)+"</tr>"
    body+="</table><ul>"+"".join("<li>"+esc(x)+"</li>" for x in result["limitations"])+"</ul></section>"
    return page("Exploratory scientific review analysis",body)


def analyze(directory,actor,form):
    if set(form)!={"job","family"}:
        from .institution_access import AccessDenied
        raise AccessDenied("select exactly one job and family")
    return render_analysis(analysis.analyze(directory,actor,form["job"],form["family"]))
