"""Read actual native manufacturing geometry, no board mutation or Gerber export."""
import pcbnew as k,json,sys
from pathlib import Path
p=Path(sys.argv[1]);out=Path(sys.argv[2]);b=k.LoadBoard(str(p));data=dict(board_file=str(p.resolve()),thickness_mm=k.ToMM(b.GetDesignSettings().GetBoardThickness()),coordinates='KiCad PCB top view: X right, Y down from upper-left board origin; native placement CSV uses Y up',components={},fill_cap_holes=[])
for f in b.GetFootprints():
 pads=[]
 for pad in f.Pads():
  pos=pad.GetPosition();drill=pad.GetDrillSize();row=dict(pin=pad.GetNumber(),net=pad.GetNetname(),x=round(k.ToMM(pos.x),6),y=round(k.ToMM(pos.y),6),drill_x_mm=k.ToMM(drill.x),drill_y_mm=k.ToMM(drill.y));pads.append(row)
  # Footprint-integrated plated exposed-pad thermal drills must be capped too.
  if drill.x and f.GetReference()=='U1' and pad.GetNumber()=='41':data['fill_cap_holes'].append(dict(kind='footprint_thermal_hole',reference='U1',**row))
 data['components'][f.GetReference()]=dict(x=k.ToMM(f.GetPosition().x),y=k.ToMM(f.GetPosition().y),angle=f.GetOrientationDegrees(),pads=pads)
smd=[(f.GetReference(),p) for f in b.GetFootprints() for p in f.Pads() if p.GetAttribute()==k.PAD_ATTRIB_SMD and p.IsOnLayer(k.F_Cu)]
for v in b.GetTracks():
 if type(v)!=k.PCB_VIA:continue
 hosts=[ref+'.'+p.GetNumber() for ref,p in smd if p.HitTest(v.GetPosition())]
 if hosts:data['fill_cap_holes'].append(dict(kind='board_via_in_pad',hosts=hosts,x=k.ToMM(v.GetPosition().x),y=k.ToMM(v.GetPosition().y),drill_x_mm=k.ToMM(v.GetDrillValue()),drill_y_mm=k.ToMM(v.GetDrillValue())))
out.write_text(json.dumps(data,indent=2));print('Factory geometry',len(data['components']),'components',len(data['fill_cap_holes']),'filled/capped holes',data['thickness_mm'],'mm')
