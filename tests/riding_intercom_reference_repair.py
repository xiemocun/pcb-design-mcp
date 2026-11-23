"""Move one bypass 1 mm to open ADC reference escape; native checks required."""
import pcbnew as k,sys,json,shutil
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from route_cleanup import try_widen
from explicit_placement import native_check
root=Path('work/riding-intercom/routing-workbench');b=k.LoadBoard(str(root/'repair06.kicad_pcb'));c=next(f for f in b.GetFootprints() if f.GetReference()=='C19');removed=[]
for t in list(b.GetTracks()):
 if t.GetNetname()=='MIC4_P' or any(t.GetNetCode()==p.GetNetCode() and t.IsOnLayer(k.F_Cu) and t.GetEffectiveShape(k.F_Cu).Collide(p.GetEffectiveShape(k.F_Cu)) for p in c.Pads()):removed.append(t.m_Uuid.AsString());b.RemoveNative(t)
items=list(b.GetTracks())+[p for f in b.GetFootprints() if f!=c for p in f.Pads()];options=[]
for angle in [0,90,180,270]:
 for xx in range(220,281):
  for yy in range(48,93):
   x,y=xx/4,yy/4;c.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));c.SetOrientationDegrees(angle)
   try:native_check(b,k,{'board_constraints':dict(width_mm=90,height_mm=65)})
   except ValueError:continue
   if any(p.GetEffectiveShape(k.F_Cu).Collide(k.SHAPE_CIRCLE(k.VECTOR2I(k.FromMM(59.2),k.FromMM(18.35)),k.FromMM(.4))) for p in c.Pads()):continue
   if any(q.GetNetCode()!=p.GetNetCode() and q.IsOnLayer(k.F_Cu) and q.GetEffectiveShape(k.F_Cu).Collide(p.GetEffectiveShape(k.F_Cu),k.FromMM(.15)) for p in c.Pads() for q in items):continue
   options.append(((x-59.5)**2+(y-17.75)**2,x,y,angle))
if not options:raise ValueError('No clear C19 placement')
_,x,y,angle=min(options);c.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));c.SetOrientationDegrees(angle);print('C19',x,y,angle,flush=True)
# Keep the reference within its own footprint for now; final silk is separate.
c.Reference().SetPosition(c.GetPosition());c.Reference().SetVisible(False)
for net,pts in [('MIC4_P',[(59.2,20.0125),(59.2,18.35)]),('REFP34',[(58.8,20.0125),(58.8,19.6),(58.7,19.0)])]:
 for a,d in zip(pts,pts[1:]):
  t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(a[0]),k.FromMM(a[1])));t.SetEnd(k.VECTOR2I(k.FromMM(d[0]),k.FromMM(d[1])));t.SetLayer(k.F_Cu);t.SetWidth(k.FromMM(.15));t.SetNetCode(b.FindNet(net).GetNetCode());b.Add(t)
  if not try_widen(b,k,t,.15,.15)['applied']:raise ValueError(('track',net,a,d))
 v=k.PCB_VIA(b);v.SetPosition(k.VECTOR2I(k.FromMM(pts[-1][0]),k.FromMM(pts[-1][1])));v.SetWidth(k.FromMM(.5));v.SetDrill(k.FromMM(.2));v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetNetCode(b.FindNet(net).GetNetCode())
 for q in list(b.GetTracks())+[p for f in b.GetFootprints() for p in f.Pads()]:
  if q.GetNetCode()==v.GetNetCode():continue
  for layer in [k.F_Cu,k.B_Cu]:
   if q.IsOnLayer(layer) and q.GetEffectiveShape(layer).Collide(v.GetEffectiveShape(layer),k.FromMM(.15)):raise ValueError(('via',net,q.GetNetname(),q.m_Uuid.AsString()))
 b.Add(v)
b.BuildConnectivity();k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(str(root/'escape10.kicad_pcb'),b)
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(root/('repair06'+ext),root/('escape10'+ext))
(root/'escape10.edits.json').write_text(json.dumps(dict(removed=removed,C19_position_mm=[x,y],C19_rotation_deg=angle),indent=2));print('Reference escape authored')
