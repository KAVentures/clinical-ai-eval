"""Exact-input offline rehearsal of the canonical retrieval_failure executor.

Uses only the bundled synthetic fixture. It tests generation under supplied
perturbed context, not the candidate's own retrieval. Native flags are lexical
heuristics, not independent clinical or citation-support adjudication.
"""
import json
import secrets
from dataclasses import asdict
from . import diagnostic_imaging as d, manifest, packsource
from .rag import execute as native
from .rag.corpus import build_demo_corpus

FAMILY="retrieval_failure"
PACK="builtin:demo_rag_corpus"
MAX_ANSWER=12000
LIMITATIONS=[
    "Bundled synthetic demonstration only; not real clinical guidance.",
    "Fixed evaluator retrieval context, not measurement of the candidate's own retriever.",
    "Native lexical abstention flags and citation-ID checks are not clinical verdicts.",
    "No positive control, independent clinician review or citation-support adjudication.",
    "Candidate identity and response provenance are operator-attested, not verified.",
    "No real model calls, clinical validity, canonical requirement satisfaction or deployment authorization.",
]


def _json(value):
    return json.loads(json.dumps(value,ensure_ascii=True,allow_nan=False))


def _definitions():
    sources=set(manifest.DEFINITION_SOURCES)|set(manifest.EXECUTOR_DEFINITION_SOURCES["rag_trace"])
    sources.update(("tests/"+FAMILY+"/family.yaml","caeval/institution_rag_trace.py",
                    "caeval/packsource.py","caeval/executors.py"))
    result={rel:manifest.hash_file(manifest.definition_file(rel)) for rel in sorted(sources)}
    d._require(all(result.values()),"RAG scientific definitions missing")
    return result


def _run(answers=None):
    queries,desc=packsource.resolve({"source":PACK},"rag_corpus_bound")
    corpus=build_demo_corpus()
    inputs=[]
    def subject(record):
        key=(record["item_id"],record["perturbation_type"])
        prompt=record["input_text"]
        inputs.append({"query_id":key[0],"probe_id":key[1],"prompt":prompt})
        return "Synthetic placeholder" if answers is None else answers[key]
    result=native.run_family(FAMILY,subject,queries,corpus=corpus)
    expected={(q["query_id"],p) for q in queries for p in native.PROBES_BY_FAMILY[FAMILY]}
    actual={(r["query_id"],r["probe_id"]) for r in inputs}
    d._require(actual==expected and len(inputs)==len(expected) and not result["skipped"],
               "incomplete native RAG fixture inventory")
    d._require(len({r["prompt"] for r in inputs})==len(inputs),
               "duplicate native probe contexts; distinct probes required")
    # Keep the exact native report except timestamps, which are execution
    # metadata, not scientific outcomes. Normalize tuples for JSON replay.
    for r in result["traces"]+result["records"]:
        r.pop("created_at",None)
    return _json(result),inputs,desc,{"corpus_id":corpus.corpus_id,"version":corpus.version,
                                     "provenance":corpus.provenance,
                                     "documents":[asdict(doc) for doc in corpus.documents]}


def freeze(plan_id,vendor_id,target,*,nonce=None):
    d._require(d._hex(plan_id) and d._token(vendor_id),"exact buyer identity required")
    d._keys(target,"name version")
    d._require(all(isinstance(v,str) and 0<len(v)<=300 and v.strip()==v for v in target.values()),
               "exact product name and version required")
    nonce=secrets.token_hex(32) if nonce is None else nonce
    d._require(d._hex(nonce),"invalid rehearsal nonce")
    _,inputs,desc,corpus=_run()
    slots=[{**r,"slot":d._digest([nonce,r["query_id"],r["probe_id"]]),
            "prompt_digest":d._digest(r["prompt"])} for r in inputs]
    return d._sealed({"schema":"institution-rag-trace-plan/1","plan_id":plan_id,"vendor_id":vendor_id,
                      "target":dict(target),"family":FAMILY,"pack":desc,"corpus_digest":d._digest(corpus),
                      "definitions":_definitions(),"private_nonce":nonce,"slots":sorted(slots,key=lambda r:r["slot"]),
                      "decision_grade":False,"limitations":LIMITATIONS[:]})


