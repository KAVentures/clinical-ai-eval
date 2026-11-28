"""Portable, label-only replay of full-variant calibration.

No raw prompts, responses, case IDs, reviewer IDs, names, paths or credentials.
This is a statistical derivative, not a source evidence or identity verifier.
Small cohorts can still be sensitive; distribution needs institutional approval.
"""
import hashlib
import hmac
import json
import re
from pathlib import Path
from . import review_calibration as cal

SCHEMA="portable-cohort-calibration/1"
LIMIT=2*1024*1024
VERDICTS=("safe","unsafe","cannot_judge")
VALIDITY=("yes","no","cannot_judge")
LIMITATIONS=[
    "Replays the statistical calculation only; source manifests, original reviews, credentials and prospective timing are not independently verified by this export.",
    "Study references are hashes, not signatures; compare the export digest through a separately trusted channel.",
    "Case clusters and judges are numbered locally; clinician vote counts do not preserve individual reviewer identities.",
    "Structured labels can still be sensitive, particularly for small cohorts. Distribution requires separate institutional authorization.",
    "Fixed full-pack coverage is not a representative patient population or clinical deployment approval.",
]


def _canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()


def _digest(obj):
    return hashlib.sha256(_canonical(obj)).hexdigest()


def _strict(obj,fields):
    if not isinstance(obj,dict) or set(obj)!=set(fields):
        raise ValueError("unexpected portable calibration fields")


def _hash(x):
    if not isinstance(x,str) or not re.fullmatch(r"[0-9a-f]{64}",x):
        raise ValueError("invalid digest reference")
    return x


def _int(x,lo,hi):
    if type(x) is not int or not lo<=x<=hi:
        raise ValueError("invalid bounded count")
    return x


def _binary(x):
    if x is not None and (type(x) is not int or x not in (0,1)):
        raise ValueError("invalid automated label")
    return x


def _full_pack(report):
    report["status"]="descriptive_full_pack"
    report["limitations"]=[x for x in report["limitations"] if not x.startswith("The locked review queue is enriched")]
    report["limitations"].insert(0,"All variants of one fixed staged family and case pack; not a random patient-population sample.")
    return report


def project(locked,results,judges,reviewers,validity,*,synthetic):
    """Reduce a full canonical variant cohort to the sufficient label inventory."""
    original=cal.analyze(locked,results,judges,reviewers,validity,synthetic=synthetic)
    if original["n_locked_review_cells"]!=original["n_full_run_variant_cells"]:
        raise ValueError("full variant inventory required")
    by={r["cell_id"]:r for r in results}
    clusters={}
    rows=[]
    for cell in sorted(locked["expected_cells"],key=lambda x:x["cell_id"]):
        cid=cell["cell_id"]
        row=by[cid]
        if cell["mandatory"] is not False:
            raise ValueError("full cohort must not contain enriched strata")
        cluster=clusters.setdefault(row["item_id"],len(clusters)+1)
        complete=row.get("evaluation_complete") is True
        scores=row.get("judge_scores") or {}
        rows.append({"cluster":cluster,
                     "votes":[sum(r[cid]==v for r in reviewers) for v in VERDICTS],
                     "validity":[validity[cid][k] for k in cal.VALIDITY_FIELDS],
                     "complete":complete,
                     "judges":[_binary((scores.get(j) or {}).get("unsafe_overconfident")) if complete else None for j in judges],
                     "panel":[_binary(row.get(k)) if complete else None for k in ("panel_any_unsafe","panel_majority_unsafe")]})
    inputs={"n_reviewers":len(reviewers),"min_reviewers":locked["min_reviewers_per_cell"],
            "n_judges":len(judges),"synthetic":synthetic,"rows":rows}
    # Ensure projection preserves the actual statistics, including denominators.
    for i,report in enumerate(original["comparisons"][:len(judges)]):
        report["name"]="judge-"+str(i+1)
    if replay(inputs)!=_full_pack(original):
        raise ValueError("portable projection changed the calibration")
    return inputs


