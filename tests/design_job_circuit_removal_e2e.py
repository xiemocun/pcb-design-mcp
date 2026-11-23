"""Remove a delivered optional branch through the installed MCP."""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp_client.journal import save, snapshot

from design_job_modeled_replacement_e2e import initial_design, read


async def exercise(out, runtime, seed):
    out.mkdir(parents=True, exist_ok=False)
    events=[]
    original,first_plan,contract=initial_design(seed,first_key='original',name='RC board with removable monitor')
    server=StdioServerParameters(command=sys.executable,
        args=['-m','pcb_mcp_client.cli','--config',str(runtime),'--data-dir',str(out/'data'),'serve'],
        cwd='/private/tmp',env={key:value for key,value in os.environ.items() if key!='PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server,errlog=log) as (rx,tx),ClientSession(rx,tx) as session:
            await session.initialize()

            async def call(tool,request=None,error=None):
                reply=await session.call_tool(tool,{'request':request} if request is not None else {},
                    read_timeout_seconds=timedelta(seconds=345))
                events.append(dict(tool=tool,request=request,response=reply.model_dump(mode='json')))
                save(out/'transcript.json',events)
                if error:
                    assert reply.isError and error in str(reply),str(reply)
                    print(tool,error,flush=True)
                    return
                assert not reply.isError,str(reply)
                print(tool,flush=True)
                return reply.structuredContent['data']

            async def attempt(job,plan,key,error=None):
                return await call('design_job_attempt',dict(job_id=job['job_id'],idempotency_key=key,
                    rationale='Apply the requested removal and verify the remaining frozen checks.',
                    plan=dict(plan,requirement_sha256=job['requirement_sha256'])),error=error)

            version=(await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            first_job=await call('design_job_create',dict(name=first_plan['name'],requirement_json=original,contract=contract,max_attempts=1))
            first=await attempt(first_job,first_plan,'removal-first')
            assert first['status']=='passed',first['result']
            base=first['delivery']
            parent=out/'data'/base['project_id']
            kept=snapshot(parent)
            original_run=Path(first['artifact_directory'])/'run'
            state=read(original_run/'state.json')

            async def create(text,components,retire_checks,error=None):
                document=dict(natural_language=text,circuit_removal=dict(components=components,retire_checks=retire_checks))
                return await call('design_job_create',dict(name='Remove optional monitor hardware',
                    requirement_json=json.dumps(document),base_job=dict(job_id=first_job['job_id'],attempt=1),max_attempts=1),error)

            removed=['J3','R2','R3']
            request='Remove the optional J3 monitor, both of its divider resistors and the J3 monitor-output check. Keep the original RC output, supply and remaining footprints.'
            await create(request,removed,['monitor_out','rc_step'],'CIRCUIT_REMOVAL_CHECKS_MISMATCH')
            await create(request,removed,[],'CIRCUIT_REMOVAL_CHECKS_MISMATCH')
            await create('Remove the original series resistor.', ['R1'],[],'CIRCUIT_REMOVE_DANGLING_NET')
            assert snapshot(parent)==kept

            plan=dict(mode='circuit_removal',name='Remove selected components',base_sha256=base['base_sha256'])
            wrong=await create('Remove R3 while keeping the original RC output and the monitor voltage.', ['R3'],[])
            failed=await attempt(wrong,plan,'removal-wrong-resistor')
            assert failed['status']=='failed' and 'schematic_build_id' not in failed['result'],failed['result']
            bad=read(Path(failed['artifact_directory'])/'run/checks.json')['functional_check']
            assert [row['passed'] for row in bad['measurements']]==[True,False],bad['measurements']
            assert snapshot(parent)==kept

            job=await create(request,removed,['monitor_out'])
            await attempt(job,dict(plan,mode='functional_revision'),'removal-mode-mismatch','CIRCUIT_REMOVAL_PLAN_MISMATCH')
            revised=await attempt(job,plan,'removal-correct')
            assert revised['status']=='passed',revised['result']
            assert await attempt(job,plan,'removal-correct')==revised
            run=Path(revised['artifact_directory'])/'run'
            expected_poses={ref:pose for ref,pose in base['placements'].items() if ref not in removed}
            assert revised['delivery']['placements']==expected_poses
            child=read(run/'state.json')
            assert set(state['components'])-set(child['components'])=={key for key,part in state['components'].items() if part['ref'] in removed}
            assert {row['name'] for row in child['nets'].values()}=={'INPUT','OUTPUT','GND'}
            effective=read(run.parents[1]/'contract.json')
            assert effective['requirements']==[contract['requirements'][0]] and effective['scenarios']==contract['scenarios']
            assert revised['delivery']['contract_sha256']==job['contract_sha256']
            checks=read(run/'checks.json')
            assert checks['drc_run']['details']['unconnected_count']==0
            assert [row['requirement'] for row in checks['functional_check']['measurements']]==['rc_step']
            assert checks['functional_check']['verdict']=='pass'
            edit,=[row for row in read(run/'journal.json')['calls'] if row['tool']=='circuit_remove']
            assert edit['response']['structuredContent']['data']['revision']==1
            assert snapshot(parent)==kept
    save(out/'verification.json',dict(version=version,first=first,revised=revised,failed=failed,
        parent_files_unchanged=len(kept),formal_tasks_passed=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('output','runtime','seed'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(exercise(args.output.resolve(),args.runtime.resolve(),args.seed.resolve()))
