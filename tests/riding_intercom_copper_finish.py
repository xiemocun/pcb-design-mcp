"""Fix measured clearance and rebuild actual ground copper; not an exporter."""
import pcbnew as k,sys,json,shutil
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from route_cleanup import clean,try_widen
from ground_planes import add
root=Path('work/riding-intercom/routing-workbench');b=k.LoadBoard(str(root/'repair20.kicad_pcb'));s=json.loads(Path('work/riding-intercom/data/e4569a2267ec4b0c8dbfe22e4bb60820/state.json').read_text());edits=[]
for t in b.GetTracks():
 if type(t)!=k.PCB_TRACK or t.GetNetname()!='MIC4_P':continue
 changed=False
 for get,setp in [(t.GetStart,t.SetStart),(t.GetEnd,t.SetEnd)]:
  p=get();x,y=k.ToMM(p.x),k.ToMM(p.y)
  if 60.3<x<60.6 and 18.<y<18.3:setp(p+k.VECTOR2I(k.FromMM(.01),k.FromMM(.01)));changed=True
 if changed:edits.append(t.m_Uuid.AsString())
for z in list(b.Zones()):
 if not z.GetIsRuleArea():b.RemoveNative(z)
# Old stitching was placed before final routing and includes isolated copper.
# Recompute it from the actual routed native pads and keepout geometry.
# Ground vias in the ADC exposed pad require filled-and-capped manufacture.
# They are intentional thermal/return connections, not unrouted stubs.
fp=next(f for f in b.GetFootprints() if f.GetReference()=='U2');ep=next(p for p in fp.Pads() if p.GetNumber()=='33');thermal=[]
for dx,dy in [(i*.25,j*.25) for i in range(-4,5) for j in range(-4,5)]:
 pos=ep.GetPosition()+k.VECTOR2I(k.FromMM(dx),k.FromMM(dy));circle=k.SHAPE_CIRCLE(pos,k.FromMM(.25));blocked=False
 for q in list(b.GetTracks())+[p for f in b.GetFootprints() for p in f.Pads()]:
  if q.GetNetCode()!=ep.GetNetCode() and any(q.IsOnLayer(l) and q.GetEffectiveShape(l).Collide(circle,k.FromMM(.15)) for l in [k.F_Cu,k.B_Cu]):blocked=True;break
  if type(q)==k.PCB_VIA and k.ToMM((q.GetPosition()-pos).EuclideanNorm())<.6:blocked=True;break
 if blocked:continue
 v=k.PCB_VIA(b);v.SetPosition(pos);v.SetWidth(k.FromMM(.5));v.SetDrill(k.FromMM(.2));v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetNetCode(ep.GetNetCode());b.Add(v);thermal.append([k.ToMM(pos.x),k.ToMM(pos.y)])
 if len(thermal)==2:break
if len(thermal)!=2:raise ValueError('No safe ADC thermal-via pair')
print('Mandatory filled/capped thermal vias',thermal,flush=True)
cleanup=clean(b,k);policy=add(b,k,s)
k.SaveBoard(str(root/'ground23.kicad_pcb'),b)
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(root/('repair20'+ext),root/('ground23'+ext))
(root/'ground23.edits.json').write_text(json.dumps(dict(clearance_vertices=edits,filled_capped_vias_mm=thermal,cleanup=cleanup,ground_policy=policy),indent=2));print('Native ground regenerated; independent DRC still required')