def replay(inputs):
    _strict(inputs,("n_reviewers","min_reviewers","n_judges","synthetic","rows"))
    nr=_int(inputs["n_reviewers"],2,5)
    minimum=_int(inputs["min_reviewers"],2,5)
    nj=_int(inputs["n_judges"],1,cal.MAX_JUDGES)
    if type(inputs["synthetic"]) is not bool:
        raise ValueError("explicit synthetic marker required")
    rows=inputs["rows"]
    if not isinstance(rows,list) or not 1<=len(rows)<=cal.MAX_CELLS:
        raise ValueError("invalid full cohort inventory")
    judges=["judge-"+str(i+1) for i in range(nj)]
    reviewers=[{} for _ in range(nr)]
    validity={}
    locked=[]
    results=[]
    max_cluster=0
    for i,row in enumerate(rows):
        _strict(row,("cluster","votes","validity","complete","judges","panel"))
        cluster=_int(row["cluster"],1,max_cluster+1)
        max_cluster=max(max_cluster,cluster)
        votes=row["votes"]
        if (not isinstance(votes,list) or len(votes)!=3 or
                sum(_int(x,0,nr) for x in votes)!=nr):
            raise ValueError("invalid clinician vote counts")
        vals=row["validity"]
        if not isinstance(vals,list) or len(vals)!=len(cal.VALIDITY_FIELDS) or any(x not in VALIDITY for x in vals):
            raise ValueError("invalid validity inventory")
        if type(row["complete"]) is not bool:
            raise ValueError("invalid completion marker")
        for key,n in (("judges",nj),("panel",2)):
            if not isinstance(row[key],list) or len(row[key])!=n:
                raise ValueError("invalid automated inventory")
            for x in row[key]:
                _binary(x)
                if not row["complete"] and x is not None:
                    raise ValueError("partial automated labels are not eligible")
        item="case-"+str(cluster)
        cid=item+"::"+str(i+1)
        # The calibration depends on counts, not individual reviewer identity.
        expanded=[v for v,n in zip(VERDICTS,votes) for _ in range(n)]
        for j,v in enumerate(expanded):
            reviewers[j][cid]=v
        validity[cid]=dict(zip(cal.VALIDITY_FIELDS,vals))
        locked.append({"cell_id":cid,"severity":"not_exported","mandatory":False})
        results.append({"cell_id":cid,"item_id":item,"severity":"not_exported","kind":"variant",
                        "evaluation_complete":row["complete"],
                        "judge_scores":{j:{"unsafe_overconfident":x} for j,x in zip(judges,row["judges"])},
                        "panel_any_unsafe":row["panel"][0],"panel_majority_unsafe":row["panel"][1]})
    return _full_pack(cal.analyze({"expected_cells":locked,"n_expected":len(rows),"min_reviewers_per_cell":minimum},
                       results,judges,reviewers,validity,synthetic=inputs["synthetic"]))


def build(inputs,provenance):
    _strict(provenance,("cohort","cohort_digest","source_manifest","institutional_report_digest",
                        "evaluator_and_job_bound","candidate_submission_bound"))
    for k in ("cohort","cohort_digest","source_manifest","institutional_report_digest"):
        _hash(provenance[k])
    for k in ("evaluator_and_job_bound","candidate_submission_bound"):
        if type(provenance[k]) is not bool:
            raise ValueError("invalid provenance marker")
    if provenance["candidate_submission_bound"] and not provenance["evaluator_and_job_bound"]:
        raise ValueError("inconsistent prospective binding markers")
    payload={"schema":SCHEMA,"provenance":provenance,"inputs":inputs,"calibration":replay(inputs),
             "limitations":LIMITATIONS[:],"decision_grade":False}
    doc={**payload,"digest":_digest(payload)}
    if len(_canonical(doc))>LIMIT:
        raise ValueError("portable calibration exceeds size limit")
    return doc


def verify(doc,expected_digest=None):
    _strict(doc,("schema","provenance","inputs","calibration","limitations","decision_grade","digest"))
    expected=build(doc["inputs"],doc["provenance"])
    if _canonical(doc)!=_canonical(expected):
        raise ValueError("portable calibration differs from deterministic replay")
    if expected_digest is not None and not hmac.compare_digest(_hash(expected_digest),doc["digest"]):
        raise ValueError("unexpected portable calibration digest")
    return {"status":"STATISTICAL_REPLAY_VERIFIED","digest":doc["digest"],
            "expected_digest_matched":expected_digest is not None,
            "source_evidence_independently_verified":False,"decision_grade":False,
            "calibration":expected["calibration"]}


def encode(doc):
    verify(doc)
    return _canonical(doc)


def load(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file():
        raise ValueError("regular local file required")
    with p.open("rb") as f:
        raw=f.read(LIMIT+1)
    if len(raw)>LIMIT:
        raise ValueError("portable calibration exceeds size limit")
    def unique(pairs):
        obj={}
        for key,value in pairs:
            if key in obj:
                raise ValueError("duplicate JSON field")
            obj[key]=value
        return obj
    def invalid(x):
        raise ValueError("nonfinite JSON number")
    return json.loads(raw.decode("utf-8"),object_pairs_hook=unique,parse_constant=invalid)


def cli(args):
    try:
        out=verify(load(args.package),args.expected_digest)
        print(json.dumps({"ok":True,"result":out},ensure_ascii=False,allow_nan=False))
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RecursionError,OverflowError):
        print(json.dumps({"ok":False,"error":"Portable calibration verification refused"}))
        raise SystemExit(1)
