"""Prove that actual KiCad DRC reads the persisted custom track-width rule."""
import json,subprocess,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
v=json.loads((ROOT/'acceptance-pcb/pending.json').read_text());source=Path(v['data'])/v['project_id']/'pcb_builds'/v['pcb_build_id']
folder=Path(tempfile.mkdtemp(prefix='pcb-drc-probe-',dir=ROOT/'acceptance-pcb'))
for ext in ['kicad_pcb','kicad_pro','kicad_dru']:shutil.copy2(source/('unrouted.'+ext),folder/('negative.'+ext))
script='''import pcbnew as k,sys
b=k.LoadBoard(sys.argv[1]);t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(10),k.FromMM(10)));t.SetEnd(k.VECTOR2I(k.FromMM(15),k.FromMM(10)));t.SetWidth(k.FromMM(.1));t.SetLayer(k.F_Cu);t.SetNetCode(1);b.Add(t);k.SaveBoard(sys.argv[1],b)
'''
r=subprocess.run(['/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9','-c',script,str(folder/'negative.kicad_pcb')],capture_output=True,text=True,timeout=30);assert r.returncode==0,r
r=subprocess.run(['/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli','pcb','drc','--format','json','--all-track-errors','--severity-all','--exit-code-violations','-o','negative.json','negative.kicad_pcb'],cwd=folder,capture_output=True,text=True,timeout=45)
raw=json.loads((folder/'negative.json').read_text());hits=[x for x in raw['violations'] if x['type']=='track_width' and 'MCP track width' in x['description']]
assert r.returncode==5 and hits,raw
report=dict(passed=True,engine=raw['kicad_version'],proof='Actual DRC identifies MCP track width rule for a deliberate 0.1mm track against 0.3mm minimum',findings=hits,artifact=str(folder/'negative.json'))
(ROOT/'acceptance-pcb/rule_probe_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
