from pathlib import Path
import pcbnew as k,shutil,json
R=Path('work/riding-intercom');W=R/'cost-down/workbench';b=k.LoadBoard(str(W/'cost04.kicad_pcb'))
v=next(v for v in b.GetTracks() if type(v)==k.PCB_VIA and v.GetNetname()=='MIC4_N' and abs(k.ToMM(v.GetPosition().y)-14.)<1e-6)
t=k.PCB_TRACK(b);t.SetStart(k.VECTOR2I(k.FromMM(59.5),k.FromMM(13.275)));t.SetEnd(v.GetPosition());t.SetNetCode(v.GetNetCode());t.SetWidth(k.FromMM(.15));t.SetLayer(k.F_Cu);b.Add(t)
u=next(f for f in b.GetFootprints() if f.GetReference()=='U1');u.SetFPID(k.LIB_ID('EXT_RIDING_COST','ESP32-S3-WROOM-1U_MaskGrid'))
for ext in ['.kicad_pro','.kicad_dru']:shutil.copy2(W/('cost04'+ext),W/('cost05'+ext))
p=R/'data/e4569a2267ec4b0c8dbfe22e4bb60820/pcb_builds/3c7f46f4a2334ef8959c9360e7650562/fp-lib-table';raw=p.read_text().rstrip();raw=raw[:-1]+'\n(lib (name "EXT_RIDING_COST")(type "KiCad")(uri '+json.dumps(str((R/'cost-down/EXT_RIDING_COST.pretty').resolve()))+')(options "")(descr "Source adapted mask grid")))\n';(W/'fp-lib-table').write_text(raw)
b.BuildConnectivity();k.ZONE_FILLER(b).Fill(b.Zones());k.SaveBoard(str(W/'cost05.kicad_pcb'),b)
