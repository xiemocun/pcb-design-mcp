from pathlib import Path
import pcbnew as k,json,sys
p=Path(sys.argv[1]);b=k.LoadBoard(str(p));pads=[(f.GetReference(),p) for f in b.GetFootprints() for p in f.Pads() if p.GetAttribute()==k.PAD_ATTRIB_SMD and p.IsOnLayer(k.F_Mask)];holes=[(f.GetReference(),p,p.GetDrillSize().x) for f in b.GetFootprints() for p in f.Pads() if p.GetAttribute()==k.PAD_ATTRIB_PTH and f.GetAttributes()&k.FP_SMD]+[('via',v,v.GetDrillValue()) for v in b.GetTracks() if type(v)==k.PCB_VIA];rows=[]
for ref,h,drill in holes:
 pos=h.GetPosition();closest=[]
 for f,pad in pads:
  circle=k.SHAPE_CIRCLE(pos,drill//2+k.FromMM(.16)+max(0,pad.GetSolderMaskExpansion(k.F_Cu)))
  sh=pad.GetEffectiveShape(k.F_Mask)
  if not sh.Collide(circle):continue
  lo=0;hi=k.FromMM(.16)
  for _ in range(20):
   mid=(lo+hi)//2;circle=k.SHAPE_CIRCLE(pos,drill//2+mid+max(0,pad.GetSolderMaskExpansion(k.F_Cu)))
   if sh.Collide(circle):hi=mid
   else:lo=mid
  closest.append(dict(host=f+'.'+(pad.GetNumber() or 'mask_grid'),clearance_mm=k.ToMM(lo)))
 if closest:rows.append(dict(kind=ref,x=k.ToMM(pos.x),y=k.ToMM(pos.y),closest=sorted(closest,key=lambda x:x['clearance_mm'])))
p.with_suffix('.mask-clearance.json').write_text(json.dumps(rows,indent=2));print('MINIMUM',min(c['clearance_mm'] for r in rows for c in r['closest']));print('BELOW .1',[(r['x'],r['y'],r['closest'][0]) for r in rows if r['closest'][0]['clearance_mm']<.1]);print('near mask holes',len(rows))
