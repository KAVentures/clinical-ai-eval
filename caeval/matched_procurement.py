"""Matched, case-clustered exploratory procurement contrasts.

No clinical verdict, ranking, equivalence or non-inferiority claim.
A risk-bound family must have its complete verified roster and exact
frozen cell inventory. Case-level sign flips preserve within-case dependence.
"""
from collections import Counter
from itertools import combinations

MAX_CELLS=4000
MAX_DP_WORK=2_000_000
MAX_CONTRASTS=500
METHOD="case_cluster_exact_sign_flip"


def _index(entry,expected):
    out={}
    for row in entry["cells"]:
        cid=row.get("cell_id")
        cluster=row.get("item_id") or row.get("case_id")
        if (not isinstance(cid,str) or not cid or cid in out or
                not isinstance(cluster,str) or not cluster):
            raise ValueError("invalid or duplicate cell or missing case cluster")
        if row.get("item_id") and row.get("case_id") and row["item_id"]!=row["case_id"]:
            raise ValueError("conflicting case cluster identifiers")
        out[cid]=(cluster,row)
    if set(out)!=set(expected):
        raise ValueError("cell inventory differs from frozen protocol")
    return out


def _binary(value):
    if value is not None and (type(value) is not int or value not in (0,1)):
        raise ValueError("nonbinary paired endpoint")
    return value


def exact_sign_flip(cluster_differences):
    """Two-sided exact conditional randomization tail, if within budget.

    Requires exchangeable product labels within independent case clusters
    under a sharp null. Zero-sum clusters contribute no sign information.
    """
    weights=[abs(d) for d in cluster_differences if d]
    total=sum(weights)
    if len(weights)*(2*total+1)>MAX_DP_WORK:
        return None
    if not weights:
        return 1.0
    observed=abs(sum(cluster_differences))
    counts={0:1}
    for w in weights:
        nxt=Counter()
        for value,n in counts.items():
            nxt[value+w]+=n
            nxt[value-w]+=n
        counts=nxt
    return sum(n for v,n in counts.items() if abs(v)>=observed)/(2**len(weights))


def contrast(a,b,expected,metric):
    """Retain all expected opportunities, including one-sided missingness."""
    expected=list(expected)
    if (not expected or len(expected)>MAX_CELLS or len(set(expected))!=len(expected)
            or any(not isinstance(x,str) or not x for x in expected)):
        raise ValueError("invalid frozen cell inventory")
    ai,bi=_index(a,expected),_index(b,expected)
    diffs=Counter()
    only_a=only_b=both_one=both_zero=missing_a=missing_b=missing_both=0
    lo=hi=0
    for cid in sorted(expected):
        ca,ar=ai[cid]
        cb,br=bi[cid]
        if ca!=cb:
            raise ValueError("case cluster mismatch between products")
        x,y=_binary(ar.get(metric)),_binary(br.get(metric))
        if x is None or y is None:
            missing_a+=x is None and y is not None
            missing_b+=y is None and x is not None
            missing_both+=x is None and y is None
            lo+=(0 if x is None else x)-(1 if y is None else y)
            hi+=(1 if x is None else x)-(0 if y is None else y)
        else:
            d=x-y
            diffs[ca]+=d
            lo+=d
            hi+=d
            only_a+=x==1 and y==0
            only_b+=x==0 and y==1
            both_one+=x==y==1
            both_zero+=x==y==0
    n=len(expected)
    paired=only_a+only_b+both_one+both_zero
    clusters=len({c for c,_ in ai.values()})
    p=None
    status="incomplete_pairs"
    if paired==n:
        if clusters<2:
            status="insufficient_case_clusters"
        else:
            p=exact_sign_flip(list(diffs.values()))
            status="complete_exploratory" if p is not None else "inference_budget_exceeded"
    return {"products":[a["product_id"],b["product_id"]],
            "vendor_ids":[a.get("vendor_id",a["product_id"]),b.get("vendor_id",b["product_id"])],
            "metric":metric,"status":status,"n_expected":n,"n_paired":paired,
            "n_case_clusters":clusters,"n_observed_case_clusters":len(diffs),
            "n_nonzero_cluster_differences":sum(d!=0 for d in diffs.values()),
            "a_only_positive":only_a,"b_only_positive":only_b,
            "both_positive":both_one,"both_negative":both_zero,
            "missing_a_only":missing_a,"missing_b_only":missing_b,"missing_both":missing_both,
            "observed_paired_difference_a_minus_b":(only_a-only_b)/paired if paired else None,
            "all_expected_difference_bounds":[lo/n,hi/n],
            "p_value_unadjusted":p,"p_value_holm_within_family":None,
            "decision_grade":False}


def family(entries,hazards,expected,*,authorized,reason=""):
    """Return all predeclared contrasts or no contrasts; no selective testing."""
    result={"schema":"matched-procurement-contrasts/1","method":METHOD,
            "status":"unavailable","reason":reason,"contrasts":[],
            "multiplicity_scope":"all declared hazard/product-pair contrasts in this family only",
            "limitations":[
                "Exploratory conditional sign-flip tests assume exchangeable labels and independent case clusters; these assumptions are not established by package integrity.",
                "Cell-weighted difference, with all missing values bounded as binary outcomes; bounds are not confidence intervals.",
                "Holm adjustment is within this family only, not across families or studies.",
                "No difference, equivalence, non-inferiority, clinical validity or purchasing decision is established."],
            "decision_grade":False}
    if not authorized:
        return result
    ids=[e["vendor_id"] for e in entries]
    if len(ids)<2 or len(ids)!=len(set(ids)):
        result["reason"]="At least two distinct verified candidates are required."
        return result
    count=len(hazards)*len(ids)*(len(ids)-1)//2
    if not count or count>MAX_CONTRASTS:
        result["reason"]="Contrast inventory empty or exceeds bounded analysis budget."
        return result
    rows=[]
    try:
        for h in hazards:
            if h.get("tests") not in (None,["ALL"]):
                raise ValueError("only frozen all-generated-cell denominators supported")
            for a,b in combinations(sorted(entries,key=lambda x:x["vendor_id"]),2):
                row=contrast(a,b,expected,h["acceptance_criterion"]["metric"])
                row["hazard_id"]=h["hazard_id"]
                rows.append(row)
    except (ValueError,KeyError,TypeError):
        result["reason"]="Frozen cell, case-cluster or binary endpoint inventory failed validation."
        return result
    # Include unavailable contrasts in the multiplicity count (as p=1),
    # so missing outcomes cannot decrease the number of planned tests.
    order=sorted(range(len(rows)),key=lambda i:rows[i]["p_value_unadjusted"]
                 if rows[i]["p_value_unadjusted"] is not None else 1.0)
    running=0.0
    for j,i in enumerate(order):
        p=rows[i]["p_value_unadjusted"]
        running=max(running,min(1.0,(len(rows)-j)*(p if p is not None else 1.0)))
        if p is not None:
            rows[i]["p_value_holm_within_family"]=running
    result.update(status="exploratory",reason="",contrasts=rows)
    return result
