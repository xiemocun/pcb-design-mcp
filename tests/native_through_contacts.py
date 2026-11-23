"""Run in KiCad Python: existing PTH thermal holes count; SMD/NPTH do not."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
import pcbnew as k
from ground_planes import through_contacts
b=k.BOARD();b.SetCopperLayerCount(2);g=k.NETINFO_ITEM(b,'GND');b.Add(g)
f=k.FootprintLoad('/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints/RF_Module.pretty','ESP32-S3-WROOM-1U');b.Add(f)
for p in f.Pads():
    if p.GetNumber()=='41':p.SetNet(g)
contacts=through_contacts(b,k,'GND');assert len(contacts)==12
assert all(p.GetAttribute()==k.PAD_ATTRIB_PTH and p.GetDrillSize().x>0 for p in contacts)
contacts[0].SetAttribute(k.PAD_ATTRIB_NPTH)
assert len(through_contacts(b,k,'GND'))==11
v=k.PCB_VIA(b);v.SetNet(g);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetWidth(k.FromMM(.6));v.SetDrill(k.FromMM(.3));b.Add(v)
assert len(through_contacts(b,k,'GND'))==12
assert through_contacts(b,k,'OTHER')==[]
print('PASS:12 native module plated thermal holes; NPTH rejected; real ground via counted; foreign net rejected')
