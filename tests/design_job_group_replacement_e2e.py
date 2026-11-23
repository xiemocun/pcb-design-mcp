"""Verify atomic RC component changes through an installed MCP server."""
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


def read(path):
    return json.loads(path.read_bytes())


async def exercise(out, seed, runtime, before):
    out.mkdir(parents=True, exist_ok=False)
    events = []
    plan = read(seed/'plan.json')
    requirement = read(seed/'requirement.json')
    journal = read(seed/'journal.json')
    contract = next(row['arguments']['contract'] for row in journal['calls'] if row['tool']=='functional_check')
    scenario = contract['scenarios'][0]
    scenario['drives'][0]['pulse'][2:] = [0.01, 0.0001, 0.0001, 0.1, 0.2]
    scenario.update(stop_s=0.04, step_s=0.00001)
    contract['requirements'][0]['time_window_s'] = [0.0199, 0.0201]
    requirement['natural_language'] = ('Known RC development board. With 10 kOhm and 1 uF, require 3.00 to 3.30 V '
                                       'at 19.9 to 20.1 ms after start, with a 5 V pulse rising at 10 ms over '
                                       '100 us. Retain the two connectors, declared board rules and source limits.')
    server = StdioServerParameters(command=sys.executable,
        args=['-m','pcb_mcp_client.cli','--config',str(runtime),'--data-dir',str(out/'data'),'serve'],
        cwd='/private/tmp',env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server,errlog=log) as (rx,tx), ClientSession(rx,tx) as session:
            await session.initialize()

            async def call(tool, request=None, error=None):
                reply=await session.call_tool(tool, {'request':request} if request is not None else {},
                                             read_timeout_seconds=timedelta(seconds=340))
                events.append(dict(tool=tool,request=request,response=reply.model_dump(mode='json')))
                save(out/'transcript.json',events)
                print(tool, flush=True)
                if error is not None:
                    assert reply.isError and error in str(reply), str(reply)
                    return None
                assert not reply.isError,str(reply)
                return reply.structuredContent['data']

            async def part(ref, mpn):
                candidates=(await call('parts_search',dict(query=mpn)))['candidates']
                candidate,=[row for row in candidates if row['mpn']==mpn]
                facts=await call('part_get',dict(candidate_id=candidate['candidate_id']))
                return dict(ref=ref,mpn=mpn,part_definition_hash=facts['definition_hash'])

            async def attempt(job, candidate, key, error=None):
                return await call('design_job_attempt',dict(job_id=job['job_id'], idempotency_key=key,
                    rationale='Keep the delivered pulse response, nets, and board configuration.',
                    plan=dict(candidate,requirement_sha256=job['requirement_sha256'])),error)

            version=(await call('system_capabilities'))['contract_version']
            capacitor=await part('C1','C1608X5R1C105K080AA')
            plan['components']=[dict(capacitor,value='1uF') if row['ref']=='C1' else row for row in plan['components']]
            first_job=await call('design_job_create',dict(name='Deliver original 10 ms RC board',
                requirement_json=json.dumps(requirement),contract=contract,max_attempts=1))
            first=await attempt(first_job,plan,'group-original')
            assert first['status']=='passed',first['result']
            original=first['delivery']
            parent=out/'data'/original['project_id']
            old_files=snapshot(parent)
            parent_run=Path(first['artifact_directory'])/'run'
            resistor=await part('R1','CRCW0603100KFKEA')
            capacitor=await part('C1','C1608X7R1H104K080AA')
            job=await call('design_job_create',dict(name='Change RC parts together',max_attempts=4,
                base_job=dict(job_id=first_job['job_id'],attempt=1),
                requirement_json=json.dumps(dict(natural_language='Use 100 kOhm and 100 nF together, '
                    'keep the same output window, connectors, wiring, positions and board rules.'))))
            binding=dict(mode='component_revision',name='Change both RC parts',base_sha256=original['base_sha256'])
            group=dict(binding,replacements=[resistor,capacitor])
            if before:
                await attempt(job,group,'group-unsupported','replacements')
                assert snapshot(parent)==old_files
                save(out/'verification.json',dict(version=version,first=first,group_unsupported=True))
                return

            failed=[]
            for target in (resistor,capacitor):
                result=await attempt(job,dict(binding,replacement=target),'group-single-'+target['ref'])
                assert result['status']=='failed' and 'delivery' not in result,result
                failed.append(result)
            wrong=await part('R1','CRCW060330K1FKEA')
            bad=await attempt(job,dict(binding,replacements=[wrong,capacitor]),'group-wrong')
            assert bad['status']=='failed' and 'delivery' not in bad,bad
            bad_run=Path(bad['artifact_directory'])/'run'
            bad_calls=read(bad_run/'journal.json')['calls']
            assert not any(row['tool']=='component_part_replace' for row in bad_calls)
            bad_evidence=read(bad_run/'revision.json')['replacement_functional_check']
            assert bad_evidence['verdict']=='fail' and bad_evidence['applies_to_current_spec'] is False

            revised=await attempt(job,group,'group-both')
            assert revised['status']=='passed',revised['result']
            assert await attempt(job,group,'group-both')==revised
            assert revised['delivery']['contract_sha256']==original['contract_sha256']
            assert revised['delivery']['placements']==original['placements']
            assert snapshot(parent)==old_files
            run=Path(revised['artifact_directory'])/'run'
            after,prior=read(run/'state.json'),read(parent_run/'state.json')
            for key in ('nets','board_constraints','power_sources'):
                assert after[key]==prior[key]
            actual={row['ref']:after['part_bindings'][cid]['mpn'] for cid,row in after['components'].items()}
            assert actual['R1']==resistor['mpn'] and actual['C1']==capacitor['mpn']
            calls=read(run/'journal.json')['calls']
            commit,=[row for row in calls if row['tool']=='component_part_replace']
            assert len(commit['arguments']['replacements'])==2
            revision=read(run/'revision.json')
            assert revision['replacement_functional_check']['verdict']=='pass'
            assert revision['replacement_functional_check']['applies_to_current_spec'] is False
            assert read(run/'checks.json')['functional_check']['verdict']=='pass'
    save(out/'verification.json',dict(version=version,first=first,revised=revised,solo_failures=failed,
        rejected_group=bad,parent_files_unchanged=len(old_files),formal_tasks_passed=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('output','runtime','seed'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--before',action='store_true')
    args=parser.parse_args()
    asyncio.run(exercise(args.output.resolve(),args.seed.resolve(),args.runtime.resolve(),args.before))
