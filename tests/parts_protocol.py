"""Real MCP MPN/BOM/DC engineering/PCB/review gates; never generates Gerber."""
import asyncio,json,os,sys,uuid,shutil
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]
async def run():
 out=ROOT/'acceptance-parts';out.mkdir(exist_ok=True);data=out/('run-'+uuid.uuid4().hex);calls=[];checks=[]
 params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
 async with stdio_client(params) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();tools=await session.list_tools();assert len(tools.tools)==68
   (out/'tools.json').write_text(json.dumps(tools.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,req=None,error=None):
    result=await asyncio.wait_for(session.call_tool(name,{} if req is None else {'request':req}),145)
    calls.append(dict(tool=name,arguments=req,result=result.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    if error:
     assert result.isError,(name,result);assert error in '\n'.join(x.text for x in result.content if hasattr(x,'text')),result;checks.append(error);return None
    assert not result.isError,(name,result);return result.structuredContent['data']
   p=await call('project_create',{'name':'470 ohm real-part LED prototype — pending human review'});pid=p['project_id'];rev=p['revision']
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   comps={}
   for sym,ref,val in [('Device:R','R1','470'),('Device:LED','D1','LED'),('Connector_Generic:Conn_01x02','J1','Conn_01x02')]:
    found=await call('library_search',{'query':sym});c=await call('library_get',{'candidate_id':found['candidates'][0]['candidate_id']});c=await call('component_add',m(candidate_id=c['candidate_id'],ref=ref,value=val));comps[ref]=c;rev=c['revision']
   def pin(ref,num):return dict(component_id=comps[ref]['component_id'],pin_id=next(p['pin_id'] for p in comps[ref]['pins'] if p['number']==num))
   nets={};mapping={'VCC':('power',[pin('J1','1'),pin('R1','1')]),'LED_A':('signal',[pin('R1','2'),pin('D1','2')]),'GND':('ground',[pin('D1','1'),pin('J1','2')])}
   for name,(kind,pins) in mapping.items():
    n=await call('net_create',m(name=name,kind=kind));rev=n['revision'];nets[name]=n['net_id'];rev=(await call('net_members_set',m(net_id=n['net_id'],pins=pins)))['revision']
   rev=(await call('power_source_set',m(net_id=nets['VCC'],ground_net_id=nets['GND'],boundary_pin=pin('J1','1'),nominal_v=3.0,reason='Proposed external 3V DC supply; source performance must be confirmed by human')))['revision']
   cond=dict(positive_pin=pin('J1','1'),ground_net_id=nets['GND'],nominal_v=3.0,min_v=2.7,max_v=3.3,available_current_ma=20.0,ambient_c=25.0,origin='proposed_assumption',reason='Proposed 3V laboratory LED supply envelope; not a measurement')
   rev=(await call('operating_conditions_set',m(**cond)))['revision']
   assert (await call('parts_search',{'query':'MAGIC-PART-999'}))['total']==0
   await call('part_get',{'candidate_id':'0'*32},'UNKNOWN_PART')
   physical={}
   for ref,mpn in [('R1','MRS25000C4700FCT00'),('D1','WP7113ID'),('J1','61300211121')]:
    found=await call('parts_search',{'query':mpn});assert found['total']==1,found;c=found['candidates'][0]
    await call('part_bind',m(component_id=comps[ref]['component_id'],candidate_id=c['candidate_id']),'PART_NOT_RESOLVED')
    physical[ref]=await call('part_get',{'candidate_id':c['candidate_id']})
   old=await call('footprint_search',{'query':'Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal'});old=await call('footprint_get',{'candidate_id':old['candidates'][0]['candidate_id']})
   rev=(await call('footprint_bind',m(component_id=comps['R1']['component_id'],candidate_id=old['candidate_id'],reason='Deliberate nominal-only old footprint test')))['revision']
   await call('part_bind',m(component_id=comps['R1']['component_id'],candidate_id=physical['R1']['candidate_id']),'PART_BINDING_MISMATCH')
   checks.append('Documented MRS25 minimum 10mm mounting pitch rejects old 7.62mm footprint')
   for ref,p in physical.items():
    found=await call('footprint_search',{'query':p['footprint_id']});assert found['total']==1,found;fp=await call('footprint_get',{'candidate_id':found['candidates'][0]['candidate_id']})
    rev=(await call('footprint_bind',m(component_id=comps[ref]['component_id'],candidate_id=fp['candidate_id'],reason='Exact nominal land pattern selected from source-linked part record')))['revision']
    rev=(await call('part_bind',m(component_id=comps[ref]['component_id'],candidate_id=p['candidate_id'])))['revision']
   await call('component_parameters_set',m(component_id=comps['R1']['component_id'],value='330'),'PART_BOUND_VALUE_LOCKED')
   await call('footprint_bind',m(component_id=comps['R1']['component_id'],candidate_id=old['candidate_id'],reason='Deliberate conflict test'),'PART_FOOTPRINT_CONFLICT')
   rev=(await call('board_constraints_set',m()))['revision']
   e=await call('engineering_check',{'project_id':pid});assert e['verdict']=='pass',e
   assert .007<e['calculations']['dc_current_upper_bound_a']<.0072
   assert any(f['code']=='CONDITIONS_NEED_HUMAN_CONFIRMATION' for f in e['findings'])
   rev=(await call('operating_conditions_set',m(**{**cond,'available_current_ma':1.0})))['revision']
   e=await call('engineering_check',{'project_id':pid});assert e['verdict']=='fail' and any(f['code']=='SOURCE_CURRENT_BUDGET' for f in e['findings'])
   await call('schematic_build',m(),'SPEC_INVALID')
   rev=(await call('operating_conditions_set',m(**cond)))['revision']
   bom=await call('bom_export',{'project_id':pid});assert len(bom['rows'])==3 and all(x['supplier_sku'] for x in bom['rows'])
   b=await call('schematic_build',m());sch=dict(project_id=pid,build_id=b['build_id']);assert (await call('schematic_verify',sch))['verdict']=='pass';assert (await call('erc_run',sch))['verdict']=='pass'
   await call('schematic_preview',sch);await call('schematic_export',sch)
   pcb=await call('pcb_build',m(schematic_build_id=b['build_id']));target=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
   assert (await call('pcb_verify',{**target,'stage':'unrouted'}))['verdict']=='pass'
   await call('pcb_autoroute',target);assert (await call('pcb_verify',target))['verdict']=='pass'
   assert (await call('drc_run',target))['verdict']=='pass'
   await call('pcb_preview',target)
   review=await call('manufacturing_review_prepare',target);ref=dict(project_id=pid,review_id=review['review_id'])
   assert (await call('manufacturing_review_status',ref))['status']=='authority_not_configured'
   await call('fabrication_export',ref,'APPROVAL_AUTHORITY_UNCONFIGURED')
   await call('fabrication_export',{**ref,'approved':True},'extra_forbidden')
   folder=Path(review['review_path']).parent
   assert not (folder/'fabrication').exists()
   original=(folder/'bom.json').read_bytes();(folder/'bom.json').write_bytes(original+b'\n')
   await call('manufacturing_review_status',ref,'REVIEW_CHANGED');(folder/'bom.json').write_bytes(original)
   checks+=['True MPN/SKU, source snapshots and exact land pattern records; constrained value/footprint edits','Actual KiCad netlist/ERC, Java Freerouting, real post-route pad comparison and zero DRC','Immutable review of exact PCB/BOM/assumptions; no human signature => no Gerber or drill invocation']
   demo=ROOT/'demo-parts';demo.mkdir(exist_ok=True)
   for name in ['review.html','review_summary.txt','bom.json','bom.csv','engineering.json','approval_payload.json']:shutil.copy2(folder/name,demo/name)
   for name in ['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','drc_report.json','preview/pcb.svg']:shutil.copy2(folder/'pcb'/name,demo/Path(name).name)
   report=dict(passed=True,call_count=len(calls),data_root=str(data),project_id=pid,revision=rev,schematic_build_id=b['build_id'],pcb_build_id=pcb['pcb_build_id'],review=review,checks=checks,gerber_generated=False,human_approval=False)
   (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(run())
