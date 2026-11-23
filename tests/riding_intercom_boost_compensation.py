"""Source equation10 TPS61023: two120pC0G parallel =240p, zero906Hz with732k."""
import pcbnew as k,json,sys,shutil,math
from pathlib import Path
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
from explicit_placement import native_check
root=Path('work/riding-intercom/routing-workbench');b=k.LoadBoard(str(root/'speaker27.kicad_pcb'));template=next(f for f in b.GetFootprints() if f.GetReference()=='C10');source=next(f for f in b.GetFootprints() if f.GetReference()=='R4')
added=[]
for ref in ['C51','C52']:
 f=k.Cast_to_FOOTPRINT(template.Duplicate(False));f.SetReference(ref);b.Add(f)
 for p in f.Pads():p.SetNetCode(b.FindNet('V5' if p.GetNumber()=='1' else 'BOOST_FB').GetNetCode())
 boxes=[g.GetBoundingBox(False,False) for g in b.GetFootprints() if g!=f]
 items=list(b.GetTracks())+[p for g in b.GetFootprints() if g!=f for p in g.Pads()];options=[]
 for a in [0,90,180,270]:
  for xi in range(116,157):
   for yi in range(180,225):
    x,y=xi/4,yi/4;f.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));f.SetOrientationDegrees(a)
    box=f.GetBoundingBox(False,False)
    if any(box.Intersects(q) for q in boxes):continue
    if any(q.GetNetCode()!=p.GetNetCode() and q.IsOnLayer(k.F_Cu) and q.GetEffectiveShape(k.F_Cu).Collide(p.GetEffectiveShape(k.F_Cu),k.FromMM(.2)) for p in f.Pads() for q in items):continue
    score=sum(min(math.hypot(k.ToMM(p.GetPosition().x-q.GetPosition().x),k.ToMM(p.GetPosition().y-q.GetPosition().y)) for q in source.Pads() if q.GetNetCode()==p.GetNetCode()) for p in f.Pads())
    options.append((score,x,y,a))
 if not options:raise ValueError('No candidate')
 score,x,y,a=min(options);f.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));f.SetOrientationDegrees(a);f.Reference().SetVisible(False);added.append(dict(ref=ref,x=x,y=y,angle=a));print(added[-1],flush=True)
b.BuildConnectivity();k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(str(root/'compensation28.kicad_pcb'),b)
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(root/('speaker27'+ext),root/('compensation28'+ext))
(root/'compensation28.edits.json').write_text(json.dumps(dict(added=added,zero_hz=1/(2*math.pi*732e3*240e-12),source='TI TPS61023 section8.2.2.6 equation10; actual loop margin still requires bench verification'),indent=2))
