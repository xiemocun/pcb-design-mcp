"""Read the actual new KiCad PCB; no geometry edits or manufacture output."""
import hashlib,json,math
from pathlib import Path
import pcbnew as k
R=Path(__file__).resolve().parents[1];r=json.loads((R/'acceptance-complete-bom/report.json').read_text());p=R/'data'/r['project_id']/'pcb_builds'/r['pcb_build_id']/'routed.kicad_pcb';b=k.LoadBoard(str(p));fs={f.GetReference():f for f in b.GetFootprints()}
ident=lambda f:str(f.GetFPID().GetLibNickname())+':'+str(f.GetFPID().GetLibItemName())
header=fs['J2'];assert ident(header)=='Connector_PCB_MCP:WR_PHD_1x06_P2.54mm_Drill1.10mm';pads=sorted(header.Pads(),key=lambda p:int(p.GetNumber()));assert len(pads)==6
holes=[[k.ToMM(p.GetDrillSize().x),k.ToMM(p.GetDrillSize().y)] for p in pads];assert all(abs(d-1.1)<1e-6 for h in holes for d in h)
positions=[(k.ToMM(p.GetPosition().x),k.ToMM(p.GetPosition().y)) for p in pads];spans=[math.hypot(a[0]-b[0],a[1]-b[1]) for a,b in zip(positions,positions[1:])];assert all(abs(d-2.54)<1e-6 for d in spans)
cap=fs['C9'];cp={p.GetNumber():p for p in cap.Pads()};assert cp['1'].GetNetname()=='VUSB' and cp['2'].GetNetname()=='GND'
a=cp['1'].GetPosition();z=cp['2'].GetPosition();distance=math.hypot(k.ToMM(a.x-z.x),k.ToMM(a.y-z.y));assert abs(distance-2.5)<1e-6
report=dict(passed=True,pcb_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),header_footprint=ident(header),header_native_drills_mm=holes,header_native_pitch_mm=spans,capacitor_footprint=ident(cap),capacitor_native_pitch_mm=distance,capacitor_pad_nets={n:p.GetNetname() for n,p in cp.items()},physical_sample_qualified=False,scope='Serialized KiCad geometry/declared pin mapping only, not drill manufacture tolerance or physical assembly qualification')
(R/'acceptance-native-bom.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
