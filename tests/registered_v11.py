"""Use the actual selected local registration, not a cached chat tool list."""
import asyncio,json,os,tomllib,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1]
async def main():
 cfg=tomllib.loads((Path.home()/'.codex/config.toml').read_text())['mcp_servers']['pcb-design-lab'];assert cfg['command']==str(R/'.venv/bin/python') and cfg['args']==[str(R/'run_server.py')];assert cfg['env']['PCB_MCP_DATA_DIR']==str(R/'data')
 proto=json.loads((R/'acceptance-copper/report.json').read_text());led=json.loads((R/'acceptance-parts/report.json').read_text());calls=[]
 async with stdio_client(StdioServerParameters(command=cfg['command'],args=cfg['args'],env={**os.environ,**cfg['env']})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();contract=await session.list_tools();assert len(contract.tools)==68;(R/'tool-contract.json').write_text(json.dumps(contract.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,args=None,error=None):
    v=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=v.model_dump(mode='json')))
    if error:
     assert v.isError and error in '\n'.join(c.text for c in v.content if hasattr(c,'text')),(name,error,v);return
    assert not v.isError,(name,v);return v.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.12.0';assert len((await call('project_list'))['projects'])==25
   original=await call('project_read',dict(project_id='923e1ddb9064427cbfcb3b95281191c3'));assert next(c['value'] for c in original['components'].values() if c['ref']=='R1')=='470'
   thin=await call('pcb_copper_check',dict(project_id=proto['thin']['project_id'],pcb_build_id=proto['thin']['pcb_build_id']));assert thin['verdict']=='fail';await call('pcb_review_export',dict(project_id=proto['thin']['project_id'],pcb_build_id=proto['thin']['pcb_build_id']))
   pt=dict(project_id=proto['project_id'],pcb_build_id=proto['pcb_build_id']);wide=await call('pcb_copper_check',pt);assert wide['verdict']=='incomplete' and wide['geometry_verdict']=='pass'
   assert all(n['track_reference']['current_reference_satisfied'] for n in wide['nets']);assert not wide['manufacturing_approved']
   state=await call('project_read',dict(project_id=proto['project_id']));assert len(state['components'])==32 and len(state['nets'])==19
   traces={}
   for ref,pin_name,net in [('U3','OUT','V3V3'),('U5','GND','GND')]:
    c=next(c for c in state['components'].values() if c['ref']==ref);pin=next(p for p in c['pins'] if p['name']==pin_name)
    v=await call('schematic_pin_connections',dict(project_id=proto['project_id'],build_id=proto['schematic_build_id'],pin=dict(component_id=c['id'],pin_id=pin['pin_id'])));assert v['net']['name']==net and v['evidence']['verified'];traces[net]=v
   await call('pcb_route_retry_prepare',dict(project_id=proto['project_id'],expected_revision=proto['revision'],idempotency_key=uuid.uuid4().hex,pcb_build_id=proto['pcb_build_id']),'FAILED_CONNECTIVITY_REQUIRED')
   gate=await call('usb_c_gate_scenarios',dict(project_id=proto['project_id']));assert gate['verdict']=='pass'
   audit=await call('design_audit',pt);stages={s['check_id']:s['status'] for s in audit['stages']};assert audit['status']=='blocked' and stages['ACTUAL_COPPER']=='incomplete'
   for key in ['ACTUAL_NETLIST','ACTUAL_ERC','ACTUAL_PCB_NETS','ACTUAL_DRC','ACTUAL_RF_AND_DECOUPLING']:assert stages[key]=='pass',(key,stages)
   await call('pcb_review_export',pt)
   await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED');await call('bom_export',dict(project_id=proto['project_id']),'BOM_PART_MISSING')
   lt=dict(project_id=led['project_id'],pcb_build_id=led['pcb_build_id']);led_audit=await call('design_audit',lt);assert led_audit['status']=='ready_for_human_review' and not led_audit['manufacturing_approved']
   bom=await call('bom_export',dict(project_id=led['project_id']));assert len(bom['rows'])==3
   await call('pcb_preview',lt);await call('pcb_review_export',lt);review=await call('manufacturing_review_prepare',lt);rr=dict(project_id=led['project_id'],review_id=review['review_id'])
   assert (await call('manufacturing_review_status',rr))['status']=='pending_human_signature'
   await call('fabrication_export',rr,'HUMAN_APPROVAL_REQUIRED');await call('fabrication_export',{**rr,'approved':True},'extra_forbidden')
   await call('manufacturing_review_status',dict(project_id='271d169a2e334222b6dedb7109d0d3b8',review_id='1626bf2009564c7bbf950d84cf3a7413'),'RULEPACK_CHANGED')
 report=dict(passed=True,call_count=len(calls),tool_count=68,contract_version='0.12.0',project_count=25,thin=thin,wide=wide,sensor_audit=audit,led_audit=led_audit,led_review=review,led_bom=bom,traces=traces,original_470_preserved=True,new_gerber_generated=False,real_human_approval=False,ordered=False,connection='Fresh stdio client using the actual registered local configuration; existing chat cache is not evidence.')
 (R/'acceptance-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(R/'work/live-transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['thin','wide','sensor_audit','led_audit','led_review','led_bom','traces']},indent=2))
if __name__=='__main__':asyncio.run(main())
