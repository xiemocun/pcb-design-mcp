"""Move existing pins on a delivered board through the installed MCP."""
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
from pcb_mcp_client.plan import resolve

from design_job_modeled_replacement_e2e import initial_design, read


def move(ref, pin, source, destination):
    return dict(pin=dict(ref=ref,number=str(pin)),from_net=source,to_net=destination)


async def exercise(out, runtime, seed):
    out.mkdir(parents=True,exist_ok=False)
    calls=[]
    document,first_plan,contract=initial_design(seed,first_key='original',name='RC board with a divider monitor')
    server=StdioServerParameters(command=sys.executable,
        args=['-m','pcb_mcp_client.cli','--config',str(runtime),'--data-dir',str(out/'data'),'serve'],
        cwd='/private/tmp',env={key:value for key,value in os.environ.items() if key!='PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server,errlog=log) as (rx,tx),ClientSession(rx,tx) as session:
            await session.initialize()

            async def call(tool,request=None,error=None):
                reply=await session.call_tool(tool,{'request':request} if request is not None else {},
                    read_timeout_seconds=timedelta(seconds=345))
                calls.append(dict(tool=tool,request=request,response=reply.model_dump(mode='json')))
                save(out/'transcript.json',calls)
                if error:
                    assert reply.isError and error in str(reply),str(reply)
                    print(tool,error,flush=True)
                    return
                assert not reply.isError,str(reply)
                print(tool,flush=True)
                return reply.structuredContent['data']

            async def attempt(job,plan,key,error=None):
                return await call('design_job_attempt',dict(job_id=job['job_id'],idempotency_key=key,
                    rationale='Move the selected pins while preserving the inherited RC and monitor acceptance.',
                    plan=dict(plan,requirement_sha256=job['requirement_sha256'])),error=error)

            version=(await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            job=await call('design_job_create',dict(name=first_plan['name'],requirement_json=document,contract=contract,max_attempts=1))
            first=await attempt(job,first_plan,'connectivity-first')
            assert first['status']=='passed',first['result']
            base=first['delivery']
            parent=out/'data'/base['project_id']
            retained=snapshot(parent)
            parent_run=Path(first['artifact_directory'])/'run'
            state=read(parent_run/'state.json')
            components={part['ref']:dict(component_id=part['id'],pins=part['pins']) for part in state['components'].values()}
            nets={net['name']:net['id'] for net in state['nets'].values()}
            correct=[move('R1',1,'INPUT','OUTPUT'),move('R1',2,'OUTPUT','INPUT')]
            stale=[correct[0],move('R1',2,'GND','INPUT')]

            async def reject_direct(moves,key,error):
                resolved=[dict(pin=resolve({'$pin':[row['pin']['ref'],row['pin']['number']]},components,{}),
                    from_net_id=nets[row['from_net']],to_net_id=nets[row['to_net']]) for row in moves]
                await call('net_pins_move',dict(project_id=base['project_id'],expected_revision=base['revision'],
                    idempotency_key=key,moves=resolved),error=error)
                assert snapshot(parent)==retained

            await reject_direct(stale,'connectivity-low-level-stale','PIN_MOVE_SOURCE_CHANGED')
            await reject_direct([move('J1',1,'INPUT','OUTPUT'),correct[1]],
                'connectivity-power-boundary','POWER_BOUNDARY_DISCONNECTED')

            requirement=dict(natural_language='Swap which physical R1 pad connects to INPUT and OUTPUT, without '
                'rotating the non-polar resistor. Keep both existing output requirements, other pin connections, '
                'all footprint positions and the board rules.',connectivity_changes=correct)
            request=dict(name='Swap the non-polar resistor pads',base_job=dict(job_id=job['job_id'],attempt=1),max_attempts=4)
            plan=dict(mode='connectivity_revision',name='Swap the two connected R1 pins',base_sha256=base['base_sha256'])
            await call('design_job_create',dict(request,requirement_json=json.dumps(dict(requirement,connectivity_changes=stale))),
                error='PIN_MOVE_SOURCE_CHANGED')
            unbound=await call('design_job_create',dict(request,requirement_json=json.dumps(
                {'natural_language':requirement['natural_language']})))
            await attempt(unbound,dict(plan,moves=correct),'connectivity-missing-scope',
                error='REVISION_CONNECTIVITY_CHANGES_REQUIRED')
            revised_job=await call('design_job_create',dict(request,requirement_json=json.dumps(requirement)))
            extra=correct+[move('R2',1,'OUTPUT','MONITOR'),move('R2',2,'MONITOR','OUTPUT')]
            rejected={}
            for key,moves,pins in [('stale',stale,'R1.2'),('omitted',correct[:1],'R1.2'),('extra',extra,'R2.1, R2.2')]:
                result=await attempt(revised_job,dict(plan,moves=moves),'connectivity-'+key)
                assert result['status']=='failed' and result['result']['project_id'] is None
                assert 'REVISION_CONNECTIVITY_TARGET_MISMATCH: '+pins in result['result']['error']
                rejected[key]=result
            assert len((await call('project_list'))['projects'])==1

            swapped_monitor=[move('J3',1,'MONITOR','GND'),move('J3',2,'GND','MONITOR')]
            monitor_job=await call('design_job_create',dict(request,requirement_json=json.dumps(dict(
                natural_language='Reverse the J3 pads and verify the inherited positive monitor voltage.',
                connectivity_changes=swapped_monitor))))
            failed=await attempt(monitor_job,dict(plan,moves=swapped_monitor),'connectivity-wrong-output')
            assert failed['status']=='failed' and 'schematic_build_id' not in failed['result'],failed['result']
            failed_run=Path(failed['artifact_directory'])/'run'
            bad=read(failed_run/'checks.json')['functional_check']
            assert [row['passed'] for row in bad['measurements']]==[True,False]
            assert snapshot(parent)==retained

            reordered=list(reversed(correct))
            revised=await attempt(revised_job,dict(plan,moves=reordered),'connectivity-correct')
            assert revised['status']=='passed',revised['result']
            assert revised['delivery']['contract_sha256']==base['contract_sha256']
            assert revised['delivery']['placements']==base['placements']
            assert await attempt(revised_job,dict(plan,moves=reordered),'connectivity-correct')==revised
            run=Path(revised['artifact_directory'])/'run'
            checks=read(run/'checks.json')
            assert checks['drc_run']['details']['unconnected_count']==0
            assert [row['passed'] for row in checks['functional_check']['measurements']]==[True,True]
            records=read(run/'journal.json')['calls']
            edit,=[row for row in records if row['tool']=='net_pins_move']
            assert edit['response']['structuredContent']['data']['revision']==1
            assert read(run/'revision.json')['connectivity_changes']==reordered
            assert snapshot(parent)==retained
    save(out/'verification.json',dict(version=version,first=first,revised=revised,rejected=rejected,failed=failed,
        parent_files_unchanged=len(retained),formal_tasks_passed=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('output','runtime','seed'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(exercise(args.output.resolve(),args.runtime.resolve(),args.seed.resolve()))
