"""Portable native CAD copies, with local used libraries and independent checks.

Original managed source artifacts are never rewritten. Exported files remain
review material and do not confer manufacturing or physical qualification.
"""
import json
import re
from pathlib import Path
from . import backend
from .common import require,write_json
from .sexpr import parse,children


def prepare(state,board_source,schematic_source,destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    source_index=[]
    def write(name,raw,source=None):
        path=destination/name
        require(path.resolve().is_relative_to(destination.resolve()),'PORTABLE_PATH_INVALID','Portable export path escaped its directory.')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        source_index.append(dict(export=name,sha256=backend.digest(raw),source=str(source) if source else 'derived_from_embedded_schematic'))
    for extension in ('kicad_pcb','kicad_pro','kicad_dru'):
        source=board_source/('routed.'+extension)
        if source.is_file():write('design.'+extension,source.read_bytes(),source)
    source=schematic_source/'led.kicad_sch';text=source.read_text();tree=parse(text)
    replacements=[]
    for symbol in children(tree,'symbol'):
        for instance in children(symbol,'instances'):
            for project in children(instance,'project'):
                fragment=text[project['start']:project['end']]
                match=re.match(r'\(project\s+("(?:\\.|[^"\\])*"|[^\s()]+)',fragment)
                require(match is not None,'PORTABLE_INSTANCE_INVALID','Unrecognized native project instance.')
                replacements.append((project['start']+match.start(1),project['start']+match.end(1),'"design"'))
    renamed=text
    for start,end,value in sorted(replacements,reverse=True):renamed=renamed[:start]+value+renamed[end:]
    write('design.kicad_sch',renamed.encode(),source)
    symbols={}
    for definition in children(children(tree,'lib_symbols')[0],'symbol'):
        qualified=definition['v'][1]
        require(':' in qualified,'PORTABLE_LIBRARY_ID_INVALID','Embedded symbol has no library identity.')
        library,name=qualified.split(':',1)
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*',library) is not None,'PORTABLE_LIBRARY_ID_INVALID','Invalid symbol library name.')
        fragment=text[definition['start']:definition['end']]
        match=re.match(r'\(symbol\s+("(?:\\.|[^"\\])*"|[^\s()]+)',fragment)
        require(match is not None,'PORTABLE_SYMBOL_INVALID','Invalid embedded symbol header.')
        fragment=fragment[:match.start(1)]+json.dumps(name)+fragment[match.end(1):]
        symbols.setdefault(library,[]).append(fragment)
    symbol_entries=[]
    for library,definitions in sorted(symbols.items()):
        name='libraries/'+library+'.kicad_sym'
        write(name,('(kicad_symbol_lib (version 20241209) (generator pcb_mcp)\n'+'\n'.join(definitions)+'\n)\n').encode())
        symbol_entries.append('(lib (name %s) (type "KiCad") (uri %s) (options "") (descr "Used embedded symbols"))' %
                              (json.dumps(library),json.dumps('${KIPRJMOD}/'+name)))
    write('sym-lib-table',('(sym_lib_table\n'+'\n'.join(symbol_entries)+'\n)\n').encode())
    footprint_entries={};seen={}
    for component in state['components'].values():
        binding=state['footprint_bindings'][component['id']]
        library,name=binding['footprint_id'].split(':',1)
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*',library) is not None and '/' not in name and '\\' not in name and name not in ('.','..'),
                'PORTABLE_LIBRARY_ID_INVALID','Invalid footprint library identity.')
        source=Path(binding['source']);raw=source.read_bytes()
        require(backend.digest(source.read_text().encode())==binding['definition_hash'],'PORTABLE_FOOTPRINT_CHANGED','Footprint source changed during export.')
        relative='libraries/'+library+'.pretty/'+name+'.kicad_mod'
        require(relative not in seen or seen[relative]==backend.digest(raw),'PORTABLE_FOOTPRINT_CONFLICT','One library name has conflicting footprint definitions.')
        if relative not in seen:write(relative,raw,source);seen[relative]=backend.digest(raw)
        footprint_entries[library]=('(lib (name %s) (type "KiCad") (uri %s) (options "") (descr "Source-bound used footprints"))' %
                                    (json.dumps(library),json.dumps('${KIPRJMOD}/libraries/'+library+'.pretty')))
    write('fp-lib-table',('(fp_lib_table\n'+'\n'.join(footprint_entries[k] for k in sorted(footprint_entries))+'\n)\n').encode())
    write_json(destination/'source-index.json',dict(files=source_index,
               note='Used embedded symbol definitions and exact footprint sources retain their original licensing; source copies do not grant new redistribution rights. Manufacturer model assets are not bundled here.'))
    run=backend.command(['sch','export','netlist','--format','kicadxml','-o','netlist.xml','design.kicad_sch'],destination)
    write_json(destination/'netlist-command.json',run)
    require(run['exit_code']==0,'PORTABLE_NETLIST_EXPORT_FAILED','Portable schematic cannot be independently read by KiCad.')
    require(backend.xml_nets(destination/'netlist.xml')==backend.expected_nets(state),
            'PORTABLE_NETLIST_MISMATCH','Portable project changed the actual electrical graph.')
    drc=backend.command(['pcb','drc','--format','json','--all-track-errors','--schematic-parity','--severity-all','--exit-code-violations','-o','drc.json','design.kicad_pcb'],destination)
    write_json(destination/'drc-command.json',drc)
    raw=json.loads((destination/'drc.json').read_text()) if (destination/'drc.json').is_file() else {}
    require(drc['exit_code']==0 and all(isinstance(raw.get(key),list) and not raw[key] for key in ('violations','unconnected_items','schematic_parity')),
            'PORTABLE_DRC_FAILED','Portable board did not preserve a complete passing native DRC result.')
    report=dict(passed=True,project_file='design.kicad_pro',netlist_matches=True,drc_errors=0,native_schematic_parity_checked=True,
                used_symbol_libraries=sorted(symbols),used_footprint_libraries=sorted(footprint_entries),
                external_library_paths_required=False,physical_verified=False,
                scope='editable native project with local used libraries; schematic-to-board resynchronization and manufacturing qualification not established')
    write_json(destination/'verification.json',report)
    return report
