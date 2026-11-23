"""Run using KiCad's Python; mutate isolated real boards, not trusted reports."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src/pcb_mcp'))
import pcbnew as k
from pcb_worker import summary
from route_cleanup import clean
report=json.loads((ROOT/'acceptance-esp32-reference/report.json').read_text())
folder=Path(report['data_root'])/report['project_id']/'pcb_builds'/report['pcb']['pcb_build_id']
out=ROOT/'acceptance-layout-faults';out.mkdir(exist_ok=True)
def load():return k.LoadBoard(str(folder/'routed.kicad_pcb'))
def ref(board,name):return next(f for f in board.GetFootprints() if f.GetReference()==name)
def track(board,layer):
    t=k.PCB_TRACK(board);t.SetStart(k.VECTOR2I(k.FromMM(50),k.FromMM(3)));t.SetEnd(k.VECTOR2I(k.FromMM(51),k.FromMM(3)));t.SetWidth(k.FromMM(.25));t.SetLayer(layer);t.SetNetCode(board.FindNet('GND').GetNetCode());board.Add(t);return t
cases=[]
for name,expected,mutate in [
    ('top_copper_intrusion','RF_COPPER_INTRUSION',lambda b:track(b,k.F_Cu)),
    ('bottom_copper_intrusion','RF_COPPER_INTRUSION',lambda b:track(b,k.B_Cu)),
    ('missing_track_protection','RF_KEEPOUT_MISSING',lambda b:list(ref(b,'U1').Zones())[0].SetDoNotAllowTracks(False)),
    ('module_rotated','RF_KEEPOUT_MISSING',lambda b:ref(b,'U1').SetOrientationDegrees(90)),
    ('bypass_cap_removed_from_rail_pin','DECOUPLING_DISTANCE',lambda b:ref(b,'C4').SetPosition(k.VECTOR2I(k.FromMM(5),k.FromMM(5)))),
    ('component_in_antenna_region','RF_COMPONENT_INTRUSION',lambda b:ref(b,'C3').SetPosition(k.VECTOR2I(k.FromMM(50),k.FromMM(4))))]:
    board=load();mutate(board);path=out/(name+'.kicad_pcb');k.SaveBoard(str(path),board)
    # Load each saved fault afresh so checks inspect actual serialized KiCad files.
    (out/(name+'.json')).write_text(json.dumps(summary(k.LoadBoard(str(path))),indent=2))
    cases.append(dict(name=name,expected=expected,actual_file=str(out/(name+'.json'))))
board=load();original=summary(board)
t=track(board,k.F_Cu);u=track(board,k.F_Cu)
cleanup=clean(board,k);actual=summary(board)
assert cleanup['removed_count']==2,cleanup
assert original==actual,'cleanup changed accepted component/net/geometry beyond the two injected dead/duplicate tracks'
(out/'cleanup.json').write_text(json.dumps(dict(passed=True,removed_count=2,baseline_preserved=True),indent=2))
(out/'cases.json').write_text(json.dumps(cases,indent=2))
print(json.dumps(dict(passed=True,native_fault_boards=len(cases),cleanup_duplicate_and_dead_branch_injection=True),indent=2))
