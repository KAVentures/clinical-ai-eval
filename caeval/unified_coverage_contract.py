"""Structural invariants shared by portable administrative coverage exports.

Does not authenticate source records or re-evaluate clinical evidence.
"""
import re


def require(ok, message):
    if not ok:
        raise ValueError(message)


def keys(doc, fields):
    require(isinstance(doc, dict) and set(doc)==set(fields.split()), 'unexpected coverage fields')


def token(value):
    return isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',value) is not None


def hex64(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def text(value,limit=300):
    return isinstance(value,str) and 1<=len(value)<=limit and bool(value.strip())


def unique_tokens(values, limit=100):
    return isinstance(values,list) and len(values)<=limit and all(token(v) for v in values) and len(set(values))==len(values)


def validate_programme(p, *, kinds, executor_for, executors, max_plans=24, max_requirements=80):
    require(token(p['programme_id']), 'invalid programme identity')
    plans=p['plans']; reqs=p['requirements']; gaps=p['unplanned_canonical_suites']
    require(isinstance(plans,list) and 1<=len(plans)<=max_plans and isinstance(reqs,list) and
            1<=len(reqs)<=max_requirements and isinstance(gaps,list), 'invalid programme inventory')
    by={}; vendors={}
    for plan in plans:
        keys(plan,'plan_id vendor_id target profiles required_suites runnable_suites required_but_not_run')
        pid=plan['plan_id']; vendor=plan['vendor_id']; target=plan['target']
        keys(target,'name version')
        require(hex64(pid) and pid not in by and token(vendor) and all(text(v) for v in target.values()), 'invalid plan identity')
        require(unique_tokens(plan['profiles']) and bool(plan['profiles']), 'invalid plan profiles')
        required=plan['required_suites']; runnable=plan['runnable_suites']; blocked=plan['required_but_not_run']
        require(unique_tokens(required) and unique_tokens(runnable) and set(runnable)<=set(required) and
                isinstance(blocked,list) and len(blocked)<=len(required), 'invalid suite inventory')
        blocked_ids=set()
        for item in blocked:
            keys(item,'suite blocked_reason')
            suite=item['suite']
            require(token(suite) and suite not in blocked_ids and text(item['blocked_reason'],2000), 'invalid blocked suite')
            blocked_ids.add(suite)
        require(blocked_ids==set(required)-set(runnable), 'blocked suite inventory mismatch')
        require(vendor not in vendors or vendors[vendor]==target, 'multiple target versions for vendor')
        by[pid]=plan; vendors[vendor]=target
    seen=set(); selectors=set(); covered=set(); planned=set()
    for req in reqs:
        keys(req,'id kind plan_id family')
        rid=req['id']; kind=req['kind']; pid=req['plan_id']; family=req['family']
        require(token(rid) and rid not in seen and kind in kinds and hex64(pid) and pid in by, 'invalid requirement identity')
        if kind in executors:
            require(token(family) and family in by[pid]['runnable_suites'] and executor_for(family)==executors[kind], 'invalid canonical executor selection')
            planned.add((pid,family))
        else:
            require(family is None, 'noncanonical requirement claims a suite')
        selector=(kind,pid,family)
        require(selector not in selectors, 'duplicate requirement selector')
        seen.add(rid); selectors.add(selector); covered.add(pid)
    require(covered==set(by), 'uncovered buyer plan')
    expected={(pid,suite,'not_planned_in_this_programme' if suite in plan['runnable_suites'] else 'required_but_not_run')
              for pid,plan in by.items() for suite in plan['required_suites'] if (pid,suite) not in planned}
    require(len(gaps)==len(expected), 'omitted suite inventory mismatch')
    actual=set()
    for gap in gaps:
        keys(gap,'plan_id suite state')
        require(hex64(gap['plan_id']) and token(gap['suite']) and gap['state'] in ('not_planned_in_this_programme','required_but_not_run'), 'invalid omitted suite')
        actual.add((gap['plan_id'],gap['suite'],gap['state']))
    require(actual==expected, 'omitted suite inventory mismatch')
    return by


def validate_source(kind, source):
    require(token(source['source_id']) and text(source['mode'],100) and text(source['claim'],100), 'invalid source metadata')
    expected_scope='vendor_id_and_version_only' if kind in ('imaging','rag_grounding') else 'exact_buyer_plan'
    require(source['scope']==expected_scope, 'source scope mismatch')
    if kind in ('text','conversation'):
        require(source['claim'] in ('none','demonstration','internal_regression'), 'invalid canonical source claim')
    else:
        mode,claim={
            'research':('experimental_integrity_checked','not_canonical'),
            'imaging':('experimental_imaging','not_canonical'),
            'rag_grounding':('experimental_rag_grounding','not_canonical'),
            'rag':('demonstration','synthetic_context_rehearsal_only'),
            'fhir':('synthetic_readiness','not_clinical_validity'),
            'hl7':('synthetic_readiness','not_clinical_validity'),
            'image_text':('synthetic_readiness','not_clinical_validity'),
        }[kind]
        require((source['mode'],source['claim'])==(mode,claim), 'invalid native source authority')
    if kind=='imaging':
        require(type(source['complete_roster']) is bool, 'invalid imaging roster flag')
    for field in ('reference_digest','transport_receipt_digest','collection_receipt_digest'):
        if field in source:
            require(source[field] is None or hex64(source[field]), 'invalid ancillary source digest')
