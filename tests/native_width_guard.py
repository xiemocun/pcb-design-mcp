"""Run with KiCad Python: width normalization must not create a pad short."""
import sys
from pathlib import Path
import pcbnew as k
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/pcb_mcp'))
from route_cleanup import try_widen

b=k.BOARD();b.SetCopperLayerCount(2)
n=k.NETINFO_ITEM(b,'BAT');g=k.NETINFO_ITEM(b,'GND');b.Add(n);b.Add(g)
f=k.FOOTPRINT(b);f.SetReference('U1');b.Add(f)
layers=k.LSET();layers.AddLayer(k.F_Cu)
p=k.PAD(f);p.SetNumber('2');p.SetAttribute(k.PAD_ATTRIB_SMD);p.SetLayerSet(layers);p.SetShape(k.PAD_SHAPE_RECT)
p.SetPosition(k.VECTOR2I(k.FromMM(10),k.FromMM(10.5)));p.SetSize(k.VECTOR2I(k.FromMM(.6),k.FromMM(.25)));p.SetNet(g);f.Add(p)
t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(9.5),k.FromMM(10)));t.SetEnd(k.VECTOR2I(k.FromMM(10.5),k.FromMM(10)));t.SetWidth(k.FromMM(.25));t.SetLayer(k.F_Cu);t.SetNet(n);b.Add(t)
old=t.GetWidth()
r=try_widen(b,k,t,.8,.15)
assert not r['applied'] and p.m_Uuid.AsString() in r['blockers'] and t.GetWidth()==old
r=try_widen(b,k,t,.4,.15)
assert r['applied'] and abs(k.ToMM(t.GetWidth())-.4)<1e-8
p.SetNet(n)
r=try_widen(b,k,t,.8,.15)
assert r['applied']
print('PASS: unsafe foreign-pad widening rejected without geometry change; clear and same-net cases accepted')
