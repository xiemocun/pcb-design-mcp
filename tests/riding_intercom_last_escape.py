import pcbnew as k,sys,json,math,shutil
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from route_cleanup import try_widen
root=Path('work/riding-intercom/routing-workbench');b=k.LoadBoard(str(root/'repair06.kicad_pcb'));net='REFP34';p=next(p for f in b.GetFootprints() if f.GetReference()=='U2' for p in f.Pads() if p.GetNumber()=='29');x,y=k.ToMM(p.GetPosition().x),k.ToMM(p.GetPosition().y)
options=sorted((abs(dx)+d,d,dx,lead) for d in [.7,.8,.9,1.,1.2,1.4,1.6,1.8,2.,2.2,2.5] for dx in [i*.1 for i in range(-15,16)] for lead in [.4,.5,.6,.7] if d>=lead)
items=list(b.GetTracks())+[p for f in b.GetFootprints() for p in f.Pads()];found=None
for _,d,dx,lead in options:
 pt=[(x,y),(x,y-lead),(x+dx,y-d)];objects=[];ok=True
 for a,c in zip(pt,pt[1:]):
  t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(a[0]),k.FromMM(a[1])));t.SetEnd(k.VECTOR2I(k.FromMM(c[0]),k.FromMM(c[1])));t.SetWidth(k.FromMM(.15));t.SetLayer(k.F_Cu);t.SetNetCode(p.GetNetCode());b.Add(t);objects.append(t)
  if not try_widen(b,k,t,.15,.15)['applied']:ok=False;break
 if ok:
  v=k.PCB_VIA(b);v.SetPosition(k.VECTOR2I(k.FromMM(pt[-1][0]),k.FromMM(pt[-1][1])));v.SetWidth(k.FromMM(.5));v.SetDrill(k.FromMM(.2));v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetNetCode(p.GetNetCode())
  for q in items:
   if q.GetNetCode()==v.GetNetCode():continue
   for layer in [k.F_Cu,k.B_Cu]:
    if q.IsOnLayer(layer) and q.GetEffectiveShape(layer).Collide(v.GetEffectiveShape(layer),k.FromMM(.15)):ok=False;break
   if not ok:break
  if ok:b.Add(v);found=pt;break
 for t in objects:b.RemoveNative(t)
if found is None:raise ValueError('No escape candidate')
b.BuildConnectivity();k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(str(root/'escape07.kicad_pcb'),b)
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(root/('repair06'+ext),root/('escape07'+ext))
(root/'escape07.path.json').write_text(json.dumps(found));print(found)
