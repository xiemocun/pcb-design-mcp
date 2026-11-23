"""A real KiCad two-track chain must not become a zero-resistance net cluster."""
import sys,json
from pathlib import Path
import pcbnew as k
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src/pcb_mcp'));from native_contacts import extract
fcu=k.LSET();fcu.AddLayer(k.F_Cu)
b=k.BOARD();net=k.NETINFO_ITEM(b,'VTEST');b.Add(net)
for ref,x in [('SRC',10),('DST',30)]:
 f=k.FOOTPRINT(b);f.SetReference(ref);b.Add(f);p=k.PAD(f);p.SetNumber('1');p.SetAttribute(k.PAD_ATTRIB_SMD);p.SetShape(k.PAD_SHAPE_RECT);p.SetSize(k.VECTOR2I(k.FromMM(1),k.FromMM(1)));p.SetLayerSet(fcu);p.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(10)));p.SetNet(net);f.Add(p)
for a,z in [(10,20),(20,30)]:
 t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(a),k.FromMM(10)));t.SetEnd(k.VECTOR2I(k.FromMM(z),k.FromMM(10)));t.SetWidth(k.FromMM(.3));t.SetLayer(k.F_Cu);t.SetNet(net);b.Add(t)
r=extract(b,k,{'VTEST'});src=next(x for x in r['nodes'].values() if x.get('reference')=='SRC');assert len(src['contacts'])==1 and r['nodes'][src['contacts'][0]]['kind']=='track'
out=R.parents[1]/'work/power-path-review/native-contact-fixture.json';out.write_text(json.dumps(r,indent=2));print('native direct-contact fixture passed: source has one immediate track, not whole-net adjacency')