def verify_plan(plan):
    d._keys(plan,"schema plan_id vendor_id target family pack corpus_digest definitions private_nonce slots decision_grade limitations digest")
    d._require(freeze(plan["plan_id"],plan["vendor_id"],plan["target"],nonce=plan["private_nonce"])==plan,
               "RAG rehearsal plan or scientific definitions changed")


def exchange(plan):
    verify_plan(plan)
    return {"schema":"institution-rag-trace-exchange/1","plan_digest":plan["digest"],
            "target":plan["target"],"synthetic":True,
            "slots":[{k:r[k] for k in ("slot","prompt","prompt_digest")} for r in plan["slots"]]}


def template(plan):
    ex=exchange(plan)
    return {"schema":"institution-rag-trace-responses/1","plan_digest":ex["plan_digest"],
            "target":ex["target"],"responses":[{"slot":r["slot"],"prompt_digest":r["prompt_digest"],"answer":""}
                                             for r in ex["slots"]]}


def _responses(plan,doc):
    verify_plan(plan)
    d._keys(doc,"schema plan_digest target responses")
    d._require(doc["schema"]=="institution-rag-trace-responses/1" and doc["plan_digest"]==plan["digest"]
               and doc["target"]==plan["target"],"response plan or candidate mismatch")
    rows=doc["responses"]
    by={r["slot"]:r for r in plan["slots"]}
    d._require(isinstance(rows,list) and len(rows)==len(by),"exact complete response inventory required")
    answers={}
    for r in rows:
        d._keys(r,"slot prompt_digest answer")
        slot=r["slot"]
        d._require(d._hex(slot) and slot in by and r["prompt_digest"]==by[slot]["prompt_digest"],
                   "response prompt binding mismatch")
        answer=r["answer"]
        d._require(isinstance(answer,str) and answer.strip() and len(answer.encode("utf-8"))<=MAX_ANSWER,
                   "nonempty bounded synthetic response required")
        key=(by[slot]["query_id"],by[slot]["probe_id"])
        d._require(key not in answers,"duplicate response slot")
        answers[key]=answer
    return answers


def package(plan,doc):
    answers=_responses(plan,doc)
    result,inputs,desc,corpus=_run(answers)
    d._require(desc==plan["pack"] and d._digest(corpus)==plan["corpus_digest"],
               "native corpus or case pack mismatch")
    d._require({(r["query_id"],r["probe_id"],d._digest(r["prompt"])) for r in inputs}==
               {(r["query_id"],r["probe_id"],r["prompt_digest"]) for r in plan["slots"]},
               "native prompt inventory mismatch")
    return d._sealed({"schema":"institution-rag-trace-evidence/1","plan":plan,
                      "responses":{**doc,"responses":sorted(doc["responses"],key=lambda r:r["slot"])},
                      "native_run":result,"corpus":corpus,"mode":"demonstration",
                      "canonical_requirements_satisfied":False,"decision_grade":False,
                      "limitations":LIMITATIONS[:]})


def verify(evidence,expected_digest=None):
    d._keys(evidence,"schema plan responses native_run corpus mode canonical_requirements_satisfied decision_grade limitations digest")
    rebuilt=package(evidence["plan"],evidence["responses"])
    d._require(rebuilt==evidence and (expected_digest is None or expected_digest==rebuilt["digest"]),
               "native RAG replay or trusted digest mismatch")
    return {"status":"REPLAY_VERIFIED","digest":rebuilt["digest"],"expected_digest_matched":expected_digest is not None,
            "n_traces":rebuilt["native_run"]["summary"]["n_traces"],"mode":"demonstration",
            "decision_grade":False}


def configure(parser):
    parser.add_argument("evidence")
    parser.add_argument("--expected-digest")
    parser.set_defaults(func=cli)


def cli(args):
    try:
        print(json.dumps(verify(d._read(args.evidence),args.expected_digest),allow_nan=False))
    except (ValueError,RuntimeError,KeyError,TypeError,OSError,AttributeError,RecursionError):
        raise SystemExit("RAG rehearsal evidence verification refused.")
