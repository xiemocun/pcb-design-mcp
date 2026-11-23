"""Native serialized fault boards, isolated from accepted artifacts and receipts."""
import json,shutil,subprocess,sys,math
from pathlib import Path
import pcbnew as k
R=Path(__file__).resolve().parents[1];rep=json.loads((R/'acceptance-bom/report.json').read_text());p=Path(rep['data_root'])/rep['project_id']/'pcb_builds'/rep['pcb_build_id'];out=R/'acceptance-copper-native';out.mkdir(exist_ok=True)
actual=json.loads((p/'copper_geometry.json').read_text());ground=next(n for n in json.loads((p/'copper_report.json').read_text())['nets'] if n['name']=='GND');escape_ids=ground['escape']['tracks']
assert escape_ids and ground['escape']['isolation']['non_controller_pads_still_connected']
first_escape=max((x for x in actual['conductors'] if x['uuid'] in escape_ids),key=lambda t:t['length_mm'])['uuid']
first_supply=max((x for x in actual['conductors'] if x['kind']=='track' and x['net']=='V3V3' and x['width_mm']>=.3-1e-6),key=lambda t:t['length_mm'])['uuid']
def load():return k.LoadBoard(str(p/'routed.kicad_pcb'))
def track(b,uid):return next(t for t in b.GetTracks() if t.m_Uuid.AsString()==uid)
def drc(folder,name):
 q=subprocess.run(['/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli','pcb','drc','--format','json','--all-track-errors','--severity-all','--exit-code-violations','-o',str(folder/name),str(folder/'routed.kicad_pcb')],capture_output=True,text=True)
 assert q.returncode in [0,5],(q.returncode,q.stderr);v=json.loads((folder/name).read_text());return q.returncode,v
cases=[]
for name,expected in [('supply_backbone_too_thin','COPPER_TRACK_TOO_NARROW'),('controller_escape_too_thin','COPPER_TRACK_TOO_NARROW'),('controller_escape_total_too_long','COPPER_ESCAPE_LENGTH'),('non_controller_ground_disconnected','COPPER_ESCAPE_BACKBONE_UNPROVEN')]:
 b=load()
 if name=='supply_backbone_too_thin':track(b,first_supply).SetWidth(k.FromMM(.15))
 if name=='controller_escape_too_thin':track(b,first_escape).SetWidth(k.FromMM(.15))
 if name=='controller_escape_total_too_long':
  original=track(b,first_escape)
  count=math.ceil(3.0/k.ToMM(original.GetLength()))+1
  for i in range(count):
   t=k.PCB_TRACK(b);t.SetStart(original.GetStart());t.SetEnd(original.GetEnd());t.SetWidth(original.GetWidth());t.SetLayer(original.GetLayer());t.SetNetCode(original.GetNetCode());b.Add(t)
 if name=='non_controller_ground_disconnected':
  fp=next(f for f in b.GetFootprints() if f.GetReference()=='U3');pad=next(p for p in fp.Pads() if p.GetNumber()=='2');assert pad.GetNetname()=='GND';pad.SetPosition(k.VECTOR2I(k.FromMM(5),k.FromMM(95)))
 folder=out/name;folder.mkdir(exist_ok=True);k.SaveBoard(str(folder/'routed.kicad_pcb'),b)
 for filename in ['routed.kicad_pro','routed.kicad_dru','fp-lib-table','spec.json']:shutil.copy2(p/filename,folder/filename)
 q=subprocess.run([sys.executable,str(R/'src/pcb_mcp/pcb_worker.py'),'inspect',str(folder),'routed.kicad_pcb'],capture_output=True,text=True);assert q.returncode==0,q.stderr
 (folder/'actual.json').write_text(q.stdout);extra={}
 if name in ['supply_backbone_too_thin','controller_escape_too_thin']:
  code,v=drc(folder,'drc-strict.json');assert code==5 and any(f['type']=='track_width' for f in v['violations']);extra['real_drc_width_rejection']=True
 if name=='supply_backbone_too_thin':
  original=(folder/'routed.kicad_dru').read_text();global_only='\n'.join(line for line in original.splitlines() if not line.startswith('(rule "MCP MCP_') and not line.startswith('(rule "MCP_ESCAPE_'))+'\n'
  (folder/'routed.kicad_dru').write_text(global_only);code,v=drc(folder,'drc-optimal-netclasses-only.json');assert code==0,(code,v)
  (folder/'routed.kicad_dru').write_text(original);extra['same_fault_not_caught_by_optimal_netclass_alone']=True
 cases.append(dict(name=name,expected=expected,actual_file=str(folder/'actual.json'),**extra))
(out/'cases.json').write_text(json.dumps(cases,indent=2));print(json.dumps(dict(passed=True,native_fault_boards=len(cases),custom_drc_minimum_demonstrated=True),indent=2))
