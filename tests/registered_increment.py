"""Read through a fresh process using actual selected local registration."""
import asyncio,json,os,tomllib
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1]
async def main():
 cfg=tomllib.loads((Path.home()/'.codex/config.toml').read_text())['mcp_servers']['pcb-design-lab'];assert cfg['command']==str(R/'.venv/bin/python') and cfg['args']==[str(R/'run_server.py')] and cfg['env']['PCB_MCP_DATA_DIR']==str(R/'data')
 fixture=json.loads((R/'acceptance-reset/report.json').read_text());pid=fixture['project_id'];calls=[];before={p:p.read_bytes() for p in (R/'data').glob('*/state.json')}
 async with stdio_client(StdioServerParameters(command=cfg['command'],args=cfg['args'],env={**os.environ,**cfg['env']})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();listing=await session.list_tools();assert len(listing.tools)==72
   async def call(name,args=None,error=None):
    v=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=v.model_dump(mode='json')))
    if error:
     assert v.isError and error in '\n'.join(c.text for c in v.content if hasattr(c,'text')),(name,error,v);return
    assert not v.isError,(name,v);return v.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.16.0'
   s=await call('project_read',dict(project_id=pid));assert s['revision']==fixture['revision'] and len(s['part_bindings'])==32
   baseline=await call('reset_timing_check',dict(project_id=pid,revision=s['revision']));assert baseline==fixture['baseline'] and baseline['reference_timing_verdict']=='incomplete'
   fast=await call('reset_timing_check',fixture['fast_request']);assert fast==fixture['fast_report'] and fast['reference_timing_verdict']=='conditional' and fast['verdict']=='incomplete'
   slow=await call('reset_timing_check',fixture['slow_request']);assert slow['reference_timing_verdict']=='fail' and not slow['manufacturing_approved']
   ready=await call('design_readiness',dict(project_id=pid));assert ready['status']=='blocked' and not ready['manufacturing_approved'] and next(g for g in ready['gates'] if g['check_id']=='EN_RESET_TIMING')['status']=='blocked'
   await call('manufacturing_review_prepare',dict(project_id=pid,pcb_build_id='0'*32),'UNKNOWN_PCB_BUILD')
   led=await call('project_read',dict(project_id='923e1ddb9064427cbfcb3b95281191c3'));assert next(c['value'] for c in led['components'].values() if c['library_id']=='Device:R')=='470'
 for p,data in before.items():assert p.read_bytes()==data,p
 result=dict(passed=True,call_count=len(calls),tool_count=72,contract_version='0.16.0',project_id=pid,baseline=baseline,fast=fast,slow=slow,readiness=ready,project_states_unchanged=len(before),old_470_ohm_project_unchanged=True,new_gerber_generated=False,manufacturing_approved=False,ordered=False,connection='Fresh stdio process using selected local configuration; does not assert automatic refresh of existing chat tool caches')
 (R/'acceptance-live.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(R/'work/live-transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['baseline','fast','slow','readiness']},indent=2))
if __name__=='__main__':asyncio.run(main())
