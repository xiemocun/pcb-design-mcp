"""Controlled native missing-route fixture -> real DRC -> immutable seeded retry.

Only this isolated fixture rebinds source hashes after deliberate fault injection.
This is not an MCP operation or a manufacturing-authority path.
"""
from pathlib import Path
import json,shutil,subprocess,sys,uuid
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp.service import Service
from pcb_mcp.models import PCBRef,PCBStage,PCBRetry,PCBRoute
from pcb_mcp.pcb_service import worker
from pcb_mcp.common import write_json,DomainError
from pcb_mcp import backend
rep=json.loads((R/'acceptance-bom/report.json').read_text());out=R/'acceptance-connectivity-retry';out.mkdir(exist_ok=True);data=out/('run-'+uuid.uuid4().hex);shutil.copytree(rep['data_root'],data);service=Service(data);pid=rep['project_id'];bid=rep['pcb_build_id'];pt=PCBRef(project_id=pid,pcb_build_id=bid);s,b,p=service.pcb_target(pt)
code="""import pcbnew as k,sys
b=k.LoadBoard(sys.argv[1])
for t in list(b.GetTracks()):
 if t.GetNetname()=='USB_ILIM':b.RemoveNative(t)
if sys.argv[2]=='width':
 t=next(t for t in b.GetTracks() if type(t)==k.PCB_TRACK and t.GetNetname()=='VBUS_RAW');t.SetWidth(k.FromMM(.15))
k.SaveBoard(sys.argv[1],b)
"""
def inject(mode):
 s,b,p=service.pcb_target(pt);rules={p/('routed.'+e):(p/('routed.'+e)).read_bytes() for e in ['kicad_pro','kicad_dru']}
 q=subprocess.run(['/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9','-c',code,str(p/'routed.kicad_pcb'),mode],capture_output=True,text=True);assert q.returncode==0,q.stderr
 for path,raw in rules.items():path.write_bytes(raw)
 write_json(p/'board_routed.json',worker('inspect',p,'routed.kicad_pcb'))
 for n in ['routed.kicad_pcb','board_routed.json']:b['sources'][n]=backend.digest((p/n).read_bytes())
 b['checks']={};service.save(s)
 assert service.pcb_verify(PCBStage(**pt.model_dump()))['verdict']=='pass'
 return service.pcb_drc(pt)
failure=inject('gap');assert failure['verdict']=='fail' and any(f['type']=='unconnected_items' for f in failure['findings'])
parent_hash=backend.digest((p/'routed.kicad_pcb').read_bytes());s=service.load(pid);request=PCBRetry(project_id=pid,expected_revision=s['revision'],idempotency_key=uuid.uuid4().hex,pcb_build_id=bid)
new=service.pcb_retry_prepare(request);assert service.pcb_retry_prepare(request)==new
nt=PCBRef(project_id=pid,pcb_build_id=new['pcb_build_id']);s,child,folder=service.pcb_target(nt);assert child['checks']=={} and child['route_repair_parent']==bid
provenance=json.loads((folder/'route_repair_provenance.json').read_text());assert not provenance['approvals_inherited'] and not provenance['checks_inherited']
assert service.pcb_verify(PCBStage(**nt.model_dump(),stage='unrouted'))['verdict']=='pass';assert service.layout_check(PCBStage(**nt.model_dump(),stage='unrouted'))['verdict']=='pass'
attempts=0
for attempt in range(3):
 attempts+=1;service.pcb_autoroute(PCBRoute(**nt.model_dump()));assert service.pcb_verify(PCBStage(**nt.model_dump()))['verdict']=='pass';drc=service.pcb_drc(nt)
 if drc['verdict']=='pass':break
 assert all(f['type'] in ['unconnected_items','via_dangling'] for f in drc['findings']),drc
 request=PCBRetry(project_id=pid,expected_revision=s['revision'],idempotency_key=uuid.uuid4().hex,pcb_build_id=nt.pcb_build_id)
 new=service.pcb_retry_prepare(request);nt=PCBRef(project_id=pid,pcb_build_id=new['pcb_build_id'])
 assert service.pcb_verify(PCBStage(**nt.model_dump(),stage='unrouted'))['verdict']=='pass';assert service.layout_check(PCBStage(**nt.model_dump(),stage='unrouted'))['verdict']=='pass'
assert drc['verdict']=='pass',drc
assert backend.digest((p/'routed.kicad_pcb').read_bytes())==parent_hash
try:service.pcb_retry_prepare(PCBRetry(project_id=pid,expected_revision=s['revision'],idempotency_key=uuid.uuid4().hex,pcb_build_id=nt.pcb_build_id));raise AssertionError('Passing board accepted as failed')
except DomainError as e:assert e.payload['code']=='FAILED_CONNECTIVITY_REQUIRED'
failure=inject('width');assert any(f['type']=='track_width' for f in failure['findings'])
try:service.pcb_retry_prepare(PCBRetry(project_id=pid,expected_revision=s['revision'],idempotency_key=uuid.uuid4().hex,pcb_build_id=bid));raise AssertionError('Width violations waived')
except DomainError as e:assert e.payload['code']=='CONNECTIVITY_RETRY_UNSUPPORTED'
result=dict(passed=True,fixture_net='USB_ILIM',router_attempts=attempts,real_native_missing_route_fixture=True,fresh_real_drc_pass_after_seeded_router=True,parent_board_preserved=True,no_checks_or_approval_inherited=True,idempotent_retry_prepare=True,passing_parent_rejected=True,width_fault_not_waived=True,new_gerber_generated=False,real_human_approval=False,fixture_scope='Isolated test-only native fault with explicit hash rebind; original acceptance data unchanged')
(R/'acceptance-connectivity-retry.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
