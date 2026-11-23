"""Search physically clear nearby vias and preserve both layer connections."""
from pathlib import Path
import pcbnew as k,math,json,shutil,sys
R=Path('work/riding-intercom/cost-down/workbench');b=k.LoadBoard(str(R/'cost05.kicad_pcb'))
P=lambda x,y:k.VECTOR2I(k.FromMM(x),k.FromMM(y))
mm=lambda p:(round(k.ToMM(p.x),6),round(k.ToMM(p.y),6))
pads=[p for f in b.GetFootprints() for p in f.Pads()];mask=[p for p in pads if p.GetAttribute()==k.PAD_ATTRIB_SMD and p.IsOnLayer(k.F_Mask)]
def clear(shape,layer,net,ignore,margin=.155):
 for sh in cached[layer]:
  if sh.Collide(shape,k.FromMM(margin)):return False
 return True
def newtrack(a,c,layer,net,width):
 t=k.PCB_TRACK(b);t.SetStart(a);t.SetEnd(c);t.SetLayer(layer);t.SetNetCode(net);t.SetWidth(k.FromMM(width));return t
moves=[]
for xy in [(56.775,16.0)]:
 v=next(t for t in b.GetTracks() if type(t)==k.PCB_VIA and mm(t.GetPosition())==xy);old=v.GetPosition();net=v.GetNetCode()
 attached=[t for t in b.GetTracks() if type(t)==k.PCB_TRACK and (t.GetStart()==old or t.GetEnd()==old)];layers={t.GetLayer() for t in attached}
 if v.GetNetname()=='GND':layers.add(k.F_Cu)
 cached={l:[q.GetEffectiveShape(l) for q in list(b.GetTracks())+pads if q!=v and q.IsOnLayer(l) and q.GetNetCode()!=net and q.GetBoundingBox().Intersects(k.BOX2I(P(xy[0]-5,xy[1]-5),P(10,10)))] for l in [k.F_Cu,k.B_Cu]}
 candidates=[]
 for ix in range(-100,101):
  for iy in range(-100,101):
   dx=ix*.025;dy=iy*.025;dist=dx*dx+dy*dy
   if .2<dist<6.25:candidates.append((dist,xy[0]+dx,xy[1]+dy))
 candidates.sort();chosen=None
 for _,x,y in candidates:
  pos=P(x,y);circ=k.SHAPE_CIRCLE(pos,v.GetWidth(k.F_Cu)//2)
  # At least 0.15mm mask-to-hole web; more than a drill just outside copper.
  hole=k.SHAPE_CIRCLE(pos,v.GetDrillValue()//2+k.FromMM(.155))
  if any(p.GetEffectiveShape(k.F_Mask).Collide(hole) for p in mask):continue
  if any(not clear(circ,l,net,v) for l in [k.F_Cu,k.B_Cu]):continue
  bad=False
  for q in list(b.GetTracks())+pads:
   if q==v:continue
   drill=q.GetDrillValue() if type(q)==k.PCB_VIA else q.GetDrillSize().x if type(q)==k.PAD else 0
   if drill and math.hypot(pos.x-q.GetPosition().x,pos.y-q.GetPosition().y)<(drill+v.GetDrillValue())/2+k.FromMM(.255):bad=True;break
  if bad:continue
  ts=[]
  for l in layers:
   width=max([k.ToMM(t.GetWidth()) for t in attached if t.GetLayer()==l] or [.25])
   options=[[old,pos],[old,P(old.x/1e6,y),pos],[old,P(x,old.y/1e6),pos]]
   if v.GetNetname()=='GND':options += [[old,P(57.8,20.8),P(57.3,20.6),pos],[old,P(57.8,23.2),P(57.3,23.4),pos]]
   valid=None
   for points in options:
    trial=[newtrack(a,c,l,net,width) for a,c in zip(points,points[1:]) if a!=c]
    if all(clear(t.GetEffectiveShape(l),l,net,v) for t in trial):valid=trial;break
   if valid is None:break
   ts+=valid
  else:chosen=(pos,ts);break
 if chosen:
  pos,ts=chosen;v.SetPosition(pos)
  for t in ts:b.Add(t)
  moves.append(dict(old=xy,new=mm(pos),net=v.GetNetname(),tracks=len(ts)));print(moves[-1],flush=True)
 else:print('NO MOVE',xy,v.GetNetname(),flush=True)
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(R/('cost05'+ext),R/('cost06'+ext))
b.BuildConnectivity();k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(str(R/'cost06.kicad_pcb'),b);(R/'moves-margin.json').write_text(json.dumps(moves,indent=2))
