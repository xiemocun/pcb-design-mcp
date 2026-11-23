"""Fresh stdio connection using exactly the actual local registered PCB entry."""
import asyncio,json,os,tomllib
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1]
async def main():
 cfg=tomllib.loads((Path.home()/'.codex/config.toml').read_text())['mcp_servers']['pcb-design-lab'];assert cfg['command']==str(R/'.venv/bin/python') and cfg['args']==[str(R/'run_server.py')];assert cfg['env']['PCB_MCP_DATA_DIR']==str(R/'data')
 proto=json.loads((R/'acceptance-bom/report.json').read_text());led=json.loads((R/'acceptance-parts/report.json').read_text());calls=[]
 async with stdio_client(StdioServerParameters(command=cfg['command'],args=cfg['args'],env={**os.environ,**cfg['env']})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();contract=await session.list_tools();assert len(contract.tools)==68
   (R/'tool-contract.json').write_text(json.dumps(contract.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,args=None,error=None):
    v=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=v.model_dump(mode='json')))
    if error:
     assert v.isError and error in '\n'.join(c.text for c in v.content if hasattr(c,'text')),(name,error,v);return
    assert not v.isError,(name,v);return v.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.12.0';projects=await call('project_list');assert len(projects['projects'])==27
   old=await call('project_read',dict(project_id='923e1ddb9064427cbfcb3b95281191c3'));assert next(c['value'] for c in old['components'].values() if c['ref']=='R1')=='470'
   state=await call('project_read',dict(project_id=proto['project_id']));assert len(state['components'])==32 and len(state['part_bindings'])==30
   bom=await call('bom_check',dict(project_id=proto['project_id']));assert bom['counts']['identity_checked']==30 and bom['counts']['missing']==2
   assert bom['declared_rating_screen']=='conditional' and bom['verdict']=='incomplete' and not bom['manufacturing_approved']
   draft=await call('bom_draft_export',dict(project_id=proto['project_id']));assert len(draft['report']['rows'])==32
   await call('bom_export',dict(project_id=proto['project_id']),'BOM_PART_MISSING')
   await call('bom_check',dict(project_id=proto['project_id'],approved=True),'extra_forbidden')
   pt=dict(project_id=proto['project_id'],pcb_build_id=proto['pcb_build_id']);audit=await call('design_audit',pt)
   stages={s['check_id']:s['status'] for s in audit['stages']};assert audit['status']=='blocked' and stages['BOM_IDENTITY']=='incomplete'
   for key in ['ACTUAL_NETLIST','ACTUAL_ERC','ACTUAL_PCB_NETS','ACTUAL_DRC','ACTUAL_RF_AND_DECOUPLING']:assert stages[key]=='pass',(key,stages)
   await call('pcb_review_export',pt);await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED')
   lt=dict(project_id=led['project_id'],pcb_build_id=led['pcb_build_id']);led_audit=await call('design_audit',lt);assert led_audit['status']=='ready_for_human_review'
   assert (await call('bom_check',dict(project_id=led['project_id'])))['identity_verdict']=='pass'
   led_bom=await call('bom_export',dict(project_id=led['project_id']));assert len(led_bom['rows'])==3
   review=await call('manufacturing_review_prepare',lt);rr=dict(project_id=led['project_id'],review_id=review['review_id'])
   assert (await call('manufacturing_review_status',rr))['status']=='pending_human_signature'
   await call('fabrication_export',rr,'HUMAN_APPROVAL_REQUIRED');await call('fabrication_export',{**rr,'approved':True},'extra_forbidden')
   await call('manufacturing_review_status',dict(project_id='271d169a2e334222b6dedb7109d0d3b8',review_id='1626bf2009564c7bbf950d84cf3a7413'),'RULEPACK_CHANGED')
 report=dict(passed=True,call_count=len(calls),tool_count=68,contract_version='0.12.0',project_count=27,bom=bom,draft=draft,sensor_audit=audit,led_audit=led_audit,led_review=review,led_bom=led_bom,new_gerber_generated=False,real_human_approval=False,ordered=False,connection='Fresh stdio client using actual selected local configuration; existing chat tool cache not used as connection evidence.')
 (R/'acceptance-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(R/'work/live-transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['bom','draft','sensor_audit','led_audit','led_review','led_bom']},indent=2))
if __name__=='__main__':asyncio.run(main())
