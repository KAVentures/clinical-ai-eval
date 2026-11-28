"""Matched clinician-versus-model binary decisions against an independent panel."""
from itertools import product
from . import predictive_research as pred, predictive_reference_panel as panel, risk_probability as risk

SCHEMA = 'predictive-human-comparison/1'


def freeze(plan, expected, reference_panel, panel_digest, spec):
    p = pred._plan(plan, expected)
    ref = panel._panel(plan, expected, reference_panel, panel_digest)
    pred.fields(spec, 'clinicians threshold_bp decision_protocol_sha256 decision_input_sha256')
    readers = spec['clinicians']
    pred.require(isinstance(readers, list) and 1 <= len(readers) <= 30)
    pred.require(all(risk._token(x) for x in readers) and len(set(readers)) == len(readers))
    pred.require(not set(readers).intersection(ref['actors'].values()))
    pred.require(type(spec['threshold_bp']) is int and spec['threshold_bp'] in p['thresholds_bp'])
    pred.require(pred.hex64(spec['decision_protocol_sha256']))
    inv = spec['decision_input_sha256']
    pred.require(isinstance(inv, dict) and set(inv) == set(p['unit_ids']) and all(pred.hex64(v) for v in inv.values()))
    body = {'schema': SCHEMA, 'plan_sha256': expected, 'panel_sha256': panel_digest,
            'spec': {**spec, 'clinicians': sorted(readers), 'decision_input_sha256': dict(inv)}, 'unit_ids': p['unit_ids'],
            'action': p['action'], 'endpoint': p['endpoint'], 'horizon': p['horizon']}
    return {**body, 'comparison_sha256': pred.digest(body)}


def check(plan, expected, reference_panel, panel_digest, comparison, digest):
    rebuilt = freeze(plan, expected, reference_panel, panel_digest, comparison['spec'])
    pred.require(pred.hex64(digest) and rebuilt['comparison_sha256'] == digest and pred.canonical(rebuilt) == pred.canonical(comparison))
    return rebuilt


def seal(plan, expected, reference_panel, panel_digest, comparison, digest, clinician, source_receipt, rows):
    c = check(plan, expected, reference_panel, panel_digest, comparison, digest)
    pred.require(clinician in c['spec']['clinicians'] and pred.hex64(source_receipt))
    pred.require(isinstance(rows, list) and len(rows) <= len(c['unit_ids']))
    values = {}
    eligible = set(c['unit_ids'])
    for r in rows:
        pred.fields(r, 'unit_id action input_sha256')
        u, value = r['unit_id'], r['action']
        pred.require(risk._token(u) and u in eligible and u not in values and (value is None or type(value) is bool))
        pred.require(r['input_sha256'] == c['spec']['decision_input_sha256'][u])
        values[u] = value
    body = {'schema': SCHEMA, 'comparison_sha256': digest, 'clinician': clinician, 'source_receipt_sha256': source_receipt,
            'rows': [{'unit_id': u, 'action': values[u], 'input_sha256': c['spec']['decision_input_sha256'][u]} for u in sorted(values)]}
    return {**body, 'submission_sha256': pred.digest(body)}


def _poss(value):
    return (False, True) if value is None else (value,)


def metrics(rows):
    """Integer numerator bounds, shared reference per unit; no independence assumption."""
    n = len(rows)
    pred.require(n > 0)
    counts = dict(both_correct=0, both_wrong=0, model_only_correct=0, clinician_only_correct=0)
    lower = [0, 0, 0]; upper = [0, 0, 0]
    for m, h, y in rows:
        if m is not None and h is not None and y is not None:
            counts[{(True, True): 'both_correct', (False, False): 'both_wrong', (True, False): 'model_only_correct', (False, True): 'clinician_only_correct'}[(m == y, h == y)]] += 1
        vals = [(int(a == c), int(b == c), int(a == c)-int(b == c)) for a,b,c in product(_poss(m), _poss(h), _poss(y))]
        for i in range(3):
            lower[i] += min(v[i] for v in vals); upper[i] += max(v[i] for v in vals)
    known = sum(counts.values())
    return {'n_planned': n, 'n_complete_pairs': known, 'complete_pair_counts': counts,
            'complete_pair_delta_numerator': counts['model_only_correct']-counts['clinician_only_correct'],
            'complete_pair_delta_denominator': known,
            'cohort_denominator': n, 'model_correct_count_bounds': [lower[0],upper[0]],
            'clinician_correct_count_bounds': [lower[1],upper[1]], 'model_minus_clinician_correct_count_bounds': [lower[2],upper[2]]}


def analyze(plan, expected, reference_panel, panel_digest, comparison, digest, *, primary, expected_primary,
            adjudication, expected_adjudication, expected_reference, model, expected_model, submissions, expected_submissions):
    c = check(plan, expected, reference_panel, panel_digest, comparison, digest)
    ref = panel.finalize(plan, expected, reference_panel, panel_digest, primary, expected_primary, adjudication, expected_adjudication)
    pred.require(ref['receipt']['receipt_sha256'] == expected_reference)
    labels = pred._bundle(plan, expected, 'reference', ref['bundle'], ref['bundle']['bundle_sha256'])
    scores = pred._bundle(plan, expected, 'candidate', model, expected_model)
    roster = c['spec']['clinicians']
    pred.require(isinstance(submissions, dict) and isinstance(expected_submissions, dict) and set(submissions) == set(expected_submissions) == set(roster))
    reports = {}
    for clinician in roster:
        s = submissions[clinician]
        rebuilt = seal(plan, expected, reference_panel, panel_digest, c, digest, clinician, s['source_receipt_sha256'], s['rows'])
        pred.require(rebuilt['submission_sha256'] == expected_submissions[clinician] and pred.canonical(rebuilt) == pred.canonical(s))
        decisions = {r['unit_id']:r['action'] for r in s['rows']}
        threshold = c['spec']['threshold_bp']
        rows = [(None if scores.get(u) is None else scores[u] >= threshold, decisions.get(u), labels.get(u)) for u in c['unit_ids']]
        reports[clinician] = metrics(rows)
    body = {'schema': SCHEMA, 'comparison_sha256': digest, 'reference_receipt_sha256': expected_reference,
            'model_bundle_sha256': expected_model, 'clinician_submission_sha256': dict(expected_submissions),
            'threshold_bp': c['spec']['threshold_bp'], 'by_clinician': reports,
            'reference_identity_verified': False, 'external_blinding_verified': False,
            'decision_grade': False, 'publication_safe': False}
    return {**body, 'report_sha256': pred.digest(body)}


def verify(*args, report, expected_report, **kwargs):
    rebuilt = analyze(*args, **kwargs)
    return rebuilt['report_sha256'] == expected_report and pred.canonical(rebuilt) == pred.canonical(report)
