"""Rebuild the already edited mainboard under final code; no repeat of BOM edits."""
import asyncio,json,os,sys,time,uuid,shutil
from pathlib import Path
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/analog-input-protection/mainboard-regression';sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts,editing,__version__
async def main():
 old=json.loads((W/'report.json').read_text());pid=old['project_id'];frozen=parts.rules_hash();out=W/('final-'+uuid.uuid4().hex);out.mkdir();calls=[];start=time.monotonic()
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (a,b):
  async with ClientSession(a,b) as session:
   await session.initialize()
   async def call(tool,args=None):
    print(tool,'START',flush=True);v=await session.call_tool(tool,{} if args is None else dict(request=args),read_timeout_seconds=timedelta(seconds=240));calls.append(dict(tool=tool,arguments=args,result=v.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert not v.isError,v;return v.structuredContent['data']
   caps=await call('system_capabilities');assert caps['contract_version']==__version__;s=await call('project_read',dict(project_id=pid));initial=editing.spec_hash(s);rev=s['revision']
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   assert (await call('spec_validate',dict(project_id=pid)))['verdict']=='pass';assert (await call('bom_check',dict(project_id=pid)))['identity_verdict']=='pass';assert (await call('power_budget_check',dict(project_id=pid)))['budget_verdict']=='conditional'
   usb=await call('usb_c_power_check',dict(project_id=pid));assert not any(f['severity']=='error' for f in usb['findings'])
   sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id']);assert (await call('schematic_verify',st))['verdict']=='pass';erc=await call('erc_run',st);assert erc['verdict']=='pass';await call('schematic_preview',st);await call('schematic_export',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']);assert (await call('pcb_verify',dict(**pt,stage='unrouted')))['verdict']=='pass';assert (await call('pcb_layout_check',dict(**pt,stage='unrouted')))['verdict']=='pass';await call('pcb_autoroute',pt)
   assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt);assert drc['verdict']=='pass';assert (await call('pcb_layout_check',pt))['verdict']=='pass';copper=await call('pcb_copper_check',pt);assert copper['geometry_verdict']=='pass' and not any(f['severity']=='error' for f in copper['findings'])
   await call('pcb_preview',pt);bundle=await call('pcb_review_export',pt)
   assert editing.spec_hash(await call('project_read',dict(project_id=pid)))==initial;assert parts.rules_hash()==frozen
   result=dict(passed=True,project_id=pid,revision=rev,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],source_version=__version__,source_evidence_hash=frozen,usb=usb,erc=erc,drc=drc,copper=copper,bundle=bundle,spec_unchanged=True,template_used='existing_mainboard_regression_not_empty_design',physical_verified=False,new_gerber_generated=False,elapsed_seconds=round(time.monotonic()-start,2))
   shutil.copy2(W/'report.json',W/'pre-final-source-report.json');(out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(W/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(W/'latest-final.txt').write_text(str(out));print(json.dumps(dict(project_id=pid,passed=True,source_evidence_hash=frozen,elapsed_seconds=result['elapsed_seconds']),indent=2))
if __name__=='__main__':asyncio.run(main())
