"""Extract actual native repaired geometry into the typed public authoring contract."""
import json,hashlib
from pathlib import Path
import pcbnew as k
root=Path('work/riding-intercom');src=root/'routing-workbench/compensation29.kicad_pcb'
s=json.loads((root/'data/e4569a2267ec4b0c8dbfe22e4bb60820/state.json').read_text());refs={c['ref']:c['id'] for c in s['components'].values()};nets={n['name']:n['id'] for n in s['nets'].values()};b=k.LoadBoard(str(src))
point=lambda p:dict(x_mm=round(k.ToMM(p.x),6),y_mm=round(k.ToMM(p.y),6))
placements=[dict(component_id=refs[f.GetReference()],**point(f.GetPosition()),rotation_deg=round(f.GetOrientationDegrees())%360,reason='Actual repaired native placement, source footprint identity checked again on fresh build') for f in b.GetFootprints()]
routes=[];vias=[]
for t in b.GetTracks():
 if type(t)==k.PCB_VIA:
  p=point(t.GetPosition());filled=p['x_mm']==58 and p['y_mm'] in [21.75,22.5]
  vias.append(dict(net_id=nets[t.GetNetname()],**p,diameter_mm=round(k.ToMM(t.GetWidth(k.F_Cu)),6),drill_mm=round(k.ToMM(t.GetDrillValue()),6),filled_capped=filled,reason='U2 exposed-pad via; MUST resin fill and copper cap before assembly' if filled else 'Actual native routed/stitching via; preserve physical layer connection'))
 else:
  assert type(t)==k.PCB_TRACK
  routes.append(dict(net_id=nets[t.GetNetname()],layer=b.GetLayerName(t.GetLayer()),width_mm=round(k.ToMM(t.GetWidth()),6),points=[point(t.GetStart()),point(t.GetEnd())],reason='Actual native repaired copper; independently recheck source pins, all clearances and quantitative width rules'))
out=dict(source=str(src),sha256=hashlib.sha256(src.read_bytes()).hexdigest(),placements=placements,local_routes=routes,local_vias=vias)
(root/'hardware-circuit/repaired-copper-proposal.json').write_text(json.dumps(out,indent=2));print(len(placements),'components',len(routes),'segments',len(vias),'vias',sum(v['filled_capped'] for v in vias),'filled/capped')
