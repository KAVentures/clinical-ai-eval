"""Administrative evidence-gap projection; no acceptance or execution decision."""
import hashlib
import json

READY = frozenset(('completed', 'report_available', 'reports_available', 'sealed'))
ACTIVE = frozenset(('prepared_not_executed', 'queued', 'running', 'collecting', 'reviewing', 'ready'))
COVERAGE = frozenset(('unlinked', 'stale_plan', 'source_available', 'source_changed', 'source_unavailable'))
SCAN_STATES = frozenset(('bounded_scan', 'bounded_legacy_scan', 'partial', 'unavailable', 'stale_plan'))


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _unique(rows, key):
    out = {}
    for r in rows:
        ident = key(r)
        _require(ident not in out, 'duplicate evidence scope')
        out[ident] = r
    return out


def _seal(doc):
    raw = json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    return {**doc, 'digest': hashlib.sha256(raw.encode('ascii')).hexdigest()}


def _condition(req, source):
    # Same administrative ceiling as hospital_referral.snapshot. This is not
    # a clinical pass or proof that every native acceptance condition is met.
    if req['family'] is not None:
        return ('internal_regression_only' if req['kind'] in ('text', 'conversation') and
                source.get('mode') == source.get('claim') == 'internal_regression' and
                source.get('scope') == 'exact_buyer_plan' else 'canonical_evidence_gap')
    if req['kind'] == 'imaging' and source.get('complete_roster') is not True:
        return 'incomplete_imaging_roster'
    return 'noncanonical_source_available'


def _task(code, role, priority):
    return {'code': code, 'role': role, 'priority': priority}


def _row(req, linked, work, current):
    state = linked['state']; source = linked['locked_source']; scan = work['work_inventory_state']
    _require(state in COVERAGE and scan in SCAN_STATES, 'unknown evidence or work state')
    _require((current and state != 'stale_plan' and scan != 'stale_plan') or
              (not current and state == scan == 'stale_plan'), 'inconsistent programme freshness')
    _require((state == 'unlinked' and source is None) or (state == 'stale_plan') or
              (state in ('source_available', 'source_changed', 'source_unavailable') and isinstance(source, dict)),
              'inconsistent source association')
    records = work['native_work_records']
    _require(isinstance(records, list), 'invalid native work inventory')
    ready = sum(r['state'] in READY for r in records)
    active = sum(r['state'] in ACTIVE for r in records)
    partial = scan in ('partial', 'unavailable', 'bounded_legacy_scan') or work['scan_truncated'] is True
    tasks = []
    condition = 'not_associated'
    if not current:
        condition = 'stale_programme'; tasks.append(_task('review_stale_programme', 'admin', 'high'))
    elif state in ('source_changed', 'source_unavailable'):
        condition = 'locked_source_needs_investigation'; tasks.append(_task('investigate_locked_source', 'admin', 'high'))
    elif state == 'source_available':
        condition = _condition(req, source)
        if condition in ('canonical_evidence_gap', 'incomplete_imaging_roster'):
            tasks.append(_task('review_evidence_limitations', 'admin', 'high'))
    else:
        if scan in ('partial', 'unavailable'):
            tasks.append(_task('investigate_native_inventory', 'operator', 'high'))
        if ready:
            tasks.append(_task('verify_candidate_association', 'admin', 'normal'))
        if active:
            tasks.append(_task('continue_native_work', 'operator', 'normal'))
        if not ready and not active and scan != 'unavailable':
            tasks.append(_task('inspect_or_prepare_native_work', 'operator', 'normal'))
    return {'requirement_id':req['id'], 'plan_id':req['plan_id'], 'kind':req['kind'], 'family':req['family'],
            'vendor_id':linked['vendor_id'], 'target':linked['target'], 'coverage_state':state,
            'condition':condition, 'source_mode':source.get('mode') if source else None,
            'source_claim':source.get('claim') if source else None,
            'work_inventory_state':scan, 'work_inventory_incomplete':partial,
            'potential_ready_records':ready, 'active_work_records':active, 'tasks':tasks,
            'canonical_requirements_satisfied':False, 'decision_grade':False}


def project(status, work):
    programme = status['programme']
    ident = programme['programme_id']; current = status['state'] == 'current'
    _require(status['state'] in ('current','stale_plan') and work['programme_state'] == status['state'] and
             work['programme_id'] == ident and work['programme_digest'] == programme['digest'], 'programme projection mismatch')
    plans = _unique(programme['plans'], lambda r:r['plan_id'])
    reqs = _unique(programme['requirements'], lambda r:r['id'])
    links = _unique(status['requirements'], lambda r:r['requirement']['id'])
    jobs = _unique(work['requirements'], lambda r:r['requirement_id'])
    _require(set(reqs) == set(links) == set(jobs), 'incomplete requirement projection')
    rows = []
    selectors = set()
    for rid, req in sorted(reqs.items()):
        plan=plans[req['plan_id']]; link=links[rid]; job=jobs[rid]
        selector=(req['plan_id'],req['family'])
        if req['family'] is not None:
            _require(selector not in selectors, 'duplicate canonical suite selector')
            selectors.add(selector)
        _require(link['requirement'] == req and link['vendor_id'] == job['vendor_id'] == plan['vendor_id'] and
                 link['target'] == job['target'] == plan['target'] and
                 all(job[k] == req[k] for k in ('plan_id','kind','family')) and
                 job['coverage_state'] == link['state'], 'requirement projection scope mismatch')
        rows.append(_row(req,link,job,current))
    canonical = []
    for pid, plan in sorted(plans.items()):
        required=plan['required_suites']; runnable=plan['runnable_suites']
        _require(len(set(required)) == len(required), 'duplicate required suite')
        for suite in sorted(required):
            match=[r for r in rows if r['plan_id']==pid and r['family']==suite]
            _require(len(match)<=1, 'ambiguous canonical suite')
            condition=(match[0]['condition'] if match else
                       'not_planned_in_this_programme' if suite in runnable else 'required_but_not_run')
            canonical.append({'plan_id':pid,'vendor_id':plan['vendor_id'],'target':plan['target'], 'suite':suite,
                              'requirement_id':match[0]['requirement_id'] if match else None,'condition':condition,
                              'canonical_requirements_satisfied':False})
    omitted={(r['plan_id'],r['suite'],r['condition']) for r in canonical if r['requirement_id'] is None}
    expected={(r['plan_id'],r['suite'],r['state']) for r in programme['unplanned_canonical_suites']}
    _require(omitted==expected and len(expected)==len(programme['unplanned_canonical_suites']), 'canonical gap inventory mismatch')
    return _seal({'schema':'unified-evidence-gaps/1','programme_id':ident,'programme_digest':programme['digest'],
                  'programme_state':status['state'],'requirements':rows,'canonical_suites':canonical,
                  'partial_work_inventory':any(r['work_inventory_incomplete'] for r in rows),
                  'canonical_requirements_satisfied':False,'decision_grade':False,'deployment_authorized':False,
                  'limitations':['Potential ready work is not verified source eligibility; use the native association preview.',
                                 'Internal regression evidence is not canonical acceptance or clinical validity.',
                                 'Administrative priority is not clinical urgency. No work is executed.']})
