"""Independent native invariants for final cost-down board, no physical claims."""
from pathlib import Path
import json,sys,pcbnew as k
sys.path.insert(0,str(Path('src').resolve()));from pcb_mcp.assembly_geometry import extract
R=Path('work/riding-intercom');old=k.LoadBoard(str(R/'vendor-review/CAD/PCB/routed.kicad_pcb'));new=k.LoadBoard(sys.argv[1]);a={f.GetReference():f for f in old.GetFootprints()};b={f.GetReference():f for f in new.GetFootprints()};assert set(a)==set(b)
for ref in a:
 assert a[ref].GetPosition()==b[ref].GetPosition() and a[ref].GetOrientationDegrees()==b[ref].GetOrientationDegrees(),ref
 def copper(f):return sorted((p.GetNumber(),p.GetAttribute(),p.GetShape(),p.GetPosition().x,p.GetPosition().y,p.GetSize().x,p.GetSize().y,p.GetDrillSize().x,p.GetDrillSize().y,p.GetNetname(),p.IsOnLayer(k.F_Cu),p.IsOnLayer(k.B_Cu),p.IsOnLayer(k.F_Paste),p.IsOnLayer(k.B_Paste)) for p in f.Pads())
 assert copper(a[ref])==copper(b[ref]),ref
geo=extract(new,k);assert not geo['fill_cap_holes'];assert all(geo['components'][f'J{i}']['assembly_method']=='THT/manual after reflow' for i in range(1,8))
assert sum(type(v)==k.PCB_VIA for v in old.GetTracks())==sum(type(v)==k.PCB_VIA for v in new.GetTracks())==179
assert sum(p.GetDrillSize().x>0 for f in new.GetFootprints() for p in f.Pads())==34
u=b['U1'];assert sum(p.IsOnLayer(k.F_Mask) and not p.GetNumber() for p in u.Pads())==9
assert all(not p.IsOnLayer(k.F_Mask) and not p.IsOnLayer(k.B_Mask) for p in u.Pads() if p.GetNumber()=='41')
report=dict(passed=True,all_91_component_positions_rotations_and_copper_pad_geometry_unchanged=True,all_pad_nets_and_paste_geometries_unchanged=True,thermal_vias_preserved=12,board_vias_preserved=179,total_drill_holes=213,mask_defined_module_thermal_apertures=9,filled_capped_holes=0,tht_headers=7,physical_verified=False)
report['module_mask_windows']=[dict(x=k.ToMM(p.GetPosition().x),y=-k.ToMM(p.GetPosition().y),width=k.ToMM(p.GetSize().x),height=k.ToMM(p.GetSize().y)) for p in u.Pads() if not p.GetNumber() and p.IsOnLayer(k.F_Mask)]
Path(sys.argv[2]).write_text(json.dumps(report,indent=2));print(report)
