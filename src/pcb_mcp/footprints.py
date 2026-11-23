"""Local footprint geometry, never a procurement or package-fit certification."""
import fnmatch,json,os
from pathlib import Path
from . import backend
from .sexpr import parse,children,one
from .config import asset_root
from . import registry

LIB=Path(os.environ.get('PCB_MCP_FOOTPRINT_ROOT','/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints'))
LIBRARIES=('Resistor_THT','Resistor_SMD','Capacitor_THT','Capacitor_SMD','LED_THT','LED_SMD','Connector_PinHeader_2.54mm','Connector_PinSocket_2.54mm','TerminalBlock_Phoenix','RF_Module','Package_TO_SOT_SMD','Connector_USB','Sensor','Connector_PCB_MCP','Converter_PCB_MCP','Package_DFN_QFN','Package_SO')


def library_paths():
    paths={p.name.removesuffix('.pretty'):p for p in LIB.glob('*.pretty') if p.is_dir()}
    paths.update({p.name.removesuffix('.pretty'):p for p in (asset_root()/'catalog/footprints').glob('*.pretty') if p.is_dir()})
    extra=registry.library_paths('footprint')
    if set(extra)&set(paths):raise ValueError('CATALOG_LIBRARY_CONFLICT')
    return dict(paths,**extra)


def entries(query,library=None):
    q=query.casefold().split();rows=[]
    paths=library_paths()
    for lib in ([registry.name(library)] if library else sorted(paths)):
        if lib not in paths:raise ValueError('FOOTPRINT_LIBRARY_NOT_AVAILABLE: '+lib)
        folder=paths[lib]
        for path in folder.glob('*.kicad_mod'):
            ident=lib+':'+path.stem
            if all(w in ident.casefold() for w in q):rows.append((ident,path))
    exact=[r for r in rows if query.casefold() in [r[0].casefold(),r[1].stem.casefold()]]
    return sorted(exact or rows)


def load(ident,path):
    raw=path.read_text();tree=parse(raw);pads=[];reasons=[]
    for p in children(tree,'pad'):
        if not p['v'][1]:continue
        pads.append(dict(number=p['v'][1],kind=p['v'][2],shape=p['v'][3],at=one(p,'at'),size=one(p,'size'),layers=one(p,'layers')))
        if p['v'][2] not in ['smd','thru_hole']:reasons.append('Numbered non-electrical pads unsupported')
    if not pads:reasons.append('No numbered electrical pads')
    if len(pads)>100:reasons.append('More than 100 pads')
    sha=backend.digest(raw.encode())
    return dict(candidate_id=backend.digest((ident+'\0'+sha).encode())[:32],footprint_id=ident,definition_hash=sha,
                pads=pads,supported=not reasons,unsupported_reasons=reasons,completeness='geometry_only',
                description=one(tree,'descr')[0] if children(tree,'descr') else '',source=str(path))


def binding_matches(component,fp):
    # Match the real library's own footprint-name filters as a role guard. This
    # and number identity still do not establish a manufacturer's package fit.
    symbol=parse(component['definition'])
    filters=next((p['v'][2].split() for p in children(symbol,'property') if p['v'][1]=='ki_fp_filters'),[])
    name=fp['footprint_id'].split(':',1)[1]
    return not filters or any(fnmatch.fnmatchcase(name,f) or fnmatch.fnmatchcase(fp['footprint_id'],f) for f in filters)


def pins(component):
    return {n for p in component['pins'] for n in p.get('physical_numbers',backend.expanded_number(p['number']))}


def write_table(bindings,folder):
    """Project-local library links, without editing global KiCad settings."""
    if not bindings:return
    libraries={p['footprint_id'].split(':',1)[0]:str(Path(p['source']).parent) for p in bindings.values()}
    rows=['(fp_lib_table (version 7)']
    for name, path in sorted(libraries.items()):
        uri = json.dumps(path, ensure_ascii=False)
        rows.append(f'(lib (name {json.dumps(name)})(type "KiCad")(uri {uri})(options "")'
                    '(descr "Managed source-linked footprint library"))')
    (folder/'fp-lib-table').write_text('\n'.join(rows)+')\n', encoding='utf-8')
