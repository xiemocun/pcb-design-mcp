"""Rebuild a delivered source-bound RC board under a revised stackup."""
import argparse
import asyncio
import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp_client.journal import save, snapshot

from design_job_modeled_replacement_e2e import read


async def exercise(out, runtime, seed, construction, *, move_connector=False, expect_unsupported=False):
    out.mkdir(parents=True, exist_ok=False)
    calls=[]
    plan=read(seed/'plan.json')
    requirement=read(seed/'requirement.json')
    requirement['natural_language'] += ' Retain the ground fill used by the two-layer reference design.'
    reference=next(row['arguments']['contract'] for row in read(seed/'journal.json')['calls'] if row['tool']=='functional_check')
    requirement['formal_requirements']=[dict(id=row['id'],allowed=dict(minimum=row['minimum'],maximum=row['maximum'],unit='V'))
                                        for row in reference['requirements']]
    document=json.dumps(requirement)
    plan['requirement_sha256']=hashlib.sha256(document.encode()).hexdigest()
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

            async def attempt(job,candidate,key,error=None):
                return await call('design_job_attempt',dict(job_id=job['job_id'],idempotency_key=key,
                    rationale='Execute the recorded board construction while preserving functional acceptance.',
                    plan=dict(candidate,requirement_sha256=job['requirement_sha256'])),error=error)

            version=(await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            initial=await call('design_job_create',dict(name='Delivered two-layer RC with ground fill',
                requirement_json=document,contract=reference,max_attempts=1))
            first=await attempt(initial,plan,'stackup-first')
            assert first['status']=='passed',first['result']
            base=first['delivery'];parent=out/'data'/base['project_id'];retained=snapshot(parent)
            first_run=Path(first['artifact_directory'])/'run'
            first_state=read(first_run/'state.json')
            previous={key:first_state['board_constraints'][key] for key in ('layers','thickness_mm','stackup')}
            assert previous==dict(layers=2,thickness_mm=1.6,stackup=None)

            async def change(parent_job,text,original,revised,error=None, *, placements=None):
                doc=dict(natural_language=text,stackup_change=dict(previous=original,revised=revised))
                if placements is not None:
                    doc['placement_requirements']=placements
                return await call('design_job_create',dict(name='Rebuild the delivered RC board construction',
                    requirement_json=json.dumps(doc),base_job=dict(job_id=parent_job['job_id'],attempt=1),max_attempts=1),error=error)

            text='Change the board to the declared four-layer development construction. Preserve all nets, placement, ground fill and original electrical acceptance.'
            expected_positions=base['placements']
            targets=None
            placements=[]
            if move_connector:
                old=base['placements']['J2']
                new=dict(old,x_mm=old['x_mm']-6)
                text=(f'Use the declared four-layer construction and move J2 from x={old["x_mm"]} to x={new["x_mm"]} mm. '
                      'Preserve the board outline, all other coordinates, electrical connectivity and original acceptance.')
                targets=[dict(ref='J2',x_mm=new['x_mm'])]
                placements=[dict(ref='J2',reason='The new connector position was requested.',**new)]
                expected_positions={**expected_positions,'J2':new}
            if expect_unsupported:
                await change(initial,text,previous,construction,'PLACEMENT_REQUIREMENTS_PLAN_MISMATCH',placements=targets)
                assert snapshot(parent)==retained and len((await call('project_list'))['projects'])==1
                save(out/'verification.json',dict(version=version,first=first,combined_revision_rejected=True,
                    parent_files_unchanged=len(retained),formal_tasks_passed=0))
                return
            await change(initial,text,dict(previous,thickness_mm=1.5),construction,'STACKUP_REVISION_BASE_CHANGED')
            await change(initial,text,previous,dict(construction,thickness_mm=1.7),'STACKUP_TOTAL_THICKNESS_MISMATCH')
            assert snapshot(parent)==retained and len((await call('project_list'))['projects'])==1
            job=await change(initial,text,previous,construction,placements=targets)
            candidate=dict(name='Rebuild under four layers',mode='stackup_revision',base_sha256=base['base_sha256'])
            await attempt(job,dict(candidate,mode='functional_revision'),'stackup-mismatch','STACKUP_REVISION_PLAN_MISMATCH')
            if move_connector:
                unauthorized=await change(initial,'Only change the layer construction.',previous,construction)
                await attempt(unauthorized,dict(candidate,placements=placements),'stackup-unapproved-move',
                              'STACKUP_REVISION_REQUIRES_PLACEMENT_REQUIREMENTS')
                missing=await attempt(job,candidate,'stackup-missing-move')
                assert missing['status']=='failed' and 'REVISION_PLACEMENT_TARGET_MISMATCH: J2' in json.dumps(missing)
                extra=dict(ref='J1',reason='Unrequested placement for the fault replay.',
                           **dict(base['placements']['J1'],x_mm=base['placements']['J1']['x_mm']+1))
                extra_job=await change(initial,text,previous,construction,placements=targets)
                unexpected=await attempt(extra_job,dict(candidate,placements=[*placements,extra]),'stackup-extra-move')
                assert unexpected['status']=='failed' and 'REVISION_UNREQUESTED_PLACEMENT: J1' in json.dumps(unexpected)
                outside_x=first_state['board_constraints']['width_mm']+1
                outside_job=await change(initial,f'Use four layers and move J2 to x={outside_x} mm.',previous,
                                         construction,placements=[dict(ref='J2',x_mm=outside_x)])
                outside=await attempt(outside_job,dict(candidate,placements=[dict(placements[0],x_mm=outside_x)]),
                                      'stackup-outside-board')
                assert outside['status']=='failed' and 'Placement origin outside board' in json.dumps(outside)
                assert len((await call('project_list'))['projects'])==1
                job=await change(initial,text,previous,construction,placements=targets)
                candidate['placements']=placements
            four=await attempt(job,candidate,'stackup-four')
            assert four['status']=='passed',four['result']
            assert four['delivery']['placements']==expected_positions and four['delivery']['contract_sha256']==base['contract_sha256']
            assert await attempt(job,candidate,'stackup-four')==four
            four_run=Path(four['artifact_directory'])/'run'
            four_state=read(four_run/'state.json')
            if move_connector:
                board=four_state['board_constraints']
                assert {k:v for k,v in board.items() if k!='placements'}=={
                    k:v for k,v in {**first_state['board_constraints'],**construction,
                                   'placement_profile':'explicit'}.items() if k!='placements'}
                assert read(four_run/'revision.json')['moved']['J2']['before']==base['placements']['J2']
                assert read(four_run/'revision.json')['moved']['J2']['after']['x_mm']==expected_positions['J2']['x_mm']
            else:
                assert four_state['board_constraints']=={**first_state['board_constraints'],**construction}
            checks=read(four_run/'checks.json')
            assert checks['pcb_verify']['details']['stackup']['copper_layers']==['F.Cu','In1.Cu','In2.Cu','B.Cu']
            assert checks['drc_run']['details']['unconnected_count']==0 and checks['functional_check']['verdict']=='pass'

            four_parent=out/'data'/four['delivery']['project_id'];four_retained=snapshot(four_parent)
            revert=await change(job,'Restore the original two-layer construction for this variant while keeping the same circuit and acceptance.',construction,previous)
            legacy={key:value for key,value in candidate.items() if key!='placements'}
            two=await attempt(revert,dict(legacy,name='Restore two layers',base_sha256=four['delivery']['base_sha256']),'stackup-two')
            assert two['status']=='passed',two['result']
            assert two['delivery']['placements']==expected_positions and two['delivery']['contract_sha256']==base['contract_sha256']
            two_run=Path(two['artifact_directory'])/'run'
            assert read(two_run/'state.json')['board_constraints']=={**four_state['board_constraints'],**previous}
            for run in (four_run,two_run):
                state=read(run/'state.json')
                assert all(state[key]==first_state[key] for key in ('components','nets','power_sources'))
            checks=read(two_run/'checks.json')
            assert checks['pcb_verify']['details']['stackup']['copper_layers']==['F.Cu','B.Cu']
            assert checks['drc_run']['details']['unconnected_count']==0 and checks['functional_check']['verdict']=='pass'
            assert snapshot(parent)==retained and snapshot(four_parent)==four_retained
    save(out/'verification.json',dict(version=version,first=first,revised=four,restored=two,
        parent_files_unchanged=len(retained),four_layer_files_unchanged=len(four_retained),formal_tasks_passed=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('output','runtime','seed','construction'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--move-connector',action='store_true')
    parser.add_argument('--expect-unsupported',action='store_true')
    args=parser.parse_args()
    if args.expect_unsupported and not args.move_connector:
        parser.error('--expect-unsupported requires --move-connector')
    asyncio.run(exercise(args.output.resolve(),args.runtime.resolve(),args.seed.resolve(),read(args.construction),
                         move_connector=args.move_connector,expect_unsupported=args.expect_unsupported))
