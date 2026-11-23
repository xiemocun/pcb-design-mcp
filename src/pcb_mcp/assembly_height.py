"""Bound the installed component envelope using exact source-linked identities."""
import hashlib
import json
import math

from .config import asset_root


def index_path():
    return asset_root() / 'catalog/component-heights.json'


def source_fingerprint():
    path = index_path()
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def source_bound(part, curated):
    identity = part['definition_hash']
    row = curated.get(identity)
    if row:
        if (row['manufacturer'], row['mpn']) != (part['manufacturer'], part['mpn']):
            return None
        if row.get('snapshot_file'):
            path = asset_root() / 'evidence-cache' / row['snapshot_file']
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row['snapshot_sha256']:
                return None
        return row

    facts = part['facts']
    record = facts.get('import_record', {})
    fact = 'seated_height_max_mm'
    references = record.get('evidence', {}).get(fact, [])
    sources = {item['package_asset']: item for item in part['sources'] if 'package_asset' in item}
    if not references or fact not in facts or any(ref['asset'] not in sources for ref in references):
        return None
    return dict(
        manufacturer=part['manufacturer'], mpn=part['mpn'], maximum_mm=facts[fact],
        basis='manufacturer_seated_part_envelope',
        sources=[dict(sources[ref['asset']], locator=ref['locator'], interpretation=ref['interpretation'])
                 for ref in references],
    )


def evaluate(parts_by_ref, limit_mm):
    path = index_path()
    curated = json.loads(path.read_text()) if path.is_file() else {}
    failures = []
    measurements = {}
    for ref, part in sorted(parts_by_ref.items()):
        row = source_bound(part, curated) if part and part['evidence_status'] == 'available' else None
        maximum = row['maximum_mm'] if row else None
        if type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum <= 0:
            failures.append('COMPONENT_HEIGHT_EVIDENCE_UNAVAILABLE:' + ref)
            continue
        measurements[ref] = dict(row, part_definition_hash=part['definition_hash'])
        if maximum > limit_mm:
            failures.append('COMPONENT_HEIGHT_LIMIT_UNPROVEN:' + ref)
    return dict(passed=not failures, limit_mm=limit_mm, measurements=measurements, failures=failures,
                curated_index_sha256=source_fingerprint(), physical_verified=False,
                scope='Component envelope under seated placement without extra spacers or mating hardware.')


def evaluate_state(state, limit_mm):
    from .parts import binding_valid, catalog
    current = {part['candidate_id']: part for part in catalog()}
    selected = {}
    for cid, component in state['components'].items():
        part = state.get('part_bindings', {}).get(cid)
        valid = part and current.get(part['candidate_id']) == part and binding_valid(state, component, part)
        selected[component['ref']] = part if valid else None
    return evaluate(selected, limit_mm)
