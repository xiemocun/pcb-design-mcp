"""Connect the native isolated top polygon through actual bottom ground copper."""
import pcbnew as k,json,sys,shutil
from pathlib import Path
root=Path('work/riding-intercom/routing-workbench');b=k.LoadBoard(str(root/'ground23.kicad_pcb'));zones={b.GetLayerName(z.GetLayer()):z for z in b.Zones() if not z.GetIsRuleArea()};front=zones['F.Cu'].GetFilledPolysList(k.F_Cu);back=zones['B.Cu'].GetFilledPolysList(k.B_Cu);items=list(b.GetTracks())+[p for f in b.GetFootprints() for p in f.Pads()];placed=None
# Locate the measured island by geometry, not a transient polygon index.
index=next(i for i in range(front.OutlineCount()) if front.Contains(k.VECTOR2I(k.FromMM(54.85),k.FromMM(21.15)),i))
for x,y in [(i/10,j/10) for i in range(538,559) for j in range(208,222)]:
 pos=k.VECTOR2I(k.FromMM(x),k.FromMM(y));circle=k.SHAPE_CIRCLE(pos,k.FromMM(.25));drill=k.SHAPE_CIRCLE(pos,k.FromMM(.2));ok=True
 if not front.Contains(pos,index) or not back.Contains(pos):continue
 for q in items:
  if q.GetNetname()!='GND' and any(q.IsOnLayer(l) and q.GetEffectiveShape(l).Collide(circle,k.FromMM(.15)) for l in [k.F_Cu,k.B_Cu]):ok=False;break
  if type(q)==k.PAD and q.GetEffectiveShape(k.F_Cu).Collide(drill):ok=False;break
  if type(q)==k.PCB_VIA and k.ToMM((q.GetPosition()-pos).EuclideanNorm())<.6:ok=False;break
 if not ok:continue
 v=k.PCB_VIA(b);v.SetPosition(pos);v.SetWidth(k.FromMM(.5));v.SetDrill(k.FromMM(.2));v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetNetCode(b.FindNet('GND').GetNetCode());b.Add(v);placed=[x,y];break
if not placed:raise ValueError('No valid island stitch')
# These exact vias are diagnosed natively as one-layer branches. The signals
# are now complete; remove only these five and immediately rerun DRC.
d=json.load(open(root/'drc23.json'));ids={x['items'][0]['uuid'] for x in d['violations'] if x['type']=='via_dangling'}
for t in list(b.GetTracks()):
 if t.m_Uuid.AsString() in ids:b.RemoveNative(t)
b.BuildConnectivity();k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(str(root/'bridge24.kicad_pcb'),b)
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(root/('ground23'+ext),root/('bridge24'+ext))
(root/'bridge24.edits.json').write_text(json.dumps(dict(island_stitch_mm=placed,removed_native_dangling_vias=sorted(ids)),indent=2));print('Ground island stitched at',placed)
