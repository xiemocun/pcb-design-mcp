"""Validate additive circuit revision through the installed MCP and native tools."""
import argparse
import asyncio
import copy
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


def pin(ref, number):
    return dict(ref=ref, number=str(number))


async def main(out, seed, runtime):
    out.mkdir(parents=True, exist_ok=False)
    events = []
    first = next(row['request'] for row in seed if row['tool'] == 'design_job_create')
    first_plan = next(row['request']['plan'] for row in seed if row['tool'] == 'design_job_attempt')
    server = StdioServerParameters(command=sys.executable,
        args=['-m','pcb_mcp_client.cli','--config',str(runtime),'--data-dir',str(out/'data'),'serve'],
        cwd='/private/tmp', env={k:v for k,v in os.environ.items() if k != 'PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server, errlog=log) as (rx,tx), ClientSession(rx,tx) as session:
            await session.initialize()

            async def call(tool, request=None, reject=None):
                response = await session.call_tool(tool, {'request':request} if request is not None else {},
                                                   read_timeout_seconds=timedelta(seconds=345))
                events.append(dict(tool=tool,request=request,response=response.model_dump(mode='json')))
                save(out/'transcript.json',events)
                if reject:
                    assert response.isError and reject in str(response), str(response)
                    print(tool,reject,flush=True)
                    return None
                assert not response.isError, str(response)
                result=response.structuredContent['data']
                print(tool,result.get('status','ok'),flush=True)
                return result

            async def attempt(job, plan, key):
                result=await call('design_job_attempt',dict(job_id=job['job_id'],
                    plan=dict(plan,requirement_sha256=job['requirement_sha256']),idempotency_key=key,
                    rationale='Keep the delivered circuit and verify the requested monitor branch.'))
                return result,Path(result['artifact_directory'])/'run'

            version=(await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            first_job=await call('design_job_create',dict(first,max_attempts=1))
            first_result,first_run=await attempt(first_job,first_plan,'extension-original')
            assert first_result['status']=='passed',first_result
            original=first_result['delivery']
            parent=out/'data'/original['project_id']
            parent_files=snapshot(parent)
            original_state=read(first_run/'state.json')
            original_contract=read(first_run.parents[1]/'contract.json')
            monitor=dict(original_contract['requirements'][0],id='monitor_out',
                description='Observe the added high-impedance monitor port over the inherited pulse window.',
                positive=pin('J3',1),negative=pin('J3',2),minimum=1.50,maximum=1.59)
            document=dict(natural_language='Add an unloaded 2-pin monitor port with a 100 kΩ / 100 kΩ divider '
                'from the original output to ground. In the existing 1.99–2.01 ms window, require 1.50–1.59 V '
                'between J3.1 and J3.2. Preserve the existing output requirement, footprint poses and board rules.',
                functional_additions=[monitor])
            create=dict(name='Add a divided-voltage monitor port',requirement_json=json.dumps(document),
                base_job=dict(job_id=first_job['job_id'],attempt=1),max_attempts=3)
            duplicate=dict(document,functional_additions=[dict(monitor,id=original_contract['requirements'][0]['id'])])
            await call('design_job_create',dict(create,requirement_json=json.dumps(duplicate)),reject='DUPLICATE_FUNCTIONAL_ID')
            job=await call('design_job_create',create)
            inherited=read(out/'data/design_jobs'/job['job_id']/'contract.json')
            assert inherited['requirements'] == [*original_contract['requirements'],monitor]
            assert inherited['scenarios']==original_contract['scenarios']

            mpn='CRCW0603100KFKEA'
            selected,=[row for row in (await call('parts_search',dict(query=mpn)))['candidates'] if row['mpn']==mpn]
            resistor=await call('part_get',dict(candidate_id=selected['candidate_id']))
            assert resistor['facts']['value_ohm']==100000
            component=next(row for row in first_plan['components'] if row['ref']=='J2')
            added=[dict(ref=ref,mpn=mpn,part_definition_hash=resistor['definition_hash'],value='100k') for ref in ('R2','R3')]
            added.append(dict(component,ref='J3'))
            poses=[dict(ref=ref,x_mm=x,y_mm=y,rotation_deg=angle,
                        reason='Keep the added divider ground close to the original capacitor return.')
                   for ref,x,y,angle in (('R2',41,25,0),('R3',37,23,180),('J3',50,32,180))]
            plan=dict(mode='circuit_extension',name='Add monitor divider',base_sha256=original['base_sha256'],
                components=added,placements=poses,
                attachments=[dict(name='OUTPUT',pins=[pin('R2',1)]),dict(name='GND',pins=[pin('R3',2),pin('J3',2)])],
                nets=[dict(name='MONITOR',kind='signal',pins=[pin('R2',2),pin('R3',1),pin('J3',1)])],
                routing_attempt_limit=4,routing_max_passes=40)
            rewiring=copy.deepcopy(plan)
            rewiring['attachments'][0]['pins'].append(pin('J2',1))
            await call('design_job_attempt',dict(job_id=job['job_id'],
                plan=dict(rewiring,requirement_sha256=job['requirement_sha256']),
                rationale='Check that the circuit extension cannot name an inherited pin as a new connection.',
                idempotency_key='extension-old-pin-refused'),reject='EXTENSION_CAN_CONNECT_ONLY_NEW_COMPONENT_PINS')
            missing=copy.deepcopy(plan)
            missing['attachments'][0]['name']='ABSENT'
            rejected,_=await attempt(job,missing,'extension-absent-net')
            assert rejected['status']=='failed' and rejected['result']['project_id'] is None
            assert 'EXTENSION_EXISTING_NET_NOT_FOUND' in str(rejected['diagnosis'])
            assert len((await call('project_list'))['projects'])==1

            wrong=copy.deepcopy(plan)
            wrong['attachments'][1]['pins'][1]=pin('J3',1)
            wrong['nets'][0]['pins'][2]=pin('J3',2)
            failed,failed_run=await attempt(job,wrong,'extension-reversed-port')
            assert failed['status']=='failed' and 'schematic_build_id' not in failed['result']
            failed_report=read(failed_run/'checks.json')['functional_check']
            assert [row['passed'] for row in failed_report['measurements']]==[True,False]
            assert snapshot(parent)==parent_files

            revised,run=await attempt(job,plan,'extension-correct-port')
            assert revised['status']=='passed' and revised['delivery']['contract_sha256']==job['contract_sha256'],revised
            report=read(run/'checks.json')['functional_check']
            assert [row['passed'] for row in report['measurements']]==[True,True]
            assert read(run/'checks.json')['drc_run']['details']['unconnected_count']==0
            state=read(run/'state.json')
            assert all(state['components'][ident]==row for ident,row in original_state['components'].items())
            assert all(set(tuple(sorted(pin.items())) for pin in row['pins']) <=
                       set(tuple(sorted(pin.items())) for pin in state['nets'][ident]['pins'])
                       for ident,row in original_state['nets'].items())
            assert {row['name'] for row in state['nets'].values()}=={row['name'] for row in original_state['nets'].values()}|{'MONITOR'}
            for ref,position in original['placements'].items():
                assert revised['delivery']['placements'][ref]==position
            assert len(state['components'])==7
            assert snapshot(parent)==parent_files
            assert len((await call('project_list'))['projects'])==3
            assert (await call('design_job_create',dict(name='Retain the delivered monitor for later revision',
                base_job=dict(job_id=job['job_id'],attempt=3),requirement_json=json.dumps(
                    dict(natural_language='Preserve the verified monitor and move a connector next.')))))['contract_sha256']==job['contract_sha256']
    save(out/'functional.json',report)
    save(out/'negative-functional.json',failed_report)
    save(out/'verification.json',dict(version=version,original=first_result,revised=revised,failed=failed,
        preflight_rejected=rejected,parent_files_unchanged=len(parent_files),formal_tasks_passed=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-transcript',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(main(args.output.resolve(),read(args.seed_transcript),args.runtime.resolve()))
