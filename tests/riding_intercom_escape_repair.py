"""Local CAD edit to open three ADC escapes; all changes require fresh DRC."""
import pcbnew as k,json,sys,shutil
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from route_cleanup import try_widen
root=Path('work/riding-intercom/routing-workbench');b=k.LoadBoard(str(root/'repair04.kicad_pcb'))
removed=[]
for t in list(b.GetTracks()):
 if t.GetNetname() in ['MIC4_P','REFP34','I2C_SCL'] or (t.GetNetname()=='A3V3' and t.IsOnLayer(k.B_Cu) and t.GetEffectiveShape(k.B_Cu).Collide(k.SHAPE_CIRCLE(k.VECTOR2I(k.FromMM(55.9),k.FromMM(21.65)),k.FromMM(.52)))):
  removed.append(t.m_Uuid.AsString());b.RemoveNative(t)
def track(net,xy):
 for a,c in zip(xy,xy[1:]):
  t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(a[0]),k.FromMM(a[1])));t.SetEnd(k.VECTOR2I(k.FromMM(c[0]),k.FromMM(c[1])));t.SetWidth(k.FromMM(.15));t.SetLayer(k.F_Cu);t.SetNetCode(b.FindNet(net).GetNetCode());b.Add(t)
  ok=try_widen(b,k,t,.15,.15)
  if not ok['applied']:raise ValueError((net,a,c,ok))
def via(net,x,y):
 v=k.PCB_VIA(b);v.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));v.SetWidth(k.FromMM(.5));v.SetDrill(k.FromMM(.2));v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetNetCode(b.FindNet(net).GetNetCode())
 for q in list(b.GetTracks())+[p for f in b.GetFootprints() for p in f.Pads()]:
  if q.GetNetCode()==v.GetNetCode():continue
  for layer in [k.F_Cu,k.B_Cu]:
   if q.IsOnLayer(layer) and q.GetEffectiveShape(layer).Collide(v.GetEffectiveShape(layer),k.FromMM(.15)):raise ValueError(('via_collision',net,q.m_Uuid.AsString()))
 b.Add(v)
# Open local ground routing channel; plane contacts are rechecked natively.
for t in list(b.GetTracks()):
 if t.GetNetname()!='GND':continue
 box=t.GetBoundingBox()
 if k.ToMM(box.GetX())<57.5 and k.ToMM(box.GetRight())>55 and k.ToMM(box.GetY())<21.3 and k.ToMM(box.GetBottom())>20.3:
  removed.append(t.m_Uuid.AsString());b.RemoveNative(t)
track('MIC4_P',[(59.2,20.0125),(59.2,19.4),(59.1,19.2),(59.1,18.85)]);via('MIC4_P',59.1,18.85)
track('MIC4_N',[(59.6,20.0125),(59.6,19.65),(59.75,19.05)]);via('MIC4_N',59.75,19.05)
track('I2C_SDA',[(57.0125,21.4),(56.35,21.4),(56.1,21.15),(56.1,20.65)]);via('I2C_SDA',56.1,20.65)
track('I2C_SCL',[(57.0125,21.8),(56.2,21.8),(55.9,21.65)]);via('I2C_SCL',55.9,21.65)
b.BuildConnectivity();k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(str(root/'escape05.kicad_pcb'),b)
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(root/('repair04'+ext),root/('escape05'+ext))
(root/'escape05.edits.json').write_text(json.dumps(dict(removed_tracks=removed,reason='Remove two obstructing signal routes, author three previously trapped ADC escapes; reroute all broken nets; final independent DRC required'),indent=2))
print('Escapes authored')
