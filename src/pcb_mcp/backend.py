import hashlib
import json
import os
import subprocess
import sys
import uuid
import math
import functools
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from .sexpr import parse, children, one
from .config import asset_root
from . import registry

CLI = Path(os.environ.get('PCB_MCP_KICAD_CLI', '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'))
LIB = Path(os.environ.get('PCB_MCP_DEVICE_LIBRARY', '/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/Device.kicad_sym'))
SYMBOLS = ('Battery_Cell', 'R', 'LED')
LIBRARIES = ('Device','Sensor','Sensor_Humidity','RF_Module','Connector','Connector_Generic','Regulator_Linear','power','PCB_MCP','Logic_LevelTranslator','74xGxx','Timer','Amplifier_Operational')


def inherited_definitions(text):
    """Resolve local inheritance without inventing geometry or pin mappings.

    Child fields override parent fields. Nested graphical unit names are renamed
    to the child, matching KiCad's flattened schematic cache representation.
    Cycles, missing parents and graphical overrides fail closed.
    """
    tree=parse(text); symbols={s['v'][1]:s for s in children(tree,'symbol')}; resolved={}
    def resolve(name,stack=()):
        if name in resolved:return resolved[name]
        if name in stack:raise ValueError('Inheritance cycle')
        node=symbols[name]; raw=text[node['start']:node['end']]
        parents=children(node,'extends')
        if not parents:resolved[name]=raw;return raw
        parent=parents[0]['v'][1]
        if parent not in symbols:raise ValueError('Missing parent '+parent)
        base=resolve(parent,stack+(name,)); parsed=parse(base)
        child_parts=[p for p in node['v'] if isinstance(p,dict) and p['v'][0]!='extends']
        if any(p['v'][0]=='symbol' for p in child_parts):raise ValueError('Inherited graphical overrides unsupported')
        def key(p):return (p['v'][0],p['v'][1] if p['v'][0]=='property' else '')
        overrides={key(p):text[p['start']:p['end']] for p in child_parts}
        fragments=[]
        for p in parsed['v']:
            if not isinstance(p,dict):continue
            k=key(p)
            fragment=overrides.pop(k,None)
            if fragment is None:
                fragment=base[p['start']:p['end']]
                if p['v'][0]=='symbol':
                    unit=p['v'][1]
                    if not unit.startswith(parent+'_'):raise ValueError('Unexpected inherited unit')
                    fragment=fragment.replace(json.dumps(unit),json.dumps(name+unit[len(parent):]),1)
            fragments.append(fragment)
        fragments.extend(overrides.values())
        result='(symbol '+json.dumps(name)+' '+' '.join(fragments)+')'
        resolved[name]=result;return result
    results={}
    for name,node in symbols.items():
        try:results[name]=(resolve(name),None)
        except (ValueError,KeyError,RecursionError) as e:results[name]=(text[node['start']:node['end']],str(e))
    return results


def digest(data):
    return hashlib.sha256(data).hexdigest()


