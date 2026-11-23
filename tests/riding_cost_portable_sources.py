"""Package exact CAD bytes with portable native symbol/footprint libraries."""
import json,shutil,hashlib,os
from pathlib import Path
root=Path('work/riding-intercom').resolve();s=json.loads((root/'data/89184e4c1e4740f88be798dfed22a98b/state.json').read_text());record=json.loads((root/'cost-down/pcb-01/pcb_build.json').read_text());bid=record['pcb_build_id'];board=root/'data'/s['id']/'pcb_builds'/bid;build=s['pcb_builds'][bid];sch=root/'data'/s['id']/'builds'/build['schematic_build_id'];out=root/'cost-down/vendor-package/CAD';out.mkdir(exist_ok=True)
# Discover actual managed schematic location from recorded build data.
if not sch.exists():
 candidates=list((root/'data'/s['id']).glob('*/'+build['schematic_build_id']));assert len(candidates)==1,candidates;sch=candidates[0]
for dirname in ['PCB','SCH','libraries']:(out/dirname).mkdir(exist_ok=True)
for ext in ['kicad_pcb','kicad_pro','kicad_dru']:shutil.copy2(board/('routed.'+ext),out/'PCB'/('routed.'+ext))
for p in sch.glob('led.*'):
 if p.suffix in ['.kicad_sch','.kicad_pro','.pdf']:shutil.copy2(p,out/'SCH'/p.name)
fpnames=set();symbols={}
for c in s['components'].values():
 f=s['footprint_bindings'][c['id']];lib,name=f['footprint_id'].split(':',1);dest=out/'libraries'/(lib+'.pretty');dest.mkdir(exist_ok=True);shutil.copy2(f['source'],dest/(name+'.kicad_mod'));fpnames.add(lib)
 lib,name=c['library_id'].split(':',1);symbols.setdefault(lib,{})[name]=c['definition']
for lib,defs in symbols.items():(out/'libraries'/(lib+'.kicad_sym')).write_text('(kicad_symbol_lib (version 20250114) (generator "source_archive")\n'+'\n'.join(defs.values())+'\n)')
quote=lambda x:json.dumps(x)
for folder in ['PCB','SCH']:
 (out/folder/'fp-lib-table').write_text('(fp_lib_table\n'+'\n'.join('(lib (name '+quote(n)+')(type "KiCad")(uri "${KIPRJMOD}/../libraries/'+n+'.pretty")(options "")(descr "Exact source-bound footprint"))' for n in sorted(fpnames))+'\n)')
 (out/folder/'sym-lib-table').write_text('(sym_lib_table\n'+'\n'.join('(lib (name '+quote(n)+')(type "KiCad")(uri "${KIPRJMOD}/../libraries/'+n+'.kicad_sym")(options "")(descr "Exact bound symbol definition"))' for n in sorted(symbols))+'\n)')
assert (out/'PCB/routed.kicad_pcb').read_bytes()==(board/'routed.kicad_pcb').read_bytes()
assert (out/'SCH/led.kicad_sch').read_bytes()==(sch/'led.kicad_sch').read_bytes()
(out/'说明.txt').write_text('使用 KiCad 10。PCB/routed.kicad_pcb 和 SCH/led.kicad_sch 保持已核验源文件字节一致。\n本包只重写外部库表为相对路径，所有实际用到的封装和符号随包提供。\nPCB 原点为板框左上角；原理图与PCB使用历史原始文件名，不混用其他目录版本。\n')
manifest={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()};(out/'source-files-sha256.json').write_text(json.dumps(manifest,indent=2));print('Portable source files',len(manifest),'from',sch)
