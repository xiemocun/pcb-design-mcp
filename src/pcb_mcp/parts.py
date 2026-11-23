"""Built-in and immutable imported source-linked manufacturer records."""
import json,math
from pathlib import Path
from . import backend,footprints
from .config import asset_root
ROOT=asset_root()

def catalog():
    data=json.loads((ROOT/'catalog/parts.json').read_text());result=[]
    for p in data['parts']:
        paths=footprints.entries(p['footprint_id'],library=p['footprint_id'].split(':',1)[0]);assert len(paths)==1,p['footprint_id']
        fp=footprints.load(*paths[0]);status='available'
        for source in p['sources']:
            if 'local_snapshot_file' in source:
                path=ROOT/'evidence-cache'/source['local_snapshot_file']
                if not path.is_file() or backend.digest(path.read_bytes())!=source.get('pdf_sha256',source.get('snapshot_sha256')):status='missing_or_changed'
        core=dict(p,footprint_definition_hash=fp['definition_hash'],catalog_version=data['catalog_version'])
        sha=backend.digest(json.dumps(core,sort_keys=True).encode())
        facts={k:v for k,v in p.items() if k not in ['manufacturer','mpn','kind','symbol_id','footprint_id','sources','skus','qualification']}
        facts['footprint_definition_hash']=fp['definition_hash']
        result.append(dict(candidate_id=sha[:32],manufacturer=p['manufacturer'],mpn=p['mpn'],kind=p['kind'],symbol_id=p['symbol_id'],footprint_id=p['footprint_id'],definition_hash=sha,facts=facts,sources=p['sources'],skus=p['skus'],evidence_status=status,qualification=p['qualification']))
    from .catalog_import import records
    return result+records()

def rules_hash():
    from . import usb_psd
    from .assembly_height import source_fingerprint
    return backend.digest(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))+json.dumps(catalog(),sort_keys=True).encode()+usb_psd.evidence_hash().encode()+(source_fingerprint() or '').encode())


def reference_catalog():
    """Legacy symbol-specific formulas cannot acquire new ratings by shadowing."""
    return [p for p in catalog() if 'import_record' not in p['facts']]


def bound_supply_models(s):
    """Resolve generic supply-load facts by exact instance identity, never symbol."""
    current={p['candidate_id']:p for p in catalog()};result={}
    for c in s['components'].values():
        p=s.get('part_bindings',{}).get(c['id'])
        if not p or current.get(p['candidate_id'])!=p or p['evidence_status']!='available' or not binding_valid(s,c,p):continue
        f=p['facts']
        if f.get('power_budget_model')!='supply_pin_load':continue
        names={pin['name'] for pin in c['pins']};pins=f.get('supply_pins',[])
        if not isinstance(pins,list) or not pins or any(not isinstance(pin,str) or pin not in names for pin in pins):continue
        if any(type(f.get(key)) not in (float,int) or not math.isfinite(f[key]) for key in ('supply_min_v','supply_max_v')):continue
        if not 0<=f['supply_min_v']<=f['supply_max_v']:continue
        result[c['id']]=p
    return result


def reference_binding_findings(s):
    # A generic LED symbol carries no fixed electrical rule pack. Its DC
    # stress check consumes the selected part's own facts/rating model, unlike
    # the fixed IC packs below. Do not inherit WP7113ID data by symbol name.
    active={p['symbol_id'] for p in reference_catalog()
            if p['symbol_id'] not in ('Device:R','Device:C','Device:C_Polarized','Device:LED')
            and not p['symbol_id'].startswith('Connector_Generic:')}
    return [dict(code='REFERENCE_MODEL_IDENTITY_MISMATCH',severity='error',
                 message='Imported identity cannot inherit a different fixed-reference part model; bind a matching generic model or a distinct source-correct symbol.',entity_ids=[c['id']])
            for c in s['components'].values() if c['library_id'] in active
            and 'import_record' in s.get('part_bindings',{}).get(c['id'],{}).get('facts',{})]


def binding_valid(s,c,p):
    """Same identity/value/footprint guard for editing and DC diagnostics."""
    from .electrical import number
    fp=s['footprint_bindings'].get(c['id'],{})
    key={'resistor':'value_ohm','capacitor':'capacitance_f'}.get(p['kind']);actual=number(c['value'])
    ok=(actual is not None and math.isclose(actual,p['facts'][key],rel_tol=1e-12,abs_tol=0)) if key else c['value']==c['symbol']
    if p['kind']=='led':
        ok=c['value']==c['symbol'] or c['value'].casefold()==p['facts'].get('color','').casefold()
    return c['library_id']==p['symbol_id'] and ok and fp.get('footprint_id')==p['footprint_id'] and fp.get('definition_hash')==p['facts']['footprint_definition_hash']


def native_cad_fields(state):
    """Persist only current, consistent source-linked selections in native CAD."""
    current = {p['candidate_id']: p for p in catalog()}
    result = {}
    for component in state['components'].values():
        part = state.get('part_bindings', {}).get(component['id'])
        if part is None:
            continue
        if (current.get(part['candidate_id']) != part or part['evidence_status'] != 'available'
                or not binding_valid(state, component, part)):
            raise ValueError('NATIVE_CAD_PART_IDENTITY_INVALID:' + component['ref'])
        fields = dict(Manufacturer=part['manufacturer'], MPN=part['mpn'])
        urls = [s['url'] for s in part['sources'] if s.get('url')]
        if urls:
            fields['SourceURL'] = urls[0]
        result[component['ref']] = fields
    return result