@functools.lru_cache(maxsize=128)
def _catalog_library(path, mtime_ns, size):
    path=Path(path)
    text = path.read_text()
    root = parse(text)
    resolved=inherited_definitions(text)
    result = []
    for symbol in children(root, 'symbol'):
        name = symbol['v'][1]
        original=text[symbol['start']:symbol['end']]
        definition,inheritance_error=resolved[name]
        symbol=parse(definition)
        # Preserve existing candidate IDs for symbols without inherited dependencies.
        h = digest(definition.encode())
        cid = digest((path.stem+':'+name+'\0'+h).encode())[:32] if path.stem.startswith('EXT_') else h[:32]
        pins = []; reasons=[]
        if inheritance_error:reasons.append(inheritance_error)
        for unit in children(symbol, 'symbol'):
            unit_match=re.search(r'_(\d+)_(\d+)$',unit['v'][1])
            if not unit_match:
                reasons.append('Unrecognized symbol unit identifier.'); continue
            unit_no,conversion=map(int,unit_match.groups())
            if unit_no>1:
                reasons.append('Multi-unit symbols are not supported yet.')
            if conversion>1:
                continue
            for pin in children(unit, 'pin'):
                number = one(pin, 'number')[0]
                physical=re.findall(r'[A-Za-z0-9]+',number) if re.fullmatch(r'\[[A-Za-z0-9]+(?:,[A-Za-z0-9]+)+\]',number) else [number]
                pins.append(dict(pin_id=digest(f'{cid}:{number}'.encode())[:32], number=number,
                                 name=one(pin, 'name')[0], electrical_type=pin['v'][1],
                                 at=one(pin, 'at'), length=one(pin, 'length')[0],physical_numbers=physical))
        if not pins: reasons.append('No directly defined pins.')
        if len(pins)>100: reasons.append('More than 100 pins exceeds this prototype limit.')
        if any('[' in p['number'] and len(p['physical_numbers'])==1 for p in pins):reasons.append('Unrecognized grouped pin number syntax.')
        if len(set(p['number'] for p in pins))!=len(pins): reasons.append('Duplicate pin numbers require an adapter.')
        physical=[n for p in pins for n in p['physical_numbers']]
        if len(physical)!=len(set(physical)):reasons.append('Overlapping physical pin numbers require an adapter.')
        if any(float(p['at'][2])%90 for p in pins): reasons.append('Non-orthogonal pins are not supported.')
        ds=next((p['v'][2] for p in children(symbol,'property') if p['v'][1]=='Datasheet'),None)
        description=next((p['v'][2] for p in children(symbol,'property') if p['v'][1]=='Description'),'')
        result.append(dict(candidate_id=cid, library_id=f'{path.stem}:{name}', symbol=name,
                           definition_hash=h, pins=pins, definition=definition,
                           completeness='symbol_only', manufacturer=None, mpn=None,
                           offers=[], footprint_bindings=[], source=str(path),
                           supported=not reasons,unsupported_reasons=sorted(set(reasons)),datasheet=ds,
                           description=description,source_definition_hash=digest(original.encode()),
                           inherited=bool(children(parse(original),'extends'))))
    return result


def library_paths():
    paths={p.stem:p for p in LIB.parent.glob('*.kicad_sym')}
    paths['Device']=LIB
    paths['PCB_MCP']=asset_root()/'catalog/symbols/PCB_MCP.kicad_sym'
    extra=registry.library_paths('symbol')
    if set(extra)&set(paths):raise ValueError('CATALOG_LIBRARY_CONFLICT')
    return dict(paths,**extra)


def catalog(libraries=None):
    result=[]
    paths=library_paths()
    selected=libraries if libraries is not None else sorted(set(LIBRARIES)|set(registry.selected_libraries())|set(registry.referenced_libraries()))
    for library in selected:
        library=registry.name(library)
        if library not in paths:raise ValueError('SYMBOL_LIBRARY_NOT_AVAILABLE: '+library)
        path=paths[library]
        stat=path.stat()
        result.extend(_catalog_library(str(path),stat.st_mtime_ns,stat.st_size))
    return result


