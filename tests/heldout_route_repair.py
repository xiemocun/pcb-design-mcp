"""Use the existing immutable routing repair API on a retained real failure."""
import argparse,asyncio,hashlib,json,os,sys,uuid
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main(run,output):
    frozen=json.loads(Path('work/generalization/freeze-01.json').read_text())
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen.items())
    original=json.loads((run/'verification.json').read_text());result=original['attempt']['result'];pid=result['project_id']
    assert original['passed'] is False and result['functional_reference_passed']
    output.mkdir(exist_ok=False);transcript=[]
    config=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(run/'data')})
    async with stdio_client(config) as (a,b),ClientSession(a,b) as session:
        await session.initialize()
        async def call(name,args):
            response=await session.call_tool(name,dict(request=args),read_timeout_seconds=timedelta(seconds=180))
            transcript.append(dict(tool=name,request=args,response=response.model_dump(mode='json')))
            (output/'transcript.json').write_text(json.dumps(transcript,indent=2)+'\n');assert not response.isError,str(response)
            return response.structuredContent['data']
        state=await call('project_read',dict(project_id=pid));before=hashlib.sha256(json.dumps(state['components'],sort_keys=True).encode()).hexdigest()
        built=await call('pcb_route_retry_prepare',dict(project_id=pid,expected_revision=state['revision'],idempotency_key=uuid.uuid4().hex,pcb_build_id=result['pcb_build_id']))
        target=dict(project_id=pid,pcb_build_id=built['pcb_build_id']);checks={}
        for tool in ('pcb_verify','pcb_layout_check'):
            checks[tool+'_unrouted']=await call(tool,dict(target,stage='unrouted'));assert checks[tool+'_unrouted']['verdict']=='pass'
        await call('pcb_autoroute',target)
        for tool in ('pcb_verify','drc_run','pcb_layout_check','pcb_copper_check'):
            checks[tool]=await call(tool,target)
        passed=all(v.get('geometry_verdict',v.get('verdict'))=='pass' for v in checks.values()) and not any(f['severity']=='error' for v in checks.values() for f in v.get('findings',[]))
        if passed:
            await call('pcb_preview',target);checks['pcb_review_export']=await call('pcb_review_export',target)
            checks['bom_export']=await call('bom_export',dict(project_id=pid))
        after=await call('project_read',dict(project_id=pid));assert before==hashlib.sha256(json.dumps(after['components'],sort_keys=True).encode()).hexdigest() and state['nets']==after['nets'] and state['revision']==after['revision']
        (output/'verification.json').write_text(json.dumps(dict(passed=passed,project_id=pid,parent_pcb_build_id=result['pcb_build_id'],pcb_build_id=built['pcb_build_id'],checks=checks,original_failure_preserved=True,electrical_design_unchanged=True,physical_verified=False,core_freeze_unchanged=True),indent=2)+'\n')
        print('passed' if passed else 'failed')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();asyncio.run(main(a.run.resolve(),a.output.resolve()))
