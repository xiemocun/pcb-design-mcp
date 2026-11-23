"""KiCad Python 3.9: actual pad/cap faults and cleanup retention on real board."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src/pcb_mcp'))
import pcbnew as k
from pcb_worker import summary
from route_cleanup import clean
report=json.loads((R/'acceptance-usb-interlock/report.json').read_text())
folder=Path(report['data_root'])/report['project_id']/'pcb_builds'/report['pcb_build_id']
out=R/'acceptance-usb-native';out.mkdir(exist_ok=True)
def load():return k.LoadBoard(str(folder/'routed.kicad_pcb'))
def ref(b,name):return next(f for f in b.GetFootprints() if f.GetReference()==name)
cases=[]
for name,mutate in [
    ('controller_cap_moved_away',lambda b:ref(b,'C10').SetPosition(k.VECTOR2I(k.FromMM(5),k.FromMM(90)))),
    ('aux_input_pad_far_enable_unchanged',lambda b:next(p for p in ref(b,'U8').Pads() if p.GetNumber()=='1').SetPosition(k.VECTOR2I(k.FromMM(5),k.FromMM(90))))]:
    b=load();mutate(b);path=out/(name+'.kicad_pcb');k.SaveBoard(str(path),b)
    actual=summary(k.LoadBoard(str(path)));(out/(name+'.json')).write_text(json.dumps(actual,indent=2))
    cases.append(dict(name=name,actual_file=str(out/(name+'.json')),expected='DECOUPLING_DISTANCE'))
b=load();before=summary(b);ids={t.m_Uuid.AsString() for t in b.GetTracks()}
b.BuildConnectivity();conn=b.GetConnectivity()
under_pad=[t.m_Uuid.AsString() for t in b.GetTracks() if type(t)==k.PCB_TRACK and conn.TestTrackEndpointDangling(t,False) and not conn.TestTrackEndpointDangling(t,True)]
cleanup=clean(b,k);after=summary(b);assert before==after and cleanup['removed_count']==0
assert ids=={t.m_Uuid.AsString() for t in b.GetTracks()}
(out/'cases.json').write_text(json.dumps(cases,indent=2))
result=dict(passed=True,accepted_board_unchanged=True,tracks_wholly_under_pads_preserved=len(under_pad),native_clean_idempotent=True,actual_fault_boards=len(cases))
(out/'cleanup.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
