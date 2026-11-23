"""Resume the pinned autorouter from edited real CAD in an isolated workbench."""
import json,subprocess,shutil,os
from pathlib import Path
root=Path('work/riding-intercom/routing-workbench').resolve();out=root/'router16';out.mkdir(exist_ok=True)
parent=Path('work/riding-intercom/data/e4569a2267ec4b0c8dbfe22e4bb60820/pcb_builds/3c17bb627cbc499797ab15d5d077b029').resolve()
py='/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9'
for ext in ['kicad_pro','kicad_dru']:shutil.copy2(root/('repair13.'+ext),out/('seed.'+ext))
shutil.copy2(parent/'fp-lib-table',out/'fp-lib-table')
code='''import pcbnew as k,sys
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from pcb_worker import export_dsn
b=k.LoadBoard(sys.argv[1])
for z in list(b.Zones()):
 if not z.GetIsRuleArea():b.RemoveNative(z)
k.SaveBoard(str(Path(sys.argv[2])/'seed.kicad_pcb'),b)
export_dsn(b,Path(sys.argv[2]),{'clearance_mm':.15})
'''
subprocess.run([py,'-c',code,str(root/'repair13.kicad_pcb'),str(out)],check=True)
settings=out/'router_data';settings.mkdir(exist_ok=True);(settings/'tmp').mkdir(exist_ok=True)
cfg=json.loads((parent/'router_data/freerouting.json').read_text());cfg['router']['max_passes']=100
(settings/'freerouting.json').write_text(json.dumps(cfg))
old=json.loads((parent/'router_command.json').read_text())['args'];args=[str(x).replace(str(parent),str(out)).replace('output.ses','output.ses') for x in old];args[args.index('-mp')+1]='100'
r=subprocess.run(args,capture_output=True,text=True,timeout=180);(out/'router-command.json').write_text(json.dumps(dict(args=args,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr),indent=2));print(r.stdout[-4000:],r.stderr[-2000:],flush=True)
if r.returncode:raise RuntimeError('Router failed')
code='''import pcbnew as k,sys
from pathlib import Path
b=k.LoadBoard(str(Path(sys.argv[1])/'seed.kicad_pcb'))
if not k.ImportSpecctraSES(b,str(Path(sys.argv[1])/'output.ses')):raise ValueError('Import failed')
k.SaveBoard(str(Path(sys.argv[1])/'routed.kicad_pcb'),b)
'''
subprocess.run([py,'-c',code,str(out)],check=True)
for ext in ['kicad_pro','kicad_dru']:shutil.copy2(out/('seed.'+ext),out/('routed.'+ext))
