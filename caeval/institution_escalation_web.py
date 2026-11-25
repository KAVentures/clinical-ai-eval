"""Local notification inbox UI. Never sends messages or changes source state."""
from . import institution_escalation as escalation


def render(directory,actor):
    from .institution_web import page,esc,_hidden
    rows=escalation.overview(directory,actor)["alerts"]
    body="<p><a href='/'>Workspace</a></p><p>On-demand local notices, not a continuously monitored emergency service. Acknowledgement records that you have seen this exact notice; it does not resolve the source, silence notices for other users or authorize deployment.</p>"
    if not rows:
        body+="<p>No current notices within your scope. This is not evidence of clinical safety.</p>"
    for row in rows:
        body+="<section><h2>"+esc(row["priority"].capitalize()+": "+row["title"])+"</h2>"
        body+="<p>Source: "+esc(row["source"])+" · Responsible owner: "+esc(row["owner"])+"</p>"
        body+="<p><a href='"+esc(row["destination"])+"'>Open source register</a></p>"
        if row["acknowledged_at"] is not None:
            import datetime as dt
            timestamp=dt.datetime.fromtimestamp(row["acknowledged_at"],dt.timezone.utc).isoformat()
            body+="<p>Acknowledged by you: "+esc(timestamp)+"</p>"
        elif actor["role"] in ("admin","operator"):
            body+="<form method='post' action='/escalation-acknowledge'>"+_hidden("csrf",actor["csrf"])+_hidden("id",row["id"])+"<button>Acknowledge notice</button></form>"
        body+="</section>"
    return page("Institutional escalation inbox",body)
