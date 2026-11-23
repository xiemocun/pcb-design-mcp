"""Native cost-down engineering proposal; independent DRC required."""
from pathlib import Path
import pcbnew as k,json,shutil
R=Path('work/riding-intercom');O=R/'cost-down';W=O/'workbench';W.mkdir(exist_ok=True)
src=R/'vendor-review/CAD/PCB/routed.kicad_pcb';b=k.LoadBoard(str(src))
for ext in ['.kicad_pro','.kicad_dru']:
 if src.with_suffix(ext).exists():shutil.copy2(src.with_suffix(ext),W/('cost01'+ext))
u=next(f for f in b.GetFootprints() if f.GetReference()=='U1')
def mask_grid(f):
 for p in f.Pads():
  layers=p.GetLayerSet()
  if p.GetNumber()=='41':layers.RemoveLayer(k.F_Mask);layers.RemoveLayer(k.B_Mask);p.SetLayerSet(layers)
  elif not p.GetNumber() and p.IsOnLayer(k.F_Paste):layers.AddLayer(k.F_Mask);p.SetLayerSet(layers);p.SetLocalSolderMaskMargin(0)
mask_grid(u)
# Export source-derived footprint with unchanged copper, paste and thermal holes.
f=k.FootprintLoad('/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints/RF_Module.pretty','ESP32-S3-WROOM-1U');mask_grid(f)
lib=O/'EXT_RIDING_COST.pretty';lib.mkdir(exist_ok=True);f.SetFPID(k.LIB_ID('EXT_RIDING_COST','ESP32-S3-WROOM-1U_MaskGrid'));io=k.PCB_IO_KICAD_SEXPR();io.Format(f);(lib/'ESP32-S3-WROOM-1U_MaskGrid.kicad_mod').write_text(io.GetStringOutput(True))
k.SaveBoard(str(W/'cost01.kicad_pcb'),b)
for f in b.GetFootprints():
 if f.GetReference() in ['U2','R14','C20','R4','C35']:
  print(f.GetReference(),[(p.GetNumber(),round(k.ToMM(p.GetPosition().x),3),round(k.ToMM(p.GetPosition().y),3),round(k.ToMM(p.GetSize().x),3),round(k.ToMM(p.GetSize().y),3),p.GetNetname()) for p in f.Pads()])
print('MASK GRID SAVED')
