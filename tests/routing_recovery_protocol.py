"""Apply the common recovery client to a copied, real failed KiCad build.

The original project and its failure evidence remain untouched. This is a
known-failure development regression, not an independent v1.0 task.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp_client.routing import route_and_verify


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


async def main(args):
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    data=output/'data';shutil.copytree(args.source_data.resolve(),data)
    transcript=[];history=[]
    config=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],
                                env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(config) as (a,b),ClientSession(a,b) as session:
        await session.initialize()
        async def call(tool,params):
            response=await session.call_tool(tool,dict(request=params),read_timeout_seconds=timedelta(seconds=240))
            transcript.append(dict(tool=tool,request=params,response=response.model_dump(mode='json')))
            save(output/'transcript.json',transcript)
            if response.isError:raise ValueError(str(response))
            return response.structuredContent['data']
        before=await call('project_read',dict(project_id=args.project))
        original=json.loads((data/args.project/'pcb_builds'/args.pcb/'drc_report.json').read_text())
        assert original['verdict']=='fail'
        # First call consumes the retained failure. Subsequent calls use the
        # real service. This avoids rerunning an already routed immutable build.
        first=True
        async def resume_call(tool,params):
            nonlocal first
            if first:
                parent=data/args.project/'pcb_builds'/args.pcb
                if tool in ('pcb_verify','pcb_layout_check'):
                    filename=('verify_' if tool=='pcb_verify' else 'layout_')+params.get('stage','routed')+'.json'
                    report=json.loads((parent/filename).read_text())
                    transcript.append(dict(tool=tool,request=params,retained_response_from=str(parent/filename),response=report))
                    save(output/'transcript.json',transcript)
                    return report
                if tool=='pcb_autoroute':
                    raw=json.loads((parent/'router_command.json').read_text())
                    assert raw['exit_code']==0 and (parent/'routed.kicad_pcb').is_file()
                    return dict(retained_router_command=raw,scope='retained immutable parent route')
                if tool=='drc_run':first=False;return original
            return await call(tool,params)
        def persist(attempts):
            history[:]=attempts;save(output/'routing-attempts.json',attempts)
        target,checks,attempts=await route_and_verify(resume_call,dict(project_id=args.project,pcb_build_id=args.pcb),before['revision'],2,persist)
        after=await call('project_read',dict(project_id=args.project))
        assert before['components']==after['components'] and before['nets']==after['nets'] and before['revision']==after['revision']
        assert attempts[0]['status']=='failed' and attempts[-1]['status']=='passed'
        save(output/'verification.json',dict(passed=True,target=target,checks=checks,
             original_failed_build_preserved=True,electrical_graph_unchanged=True,
             physical_verified=False,scope='known retained failure through common incremental recovery'))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source-data',type=Path,required=True)
    p.add_argument('--project',required=True);p.add_argument('--pcb',required=True)
    p.add_argument('--output',type=Path,required=True);asyncio.run(main(p.parse_args()))
