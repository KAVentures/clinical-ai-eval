"""Bounded inventory helpers for existing text and conversation workflows."""
import re


def prepared_ids(path, limit=200):
    """Return a sentinel extra ID when the scan is truncated.

    Valid-looking linked or malformed records must reach the verifier, rather
    than disappear from the inventory. Unrelated directory entries are ignored.
    """
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('invalid prepared inventory limit')
    if not path.exists():
        return []
    if not path.is_dir() or path.is_symlink():
        raise ValueError('invalid prepared assessment directory')
    ids = []
    for item in path.iterdir():
        if re.fullmatch(r'[0-9a-f]{64}', item.name):
            ids.append(item.name)
            if len(ids) > limit:
                break
    return sorted(ids)


def text_scope(exchange, plan, family):
    if exchange['plan_id'] != plan['plan_id']:
        return False
    if (exchange['vendor_id'] != plan['vendor_id'] or
            {k: exchange['target'][k] for k in ('name', 'version')} != plan['target']):
        raise ValueError('text exchange candidate scope mismatch')
    families = exchange['family_ids']
    if not isinstance(families, list) or len(set(families)) != len(families):
        raise ValueError('invalid text exchange family inventory')
    return family in families


def merge_text(prepared, jobs):
    used = {r['assessment_id'] for r in jobs['records']}
    records = [r for r in prepared['records'] if r['id'] not in used] + jobs['records']
    failed = prepared['unverified_records'] + jobs['unverified_records']
    truncated = prepared['scan_truncated'] or jobs['scan_truncated']
    return {'records': records, 'state': 'partial' if failed or truncated else 'bounded_scan',
            'scan_truncated': truncated, 'unverified_records': failed}
