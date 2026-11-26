"""Matched research comparison browser. Never presents an acceptance score."""
import urllib.parse

from . import institution_research as studio, institution_access as access
from . import research_comparison as comparison


def _choices(directory,actor):
    with access.db(directory) as conn:
        studio._schema(conn)
        studio._actor(conn,actor,"admin","operator")
        rows=conn.execute("SELECT * FROM research_studies WHERE state='analyzed' ORDER BY rowid DESC LIMIT ?",
                          (studio.MAX_STUDIES,)).fetchall()
        result=[]
        for raw in rows:
            row=dict(raw)
            if actor["role"]!="admin" and row["owner"]!=actor["user_id"]:
                continue
            try:
                _,_,plan=studio._plan(directory,row)
                target=plan["spec"]["target"]
                result.append((row["id"],studio.LABELS[row["kind"]]+" · "+target["name"]+" "+target["version"]+" · "+row["id"]))
            except (ValueError,RuntimeError,TypeError,KeyError,OSError):
                continue
        return result


def render(directory,actor):
    from .institution_web import esc,page,_hidden
    choices=_choices(directory,actor)
    options="".join("<option value='"+esc(k)+"'>"+esc(label)+"</option>" for k,label in choices)
    body=("<p><a href='/research'>Clinical research studio</a></p>"
          "<p>Exploratory matched comparison. Only identical protocols, reviewer rosters "
          "and collection modes may be compared. Rates use determinate shared units; "
          "no clinical certification or procurement ranking.</p>")
    if len(choices)>=2:
        body+="<form method='post' action='/research-comparison-create'>"+_hidden("csrf",actor["csrf"])
        body+="<label>Baseline study <select name='baseline' required><option value=''>Select</option>"+options+"</select></label>"
        for i in range(1,5):
            body+="<label>Comparison study "+str(i)+" <select name='candidate"+str(i)+"'"+(" required" if i==1 else "")+"><option value=''>Select</option>"+options+"</select></label>"
        body+="<label><input type='checkbox' name='confirm' value='yes' required> I approve this descriptive comparison, not a clinical or purchasing decision.</label><button>Create frozen comparison</button></form>"
    else:
        body+="<p>At least two analyzed studies are required.</p>"
    for identifier in comparison.inventory(directory,actor):
        href="/research-comparison?"+urllib.parse.urlencode({"id":identifier})
        body+="<p><a href='"+esc(href)+"'>"+esc(identifier)+"</a></p>"
    return page("Matched clinical research comparisons",body)


def create(directory,actor,form):
    if set(form)!={"baseline","candidate1","candidate2","candidate3","candidate4","confirm"}:
        raise access.AccessDenied("invalid comparison form")
    ids=[form["baseline"]]+[form["candidate"+str(i)] for i in range(1,5) if form["candidate"+str(i)]]
    return comparison.create(directory,actor,ids,confirmed=form["confirm"]=="yes")


def detail(directory,actor,identifier):
    from .institution_web import page,esc
    doc=comparison.download(directory,actor,identifier)
    sources={s["study"]:s["target"] for s in doc["sources"]}
    def target(study):
        t=sources[study]
        return esc(t["name"]+" "+t["version"])
    def pct(value):
        return "Not assessed" if value is None else f"{100*value:.1f}%"
    def delta(value):
        return "Not assessed" if value is None else f"{100*value:+.1f} pp"
    body="<p><a href='/research-comparisons'>All comparisons</a></p>"
    body+="<p>Baseline: "+target(doc["baseline_study"])+"</p>"
    body+="<p>Protocol digest: "+esc(doc["protocol_hash"])+"</p>"
    body+="<p>Same reviewer roster; collection mode: "+esc(doc["sources"][0]["collection_mode"])+"</p>"
    body+="<p>Matched rate differences are not rankings. Some outcomes are favorable and others are adverse.</p>"
    url="/research-comparison-download?"+urllib.parse.urlencode({"id":identifier})
    body+="<p><a href='"+esc(url)+"'>Download verified comparison JSON</a></p>"
    for c in doc["comparisons"]:
        body+="<section><h2>"+target(c["candidate_study"])+"</h2>"
        body+="<div style='overflow-x:auto'><table><thead><tr><th>Endpoint</th><th>Matched / planned</th><th>Baseline</th><th>Candidate</th><th>Difference</th><th>Only baseline / only candidate assessed</th></tr></thead><tbody>"
        for name,m in c["metrics"].items():
            body+="<tr><td>"+esc(name.replace("_"," ").replace("/"," · "))+"</td>"
            body+="<td>"+esc(str(m["both_assessed"])+"/"+str(m["planned_units"]))+"</td>"
            body+="<td>"+pct(m["baseline_rate_on_matched"])+"</td><td>"+pct(m["candidate_rate_on_matched"])+"</td>"
            body+="<td>"+delta(m["candidate_minus_baseline"])+"</td>"
            body+="<td>"+esc(str(m["baseline_only_assessed"])+" / "+str(m["candidate_only_assessed"]))+"</td></tr>"
        body+="</tbody></table></div></section>"
    body+="<h2>Limitations</h2><ul>"+"".join("<li>"+esc(x)+"</li>" for x in doc["limitations"])+"</ul>"
    return page("Verified exploratory comparison",body)