def command(args, cwd):
    """No shell. Failures and timeouts are not validation passes."""
    try:
        run = subprocess.run([str(CLI), *args], cwd=cwd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('BACKEND_TIMEOUT') from exc
    return dict(exit_code=run.returncode, stdout=run.stdout, stderr=run.stderr)


def expanded_number(number):
    return re.findall(r'[A-Za-z0-9]+',number) if number.startswith('[') else [number]


def xml_nets(path):
    tree = ET.parse(path)
    return {net.attrib['name'].lstrip('/'): sorted((n.attrib['ref'], pin) for n in net.findall('node') for pin in expanded_number(n.attrib['pin']) if not n.attrib['ref'].startswith('#'))
            for net in tree.findall('./nets/net') if not net.attrib['name'].startswith('unconnected-')}


def sexpr_nets(path):
    root = parse(Path(path).read_text())
    return {one(n, 'name')[0]: sorted((one(p, 'ref')[0],pin) for p in children(n, 'node') for pin in expanded_number(one(p,'pin')[0]) if not one(p,'ref')[0].startswith('#'))
            for n in children(children(root, 'nets')[0], 'net') if one(n,'name')[0] not in ['__NOCONNECT'] and not one(n,'name')[0].startswith('unconnected-')}


def native_source_field_report(state, netlist):
    from .parts import native_cad_fields
    expected = native_cad_fields(state)
    actual = {c.attrib['ref']: {f.attrib['name']: f.text or '' for f in c.findall('./fields/field')}
              for c in ET.parse(netlist).findall('./components/comp')}
    mismatches = [ref for ref, fields in expected.items()
                  if any(actual.get(ref, {}).get(name) != value for name, value in fields.items())]
    return dict(matched=not mismatches, bound_count=len(expected),
                unbound_count=len(state['components']) - len(expected), mismatch_references=mismatches,
                scope='Persisted current source-linked selection strings, not physical qualification')


def render_grid(state,directory):
    """Conservative single-unit labeled blocks; no model geometry or PCB routing.

    All net relationships originate in CircuitSpec. Every connected pin receives a
    real wire stub and a named label, so KiCad independently resolves connectivity.
    """
    from .parts import native_cad_fields
    source_fields=native_cad_fields(state)
    sheet_uuid=str(uuid.uuid4())
    uid=lambda:str(uuid.uuid4())
    quote=lambda s:json.dumps(s,ensure_ascii=False)
    fmt=lambda n:f'{n:.6f}'.rstrip('0').rstrip('.') if '.' in f'{n:.6f}' else str(n)
    objects=[]; definitions={}; layouts=[]
    pin_nets={(p['component_id'],p['pin_id']):n['name'] for n in state['nets'].values() for p in n['pins']}
    intentions=state.get('pin_intents',{})
    for c in sorted(state['components'].values(),key=lambda c:c['ref']):
        pins=c['pins']; xs=[float(p['at'][0]) for p in pins]; ys=[float(p['at'][1]) for p in pins]
        # Keep labels and fields clear of adjacent blocks even for larger connectors.
        extent_x=max([abs(x) for x in xs]+[5]); extent_y=max([abs(y) for y in ys]+[5])
        layouts.append((c,extent_x,extent_y))
    cell_w=max(90,max(2*x+90 for _,x,y in layouts)); cell_h=max(70,max(2*y+60 for _,x,y in layouts))
    columns=min(6,max(1,math.ceil(math.sqrt(len(layouts)*cell_h/cell_w*1.4))))
    rows=math.ceil(len(layouts)/columns)
    width=columns*cell_w+30; height=rows*cell_h+30
    positions=None
    if width>1189 or height>841:
        # One large MCU must not inflate every passive cell to MCU height.
        # Preserve the old layout when it fits; otherwise pack complete labeled
        # symbol envelopes on bounded A0 geometry without shrinking the fonts.
        from .generic_placement import bounded_positions
        width,height=1189.,841.
        try:positions=bounded_positions({c['ref']:(2*ex+86,2*ey+56) for c,ex,ey in layouts},width,height,15.)
        except ValueError as exc:raise RuntimeError('LAYOUT_CAPACITY_EXCEEDED') from exc
    for index,(c,ex,ey) in enumerate(layouts):
        x=15+(index%columns+.5)*cell_w; y=15+(index//columns+.5)*cell_h
        if positions:x,y=positions[c['ref']]
        x=round(x/1.27)*1.27; y=round(y/1.27)*1.27
        libid=c['library_id']; name=c['symbol']
        definitions[libid]=c['definition'].replace(f'(symbol "{name}"',f'(symbol {quote(libid)}',1)
        pin_records=' '.join(f'(pin {quote(p["number"])} (uuid {uid()}))' for p in c['pins'])
        fields=[]
        for name,value,dy in [('Reference',c['ref'],-ey-8),('Value',c['value'],-ey-5)]:
            fields.append(f'(property {quote(name)} {quote(value)} (at {fmt(x+ex+8)} {fmt(y+dy)} 0) (effects (font (size 1.27 1.27)) (justify left)))')
        fp=state.get('footprint_bindings',{}).get(c['id'],{}).get('footprint_id','')
        fields.append(f'(property "Footprint" {quote(fp)} (at {fmt(x)} {fmt(y)} 0) (effects (font (size 1.27 1.27)) (hide yes)))')
        fields.extend(f'(property {quote(key)} {quote(value)} (at {fmt(x)} {fmt(y)} 0) (effects (font (size 1.27 1.27)) (hide yes)))' for key,value in source_fields.get(c['ref'],{}).items())
        objects.append(f'''(symbol (lib_id {quote(libid)}) (at {fmt(x)} {fmt(y)} 0) (unit 1)
          (in_bom yes) (on_board yes) (dnp no) (uuid {uid()})
          {' '.join(fields)} {pin_records}
          (instances (project "led" (path "/{sheet_uuid}" (reference {quote(c['ref'])}) (unit 1)))))''')
        rendered=set()
        for p in c['pins']:
            px=x+float(p['at'][0]); py=y-float(p['at'][1]); angle=int(float(p['at'][2]))%360
            key=(c['id'],p['pin_id']); net=pin_nets.get(key)
            group=(px,py)
            if group in rendered: continue
            rendered.add(group)
            if net:
                dx,dy={0:(-5.08,0),90:(0,5.08),180:(5.08,0),270:(0,-5.08)}[angle]
                tx,ty=px+dx,py+dy
                objects.append(f'(wire (pts (xy {fmt(px)} {fmt(py)}) (xy {fmt(tx)} {fmt(ty)})) (stroke (width 0) (type default)) (uuid {uid()}))')
                # Vertical labels stay in narrow pin columns, horizontal labels extend outwards.
                rotation=90 if dy else 0
                justify='right bottom' if dx<0 or dy>0 else 'left bottom'
                objects.append(f'(global_label {quote(net)} (shape input) (at {fmt(tx)} {fmt(ty)} {rotation}) (effects (font (size 0.8 0.8)) (justify {justify})) (uuid {uid()}))')
            elif intentions.get(c['id']+':'+p['pin_id'],{}).get('intent')=='no_connect':
                objects.append(f'(no_connect (at {fmt(px)} {fmt(py)}) (uuid {uid()}))')
    if state.get('power_sources'):
        flag=next(c for c in catalog(['power']) if c['library_id']=='power:PWR_FLAG')
        from .power_drive import resistive_drive
        derived,paths=resistive_drive(state)
        snapshot=dict(flag,passive_drive_annotation=dict(net_ids=sorted(derived),paths=paths,scope='ERC connectivity annotation only; no voltage, current or functional proof'))
        (directory/'power_flag_snapshot.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2))
        definitions['power:PWR_FLAG']=flag['definition'].replace('(symbol "PWR_FLAG"','(symbol "power:PWR_FLAG"',1)
        marked=set()
        for source in state['power_sources'].values(): marked.update([source['net_id'],source['ground_net_id']])
        marked.update(derived)
        # External battery terminals can also be charger outputs. A PWR_FLAG is
        # only ERC annotation: adding a second synthetic power_out beside the
        # actual charger pin would manufacture a driver-conflict error. Actual
        # component-to-component power_out conflicts remain visible to KiCad.
        real_driven={nid for nid in marked if any(
            next(pin for pin in state['components'][member['component_id']]['pins'] if pin['pin_id']==member['pin_id'])['electrical_type']=='power_out'
            for member in state['nets'][nid]['pins'])}
        marked-=real_driven
        for i,nid in enumerate(sorted(marked)):
            x=round((25+i*35)/1.27)*1.27; y=round((height-8)/1.27)*1.27
            name=state['nets'][nid]['name']; reference=f'#FLG{i+1:02d}'
            if x+30>width:raise RuntimeError('POWER_FLAG_LAYOUT_CAPACITY')
            objects.append(f'''(symbol (lib_id "power:PWR_FLAG") (at {fmt(x)} {fmt(y)} 0) (unit 1)
              (in_bom no) (on_board yes) (dnp no) (uuid {uid()})
              (property "Reference" {quote(reference)} (at {fmt(x)} {fmt(y)} 0) (effects (font (size 0.8 0.8)) (hide yes)))
              (property "Value" "PWR_FLAG" (at {fmt(x)} {fmt(y-5.08)} 0) (effects (font (size 0.8 0.8))))
              (pin "1" (uuid {uid()}))
              (instances (project "led" (path "/{sheet_uuid}" (reference {quote(reference)}) (unit 1)))))''')
            objects.append(f'(wire (pts (xy {fmt(x)} {fmt(y)}) (xy {fmt(x+5.08)} {fmt(y)})) (stroke (width 0) (type default)) (uuid {uid()}))')
            objects.append(f'(global_label {quote(name)} (shape input) (at {fmt(x+5.08)} {fmt(y)} 0) (effects (font (size 0.8 0.8)) (justify left bottom)) (uuid {uid()}))')
    result=f'(kicad_sch (version 20250114) (generator "pcb_mcp_lab") (uuid {sheet_uuid}) (paper "User" {fmt(width)} {fmt(height)}) (lib_symbols {" ".join(definitions.values())}) {" ".join(objects)})'
    (directory/'led.kicad_sch').write_text(result)


def expected_nets(state):
    result = {}
    for net in state['nets'].values():
        members = []
        for p in net['pins']:
            c = state['components'][p['component_id']]
            pin = next(x for x in c['pins'] if x['pin_id'] == p['pin_id'])
            members.extend((c['ref'],number) for number in pin.get('physical_numbers',expanded_number(pin['number'])))
        result[net['name']] = sorted(members)
    return result


def no_connect_matches(state,path):
    root=parse(path.read_text())
    actual={tuple(round(float(v),6) for v in one(n,'at')[:2]) for n in children(root,'no_connect')}
    instances={next(p['v'][2] for p in children(s,'property') if p['v'][1]=='Reference'):s for s in children(root,'symbol')}
    expected=set()
    for intent in state.get('pin_intents',{}).values():
        if intent['intent']!='no_connect': continue
        ref=intent['pin']; c=state['components'][ref['component_id']]
        pin=next(p for p in c['pins'] if p['pin_id']==ref['pin_id'])
        x,y,angle=map(float,one(instances[c['ref']],'at'))
        if angle!=0 or children(instances[c['ref']],'mirror'): return False
        expected.add((round(x+float(pin['at'][0]),6),round(y-float(pin['at'][1]),6)))
    return expected==actual


def render_led(state, directory):
    """Bounded, deterministic layout adapter for the explicitly supported topology.

    Copies exact real library definitions; generates fresh server-owned instance IDs.
    Not a general-purpose layout engine and never accepts coordinates from tools.
    """
    from .parts import native_cad_fields
    source_fields=native_cad_fields(state)
    sheet_uuid = str(uuid.uuid4())
    uid = lambda: str(uuid.uuid4())
    quote = lambda x: json.dumps(x, ensure_ascii=False)
    defs, objects = [], []
    config = {'Battery_Cell': (101.6,101.6,0,(106.68,99.06),(106.68,104.14)),
              'R': (142.24,88.9,0,(147.32,87.63),(147.32,91.44)),
              'LED': (142.24,111.76,90,(149.86,110.49),(149.86,114.3))}
    for c in state['components'].values():
        name = c['symbol']
        defs.append(c['definition'].replace(f'(symbol "{name}"', f'(symbol "Device:{name}"', 1))
        x,y,angle,refpos,valpos = config[name]
        fields = []
        for key,val,pos in [('Reference',c['ref'],refpos),('Value',c['value'],valpos)]:
            fields.append(f'(property {quote(key)} {quote(val)} (at {pos[0]} {pos[1]} {angle}) (effects (font (size 1.27 1.27)) (justify left)))')
        fp=state.get('footprint_bindings',{}).get(c['id'],{}).get('footprint_id','')
        fields.append(f'(property "Footprint" {quote(fp)} (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))')
        fields.extend(f'(property {quote(key)} {quote(value)} (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))' for key,value in source_fields.get(c['ref'],{}).items())
        pin_records = ' '.join(f'(pin {quote(p["number"])} (uuid {uid()}))' for p in c['pins'])
        objects.append(f'''(symbol (lib_id "Device:{name}") (at {x} {y} {angle}) (unit 1)
          (in_bom yes) (on_board yes) (dnp no) (uuid {uid()})
          {' '.join(fields)} {pin_records}
          (instances (project "led" (path "/{sheet_uuid}" (reference {quote(c['ref'])}) (unit 1)))))''')
    paths = [[(101.6,96.52),(101.6,76.2),(142.24,76.2),(142.24,85.09)],
             [(142.24,92.71),(142.24,107.95)],
             [(142.24,115.57),(142.24,127),(101.6,127),(101.6,104.14)]]
    for path in paths:
        for a,b in zip(path,path[1:]):
            objects.append(f'(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) (stroke (width 0) (type default)) (uuid {uid()}))')
    by_symbol = {c['symbol']: c for c in state['components'].values()}
    # Names come from the actual spec, not a hard-coded target netlist.
    for symbol,number,x,y in [('Battery_Cell','1',116.84,76.2),('LED','2',142.24,101.6),('Battery_Cell','2',116.84,127)]:
        c = by_symbol[symbol]
        pinid = next(p['pin_id'] for p in c['pins'] if p['number']==number)
        net = next(n for n in state['nets'].values() if {'component_id':c['id'],'pin_id':pinid} in n['pins'])
        objects.append(f'(global_label {quote(net["name"])} (shape input) (at {x} {y} 0) (effects (font (size 1 1)) (justify left bottom)) (uuid {uid()}))')
    result = f'(kicad_sch (version 20250114) (generator "pcb_mcp_lab") (uuid {sheet_uuid}) (paper "A4") (lib_symbols {" ".join(defs)}) {" ".join(objects)})'
    (directory/'led.kicad_sch').write_text(result)


def build(state, directory, layout_profile='auto'):
    directory.mkdir()
    from . import footprints
    footprints.write_table(state.get('footprint_bindings',{}),directory)
    libraries={c['library_id'].split(':',1)[0]:c['source'] for c in state['components'].values()}
    if libraries:
        rows = []
        for name, path in sorted(libraries.items()):
            uri = json.dumps(path, ensure_ascii=False)
            rows.append(f'(lib (name {json.dumps(name)})(type "KiCad")(uri {uri})(options "")'
                        '(descr "Source-bound symbols"))')
        (directory/'sym-lib-table').write_text('(sym_lib_table (version 7) '+''.join(rows)+')\n', encoding='utf-8')
        (directory/'led.kicad_pro').write_text(json.dumps(dict(meta=dict(version=1))))
    if state.get('footprint_bindings'):
        (directory/'led.kicad_pro').write_text(json.dumps(dict(meta=dict(version=1))))
    (directory/'spec.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
    env = dict(os.environ)
    env['KICAD10_SYMBOL_DIR'] = str(LIB.parent)
    env['MPLCONFIGDIR'] = str(directory/'mplconfig')
    env['XDG_CACHE_HOME'] = str(directory/'cache')
    env['XDG_DATA_HOME'] = str(directory/'userdata')
    # GUI/library lookup settings are backend configuration, not tool arguments.
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    try:
        run = subprocess.run([sys.executable,'-m','pcb_mcp.worker',str(directory/'spec.json')],
                             cwd=directory,env=env,capture_output=True,text=True,timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('BACKEND_TIMEOUT') from exc
    (directory/'skidl.log').write_text(run.stdout+'\n'+run.stderr)
    if run.returncode != 0:
        lastline=next((line for line in reversed(run.stderr.splitlines()) if line.strip()),'No backend diagnostic')
        raise RuntimeError('SKIDL_FAILED: '+lastline[:1000])
    if sexpr_nets(directory/'skidl.net') != expected_nets(state):
        raise RuntimeError('SKIDL_NETLIST_MISMATCH')
    if state['profile']=='led_series_experiment' and layout_profile in ['auto','led_loop']:
        render_led(state,directory)
    else:
        render_grid(state,directory)
    return digest((directory/'led.kicad_sch').read_bytes())
