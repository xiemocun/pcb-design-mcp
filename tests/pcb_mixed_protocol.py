"""Second actual physical design: SMD RC + THT connector and explicit NC."""
import asyncio,json,os,sys,uuid,shutil
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]
async def run():
 out=ROOT/'acceptance-pcb-mixed';out.mkdir(exist_ok=True);data=out/('run-'+uuid.uuid4().hex);calls=[]
 params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
 async with stdio_client(params) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize()
   async def call(name,req):
    result=await asyncio.wait_for(session.call_tool(name,{'request':req}),145)
    calls.append(dict(tool=name,request=req,result=result.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert not result.isError,result
    return result.structuredContent['data']
   p=await call('project_create',{'name':'SMD RC PCB with explicit unused connector pin'});pid=p['project_id'];rev=p['revision']
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   components={}
   for sym,ref,val in [('Device:R','R1','10k'),('Device:C','C1','100nF'),('Connector_Generic:Conn_01x04','J1','Conn_01x04')]:
    found=await call('library_search',{'query':sym});c=await call('library_get',{'candidate_id':found['candidates'][0]['candidate_id']});c=await call('component_add',m(candidate_id=c['candidate_id'],ref=ref,value=val));components[ref]=c;rev=c['revision']
   def pin(ref,num):return dict(component_id=components[ref]['component_id'],pin_id=next(x['pin_id'] for x in components[ref]['pins'] if x['number']==num))
   for name,kind,pins in [('VCC','power',[pin('J1','1'),pin('R1','1')]),('OUT','signal',[pin('R1','2'),pin('C1','1'),pin('J1','2')]),('GND','ground',[pin('C1','2'),pin('J1','3')])]:
    n=await call('net_create',m(name=name,kind=kind));rev=n['revision'];rev=(await call('net_members_set',m(net_id=n['net_id'],pins=pins)))['revision']
   rev=(await call('pin_intent_set',m(pin=pin('J1','4'),intent='no_connect',reason='Reserved connector pad intentionally unused for this RC experiment')))['revision']
   for ref,ident in [('R1','Resistor_SMD:R_0805_2012Metric'),('C1','Capacitor_SMD:C_0805_2012Metric'),('J1','Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical')]:
    found=await call('footprint_search',{'query':ident});assert found['total']==1
    fp=await call('footprint_get',{'candidate_id':found['candidates'][0]['candidate_id']});rev=(await call('footprint_bind',m(component_id=components[ref]['component_id'],candidate_id=fp['candidate_id'],reason='Geometry experiment only; exact package and purchased parts unqualified')))['revision']
   rev=(await call('board_constraints_set',m(width_mm=50.0,height_mm=35.0)))['revision']
   assert (await call('pcb_preflight',{'project_id':pid}))['verdict']=='pass'
   b=await call('schematic_build',m());target=dict(project_id=pid,build_id=b['build_id'])
   assert (await call('schematic_verify',target))['verdict']=='pass';assert (await call('erc_run',target))['verdict']=='pass'
   b=await call('pcb_build',m(schematic_build_id=b['build_id']));target=dict(project_id=pid,pcb_build_id=b['pcb_build_id'])
   assert (await call('pcb_verify',{**target,'stage':'unrouted'}))['verdict']=='pass'
   await call('pcb_autoroute',target);assert (await call('pcb_verify',target))['verdict']=='pass'
   check=await call('drc_run',target);assert check['verdict']=='pass',check
   preview=await call('pcb_preview',target);exp=await call('pcb_review_export',target)
   folder=Path(exp['bundle_path']).parent;demo=ROOT/'demo-pcb-mixed';demo.mkdir(exist_ok=True)
   for name in ['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','pcb_review_bundle.zip','drc_report.json']:shutil.copy2(folder/name,demo/name)
   shutil.copy2(preview['preview_path'],demo/'preview.svg')
   report=dict(passed=True,call_count=len(calls),data_root=str(data),project_id=pid,revision=rev,pcb_build_id=b['pcb_build_id'],drc=check,checks=['Real SMD + THT geometry, three nets, explicit unused connector pad; independently checked before and after actual Freerouting; zero KiCad DRC'],qualification='geometry_connectivity_and_drc_experiment_only')
   (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(run())
