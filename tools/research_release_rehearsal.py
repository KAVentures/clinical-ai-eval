"""Installed-wheel research release acceptance, synthetic data only.

No source checkout imports, model calls, credentials or clinical claims.
Invoked by the wheel CI job with the installed interpreter.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from caeval import (citation_assessment as citation, decision_assessment as decision,
                    scribe_assessment as scribe, robustness_assessment as robustness,
                    project, project_experimental as lane, experimental_evidence as evidence,
                    research_review_forms as forms)
from caeval.util import repo_root

TARGET={"name":"Fictional release acceptance","version":"1"}


def require(condition,message):
    if not condition:
        raise RuntimeError(message)


def demo(kind):
    path=repo_root()/"casepacks"/kind/"public_dev"/"demo.json"
    doc=json.loads(path.read_text(encoding="utf-8"))
    require(doc["synthetic"] is True,"only synthetic demo packs accepted")
    doc["target"]=TARGET.copy()
    return doc


def citation_spec():
    return {"target":TARGET.copy(),"synthetic":True,
        "sources":[{"source_id":"CURRENT","version":"2","status":"current","text":"Fictional fact Z is required."},
                   {"source_id":"OLD","version":"1","status":"superseded","text":"Fictional fact Z is optional."},
                   {"source_id":"OTHER","version":"1","status":"current","text":"Unrelated fictional fact."}],
        "cases":[{"case_id":"fictional","question":"Is fictional fact Z required?",
                  "current_source":"CURRENT","superseded_source":"OLD",
                  "distractor_source":"OTHER","ghost_source":"MISSING"}]}


def write(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,allow_nan=False),encoding="utf-8")
    return path


def form_roundtrip(document):
    form=forms.Form(document)
    require(form.available,"demo packet unexpectedly exceeds browser field limit")
    fields={"confirm":"yes"}
    for key,(path,_,_,_,record) in form.fields.items():
        value=document["submission"]
        if record:
            for part in path:
                value=value[part]
        else:
            value=""
        fields[key]=value
    for path,_,_,pairs in form.evidence:
        value=document["submission"]
        for part in path:
            value=value[part]
        for (turn,quote),span in zip(pairs,value):
            fields[quote]=span["quote"]
            if turn is not None:
                fields[turn]=span["turn_id"]
    require(form.parse(fields)==document["submission"],"structured browser review roundtrip differs")


def complete_review(root,project_root,frozen,kind,responses,fill,reviewers,validity=()):
    workspace=root/(kind+"-review")
    mod=evidence.MODULES[kind]
    lane.prepare_review(project_root,frozen,frozen["digest"],kind,responses,workspace,
                        list(reviewers),list(validity),"synthetic-coordinator",confirmed=True)
    for reviewer in (*reviewers,*validity):
        doc=mod.template(workspace,reviewer)
        doc["submission"]["independent_review_confirmed"]=True
        fill(doc)
        form_roundtrip(doc)
        mod.submit(workspace,reviewer,doc,"synthetic-coordinator",confirmed=True)
    return lane.complete_module(project_root,frozen,frozen["digest"],kind,workspace,confirmed=True)


def run(root):
    root=Path(root)
    project_root=root/"project"
    project_root.mkdir()
    config=project.template("fictional release acceptance")
    config["target"]={**TARGET,"vendor":"Synthetic"}
    config["target_profile"]["types"]=["clinician_rag","clinician_decision_support","medical_scribe"]
    config["intake"]={q:"Synthetic release acceptance: "+q for q in project.MANDATORY_INTAKE}
    config["governance"]={q:"yes" for q in project.MANDATORY_GOVERNANCE}
    (project_root/"project.yaml").write_text(yaml.safe_dump(config),encoding="utf-8")
    specs={"citation":citation_spec(),"scribe":demo("scribe"),
           "decision":demo("decision_certifiability"),"robustness":demo("robustness")}
    frozen=lane.build_plan(project_root,specs)
    require(set(frozen["modules"])==set(specs),"module inventory differs")
    plans={k:v["plan"] for k,v in frozen["modules"].items()}
    packages={}

    # Exact structured candidate response: one expected certificate per probe.
    plan=plans["decision"]
    responses=decision.response_template(plan)
    for item,row in zip(plan["items"],responses["responses"]):
        ref=item["reference"]
        row.update(status="complete",verdict=ref["verification"]["verdict"],
                   certificate=ref["certificate"],requested_facts=ref["unknown_facts"])
        row["raw_response"]=json.dumps({k:row[k] for k in ("verdict","certificate","requested_facts")})
    packages["decision"]=lane.complete_module(project_root,frozen,frozen["digest"],"decision",
                                write(root/"decision.json",responses),confirmed=True)
    require(packages["decision"]["report"]["summary"]["verdict_agreement"]["rate_on_assessed"]==1,
            "synthetic native decision agreement differs")

    # Controlled citation context: one claim on every probe, with exact spans.
    plan=plans["citation"]
    responses=lane.response_template(project_root,frozen,frozen["digest"],"citation")
    byid={q["query_id"]:q for q in plan["queries"]}
    for row in responses["traces"]:
        q=byid[row["query_id"]]
        sid=q["source_ids"][0] if q["source_ids"] else "MISSING"
        claim="Fictional fact Z is required."
        row.update(model_status="complete",answer=claim+" ["+sid+"]",
                   atomic_claims=[{"claim_id":"claim","text":claim,"answer_start":0,
                                   "answer_end":len(claim),"cited_source_ids":[sid]}])
    def cite_fill(doc):
        for row in doc["submission"]["coverage"]:
            row["status"]="complete"
        for row,item in zip(doc["submission"]["support"],doc["request"]["claim_source_pairs"]):
            supports=item["source_id"]=="CURRENT"
            row.update(verdict="supports" if supports else "does_not_support",
                       evidence_quote=item["source_text"] if supports else "")
    packages["citation"]=complete_review(root,project_root,frozen,"citation",responses,cite_fill,("cite-a","cite-b"))
    require(packages["citation"]["report"]["summary"]["baseline"]["unsupported_rate_on_assessed"]==0,
            "synthetic citation baseline differs")

    # Scribe: explicitly omit the allergy, examination and follow-up facts.
    plan=plans["scribe"]
    responses=scribe.response_template(plan)
    note="Patient reports headache."
    responses["responses"][0].update(status="complete",note=note,
        claims=[{"claim_id":"headache","start":0,"end":len(note),"quote":note}])
    def scribe_fill(doc):
        for row in doc["submission"]["cases"]:
            row.update(reference_coverage="complete",claim_coverage="complete")
            for fact in row["facts"]:
                documented=fact["fact_id"]=="headache"
                fact.update(reference_validity="valid",documentation="documented" if documented else "omitted",
                            note_evidence=[{"start":0,"end":len(note),"quote":note}] if documented else [],
                            rationale="Fictional acceptance rating.")
            for claim in row["claims"]:
                quote="I have a headache."
                claim.update(verdict="supported",importance="important",
                            transcript_evidence=[{"turn_id":"t2","start":0,"end":len(quote),"quote":quote}],
                            rationale="Fictional acceptance rating.")
    packages["scribe"]=complete_review(root,project_root,frozen,"scribe",responses,scribe_fill,("scribe-a","scribe-b"))
    facts=packages["scribe"]["report"]["rows"][0]["facts"]
    require(any(f["fact_id"]=="allergy" and f["status"]=="omitted" for f in facts),
            "synthetic scribe omission disappeared")

    # Paired robustness: a deliberately unsafe distractor arm.
    plan=plans["robustness"]
    responses=robustness.response_template(plan)
    kinds={a["slot"]:a["kind"] for a in plan["arms"]}
    for row in responses["responses"]:
        row.update(status="complete",answer="Fictional response: "+kinds[row["slot"]])
    def robustness_fill(doc):
        for row in doc["submission"]["rows"]:
            row["rationale"]="Fictional acceptance rating."
            if doc["request"]["role"]=="reference_validity":
                row["validity"]="valid"
            else:
                kind=kinds[row["slot"]]
                defect=kind=="add_distractor"
                row.update(safety="unsafe" if defect else "safe",
                           unsupported_inference="present" if defect else "absent",
                           adequacy="inadequate" if defect else "adequate",
                           decision_group="unsafe" if defect else "defer" if kind=="decontextualize_query" else "option-a")
    packages["robustness"]=complete_review(root,project_root,frozen,"robustness",responses,
                            robustness_fill,("answer-a","answer-b"),("valid-a","valid-b"))
    metric=packages["robustness"]["report"]["summary"]["transforms"]["add_distractor"]["metrics"]["adverse_degradation"]
    require(metric["count"]==1,"synthetic paired defect disappeared")

    result=lane.assemble(project_root,frozen,frozen["digest"],packages)
    require(result["all_planned_modules_replayed"] and not result["decision_grade"],"invalid combined receipt")
    require(not any(r["canonical_requirement_satisfied"] for r in result["coverage"]),"canonical claim promoted")
    fp=write(root/"frozen.json",frozen)
    rp=write(root/"result.json",result)
    # Fresh interpreter and working directory outside the checkout.
    env={k:v for k,v in os.environ.items() if k not in ("PYTHONPATH","CAEVAL_HOME")}
    proc=subprocess.run([sys.executable,"-m","caeval.cli","experimental-project","verify",
                         "--project",str(project_root),"--plan",str(fp),"--approve-plan",frozen["digest"],
                         "--result",str(rp),"--expected-digest",result["digest"]],
                        cwd=root,env=env,capture_output=True,text=True,check=True,timeout=120)
    receipt=json.loads(proc.stdout)
    require(receipt["ok"] and receipt["result"]["status"]=="REPLAY_VERIFIED","fresh process replay failed")
    require(receipt["result"]["expected_digest_matched"],"independent digest mismatch")
    print(json.dumps({"status":"PASS","modules":sorted(packages),"decision_grade":False,
                      "canonical_requirements_satisfied":0,"synthetic":True},sort_keys=True))


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="caeval-release-acceptance-") as tmp:
        run(tmp)
