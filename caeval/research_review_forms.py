"""Server-rendered clinical review forms over the exact issued packet.

The form is a presentation adapter, not a second clinical schema. It rebuilds
the original submission using server-generated field inventory and delegates
all clinical validation to the existing account-bound review lock.
"""
from __future__ import annotations

import copy

from . import institution_access as access
from . import citation_assessment as citation, scribe_assessment as scribe
from . import robustness_assessment as robustness

MAX_FIELDS=2400
EVIDENCE_SLOTS=3


def _fail(message):
    raise access.AccessDenied(message)


def _set(obj,path,value):
    for part in path[:-1]:
        obj=obj[part]
    obj[path[-1]]=value


def _span(text,quote):
    # No whitespace normalization: offsets are Python Unicode codepoints.
    if not quote or len(quote)>8000:
        _fail("evidence quote must be nonempty and bounded")
    start=text.find(quote)
    if start<0 or text.find(quote,start+1)>=0:
        _fail("quote must occur exactly once; use advanced packet for ambiguous spans")
    return {"start":start,"end":start+len(quote),"quote":quote}


class Form:
    def __init__(self,doc):
        self.doc=doc
        self.sub=copy.deepcopy(doc["submission"])
        self.fields={}
        self.evidence=[]
        self.html=[]
        self._build()

    def add(self,path,label,*,options=None,limit=4000,required=True,record=True):
        from .institution_web import esc
        key="f"+str(len(self.fields))
        self.fields[key]=(path,options,limit,required,record)
        if options is not None:
            control="<select name='"+key+"'"+(" required" if required else "")+"><option value=''>Choose</option>"
            control+="".join("<option value='"+esc(x)+"'>"+esc(x.replace("_"," ").capitalize())+"</option>" for x in options)+"</select>"
        else:
            control="<textarea name='"+key+"' rows='3' maxlength='"+str(limit)+"'"+(" required" if required else "")+"></textarea>"
        self.html.append("<label>"+esc(label)+control+"</label>")
        return key

    def heading(self,title):
        from .institution_web import esc
        self.html.append("<h3>"+esc(title)+"</h3>")

    def context(self,label,value):
        from .institution_web import esc
        self.html.append("<p><strong>"+esc(label)+"</strong></p><p class='case'>"+esc(value)+"</p>")

    def open(self,title):
        from .institution_web import esc
        self.html.append("<section><h2>"+esc(title)+"</h2>")

    def close(self):
        self.html.append("</section>")

    def spans(self,path,label,text=None,turns=None):
        fields=[]
        for index in range(EVIDENCE_SLOTS):
            turn=None
            if turns is not None:
                turn=self.add(None,label+" "+str(index+1)+" — transcript turn",
                              options=tuple(turns),required=False,record=False)
            quote=self.add(None,label+" "+str(index+1)+" — exact quote",
                           limit=8000,required=False,record=False)
            fields.append((turn,quote))
        self.evidence.append((path,text,turns,fields))

    def _build(self):
        request=self.doc["request"]
        schema=request["schema"]
        if schema==citation.SCHEMA:
            for i,item in enumerate(request["answers"]):
                self.open("Answer "+str(i+1))
                self.context("Question and supplied context",item["question"])
                self.context("Candidate answer",item["answer"])
                for claim in item["claims"]:
                    self.context("Extracted claim",claim["text"]+" | Citations: "+", ".join(claim["cited_source_ids"]))
                self.add(("coverage",i,"status"),"Extraction coverage",options=citation.COVERAGE)
                self.add(("coverage",i,"note"),"Coverage explanation (required for incomplete or uncertain)",required=False)
                self.close()
            for i,item in enumerate(request["claim_source_pairs"]):
                self.open("Claim-to-source assessment "+str(i+1))
                self.context("Claim",item["claim_text"])
                self.context("Source "+item["source_id"]+" · "+item["source_version"],item["source_text"])
                self.add(("support",i,"verdict"),"Semantic support",options=citation.VERDICTS)
                self.add(("support",i,"evidence_quote"),"Exact source quotation (required for support or contradiction)",limit=8000,required=False)
                self.close()
        elif schema==scribe.SCHEMA:
            for i,item in enumerate(request["items"]):
                self.open("Consultation "+str(i+1))
                turns={t["turn_id"]:t["text"] for t in item["transcript"]}
                for t in item["transcript"]:
                    self.context(t["turn_id"]+" · "+t["speaker"],t["text"])
                self.context("Generated clinical note",item["note"])
                p=("cases",i)
                self.add(p+("reference_coverage",),"Reference fact inventory coverage",options=scribe.COVERAGE_VERDICTS)
                self.add(p+("claim_coverage",),"Note claim inventory coverage",options=scribe.COVERAGE_VERDICTS)
                self.add(p+("coverage_note",),"Coverage explanation (required for incomplete or uncertain)",required=False)
                for j,f in enumerate(item["reference_facts"]):
                    self.heading("Reference fact: "+f["fact"])
                    self.context("Authored importance",f["importance"])
                    for ev in f["transcript_evidence"]:
                        self.context("Reference evidence · "+ev["turn_id"],ev["quote"])
                    q=p+("facts",j)
                    self.add(q+("reference_validity",),"Reference validity",options=scribe.REFERENCE_VERDICTS)
                    self.add(q+("documentation",),"Documentation assessment",options=scribe.FACT_VERDICTS)
                    self.spans(q+("note_evidence",),"Note evidence",text=item["note"])
                    self.add(q+("rationale",),"Clinical rationale")
                for j,c in enumerate(item["claims"]):
                    self.heading("Note claim: "+c["quote"])
                    q=p+("claims",j)
                    self.add(q+("verdict",),"Transcript support",options=scribe.CLAIM_VERDICTS)
                    self.spans(q+("transcript_evidence",),"Transcript evidence",turns=turns)
                    self.add(q+("importance",),"Clinical importance",options=(*scribe.IMPORTANCE,"cannot_judge"))
                    self.add(q+("rationale",),"Clinical rationale")
                self.close()
        elif schema==robustness.SCHEMA:
            for i,item in enumerate(request["items"]):
                self.open("Review item "+str(i+1))
                p=("rows",i)
                if request["role"]=="reference_validity":
                    self.context("Declared relationship",item["relation"])
                    for key in ("baseline","variant"):
                        arm=item[key]
                        self.context(key.capitalize()+" question",arm["prompt"])
                        for group,label in arm["decision_groups"].items():
                            self.context("Decision group "+group,label)
                        self.context("Authored acceptable groups",", ".join(arm["acceptable"]))
                        self.context("Authored rationale",item[key+"_rationale"])
                    self.add(p+("validity",),"Clinical validity of pair",options=robustness.VALIDITY)
                elif request["role"]=="isolated_answer":
                    self.context("Clinical question",item["prompt"])
                    self.context("Candidate answer",item["answer"])
                    for group,label in item["decision_groups"].items():
                        self.context("Decision group "+group,label)
                    self.add(p+("safety",),"Safety",options=robustness.SAFETY)
                    self.add(p+("unsupported_inference",),"Unsupported inference",options=robustness.INFERENCE)
                    self.add(p+("adequacy",),"Clinical adequacy",options=robustness.ADEQUACY)
                    self.add(p+("decision_group",),"Observed decision group",options=(*item["decision_groups"],"cannot_judge"))
                else:
                    _fail("unsupported reviewer role")
                self.add(p+("rationale",),"Clinical rationale")
                self.close()
        else:
            _fail("unsupported clinical review schema")

    @property
    def available(self):
        return len(self.fields)<=MAX_FIELDS

    def render(self):
        return "".join(self.html)

    def parse(self,fields):
        if not self.available or set(fields)!=set(self.fields)|{"confirm"} or fields["confirm"]!="yes":
            _fail("complete issued form and independent confirmation required")
        for key,(path,options,limit,required,record) in self.fields.items():
            value=fields[key]
            if (not isinstance(value,str) or len(value)>limit or
                    required and not value.strip() or
                    options is not None and value not in options and not (not required and value=="")):
                _fail("invalid review field")
            if record:
                _set(self.sub,path,value)
        for path,text,turns,pairs in self.evidence:
            spans=[]
            for turn_key,quote_key in pairs:
                quote=fields[quote_key]
                if turn_key is not None:
                    turn=fields[turn_key]
                    if bool(turn)!=bool(quote):
                        _fail("transcript evidence requires both turn and quote")
                    if quote:
                        spans.append({"turn_id":turn,**_span(turns[turn],quote)})
                elif quote:
                    spans.append(_span(text,quote))
            _set(self.sub,path,spans)
        self.sub["independent_review_confirmed"]=True
        return self.sub
