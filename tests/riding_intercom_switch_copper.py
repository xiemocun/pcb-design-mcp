"""Author short boost switch-node copper with checked real geometry."""
import pcbnew as k,sys,json,shutil
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from route_cleanup import try_widen
root=Path('work/riding-intercom/routing-workbench');b=k.LoadBoard(str(root/'bridge24.kicad_pcb'));removed=[]
for t in list(b.GetTracks()):
 if t.GetNetname()=='BOOST_SW':removed.append(t.m_Uuid.AsString());b.RemoveNative(t)
v=next(t for t in b.GetTracks() if t.m_Uuid.AsString()=='fa5a4a55-7e03-4f57-9861-56ee8ddf9211');old=v.GetPosition();new=k.VECTOR2I(k.FromMM(28.3),k.FromMM(44.5));v.SetPosition(new)
for t in list(b.GetTracks()):
 if type(t)!=k.PCB_TRACK or t.GetNetname()!='GND':continue
 if t.GetStart()==old:t.SetStart(new)
 if t.GetEnd()==old:t.SetEnd(new)
rows=[(.25,[(29.2875,46),(28.7,46),(28.4,45.8)]),(.8,[(28.4,45.8),(27.6,45.8),(26.685,45)])]
for width,pts in rows:
 for a,c in zip(pts,pts[1:]):
  t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(a[0]),k.FromMM(a[1])));t.SetEnd(k.VECTOR2I(k.FromMM(c[0]),k.FromMM(c[1])));t.SetWidth(k.FromMM(width));t.SetLayer(k.F_Cu);t.SetNetCode(b.FindNet('BOOST_SW').GetNetCode());b.Add(t)
  r=try_widen(b,k,t,width,.15)
  if not r['applied']:raise ValueError((a,c,r))
b.BuildConnectivity();k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(str(root/'switch25.kicad_pcb'),b)
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(root/('bridge24'+ext),root/('switch25'+ext))
(root/'switch25.edits.json').write_text(json.dumps(dict(removed=removed,new_routes=rows),indent=2));print('Switch node widened after short physical pad escape')
