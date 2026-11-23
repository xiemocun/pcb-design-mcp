"""Actual MCP power-layout revision; keeps source project and failure records."""
import asyncio,copy,hashlib,json,os,sys,time,uuid
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/power-path-review';sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts,__version__
SOURCE='0c246deec9424c14899ef4fceefe124c'
async def main():
 start=time.monotonic();frozen=parts.rules_hash();out=W/('run-'+uuid.uuid4().hex);out.mkdir();(W/'latest-run.txt').write_text(str(out));calls=[]
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (reader,writer):
  async with ClientSession(reader,writer) as session:
   await session.initialize()
   async def call(name,args=None):
    print(name,'START',flush=True);t=time.monotonic();v=await session.call_tool(name,{} if args is None else {'request':args},read_timeout_seconds=timedelta(seconds=240));calls.append(dict(tool=name,arguments=args,seconds=round(time.monotonic()-t,3),result=v.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert not v.isError,(name,v);print(name,'OK',calls[-1]['seconds'],flush=True);return v.structuredContent['data']
   cap=await call('system_capabilities');assert cap['contract_version']==__version__ and cap['features']['freerouting']
   original=await call('project_read',dict(project_id=SOURCE));fork=await call('project_fork',dict(project_id=SOURCE,expected_revision=original['revision'],name='缩短供电路径与加粗电源回流骨干',idempotency_key=uuid.uuid4().hex));pid=fork['project_id'];rev=0
   (out/'project.json').write_text(json.dumps(fork,ensure_ascii=False,indent=2))
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   policy=copy.deepcopy(original['pcb_copper_policy'])
   for rule in policy['rules']:
    if original['nets'][rule['net_id']]['name'] in ['V3V3','GND','VUSB','VBUS_RAW']:rule['min_width_mm']=1.2 if original['nets'][rule['net_id']]['name'] == 'V3V3' else .6
   policy['reason']='Keep existing current/thickness assumptions; increase MCU supply backbone to1.2mm and ground to0.6mm and USB power backbone to0.6mm. Native geometry group placement shortens MCU supply path; no physical measurement claim.'
   changed=await call('pcb_net_rules_set',m(**policy));rev=changed['revision']
   for name,key,verdict in [('spec_validate','verdict','pass'),('module_constraints_check','verdict','pass'),('power_budget_check','budget_verdict','conditional'),('bom_check','identity_verdict','pass')]:assert (await call(name,dict(project_id=pid)))[key]==verdict
   state=await call('project_read',dict(project_id=pid));(out/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2));sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id'])
   assert (await call('schematic_verify',st))['verdict']=='pass';erc=await call('erc_run',st);assert erc['verdict']=='pass';await call('schematic_preview',st);await call('schematic_export',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
   assert (await call('pcb_verify',dict(**pt,stage='unrouted')))['verdict']=='pass';assert (await call('pcb_layout_check',dict(**pt,stage='unrouted')))['verdict']=='pass'
   await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt)
   for retry in range(2):
    if drc['verdict']=='pass':break
    assert all(f['type'] in ['unconnected_items','via_dangling'] for f in drc['findings']),drc
    pcb=await call('pcb_route_retry_prepare',m(pcb_build_id=pt['pcb_build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
    assert (await call('pcb_verify',dict(**pt,stage='unrouted')))['verdict']=='pass';assert (await call('pcb_layout_check',dict(**pt,stage='unrouted')))['verdict']=='pass';await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt)
   assert drc['verdict']=='pass',drc
   assert (await call('pcb_layout_check',pt))['verdict']=='pass';copper=await call('pcb_copper_check',pt);assert copper['geometry_verdict']=='pass' and not any(f['severity']=='error' for f in copper['findings']),copper
   assert copper['dc_track_paths'] and all(p['status']=='within_reference' for p in copper['dc_track_paths']),copper['dc_track_paths']
   preview=await call('pcb_preview',pt);bundle=await call('pcb_review_export',pt);await call('bom_export',dict(project_id=pid))
   assert parts.rules_hash()==frozen
   for name,sha in json.loads((W/'baseline/states.json').read_text()).items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==sha,name
   folder=R/'data'/pid/'pcb_builds'/pt['pcb_build_id'];initial=json.loads((folder/'board_initial.json').read_text());assert initial['power_moves'],initial
   result=dict(passed=True,project_id=pid,revision=rev,source_evidence_hash=frozen,schematic_build_id=sch['build_id'],pcb_build_id=pt['pcb_build_id'],erc=erc,drc=drc,copper=copper,preview=preview,bundle=bundle,power_moves=initial['power_moves'],elapsed_seconds=round(time.monotonic()-start,2),original_projects_unchanged=True,physical_verified=False,new_gerber_generated=False)
   (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(W/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['erc','drc','copper','preview','bundle']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
