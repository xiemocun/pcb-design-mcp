"""Actual native plated/NPTH hole reference for independent Excellon comparison."""
import pcbnew as k,json,sys
from pathlib import Path
b=k.LoadBoard(sys.argv[1]);holes=[]
for t in b.GetTracks():
 if type(t)==k.PCB_VIA:holes.append(dict(x=k.ToMM(t.GetPosition().x),y=-k.ToMM(t.GetPosition().y),diameter=k.ToMM(t.GetDrillValue()),kind='PTH'))
for f in b.GetFootprints():
 for p in f.Pads():
  d=p.GetDrillSize()
  if d.x:
   assert d.x==d.y,'Slot must be parsed separately'
   holes.append(dict(x=k.ToMM(p.GetPosition().x),y=-k.ToMM(p.GetPosition().y),diameter=k.ToMM(d.x),kind='NPTH' if p.GetAttribute()==k.PAD_ATTRIB_NPTH else 'PTH'))
Path(sys.argv[2]).write_text(json.dumps(holes,indent=2));print('Actual native drill reference:',len(holes),'holes')
