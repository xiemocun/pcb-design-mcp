"""Move nonplated mounting holes through the installed design job interface."""
import argparse
import asyncio
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pcb_mcp_client.acceptance import delivery_criteria
from pcb_mcp_client.journal import save, snapshot


def read(path):
    return json.loads(path.read_bytes())


async def exercise(out, runtime, seed, before, check_summary, remove_holes=None):
    out.mkdir(parents=True, exist_ok=False)
    events = []
    initial = [dict(x_mm=3.0,y_mm=3.0,diameter_mm=2.2,keepout_mm=1.0),
               dict(x_mm=57.0,y_mm=37.0,diameter_mm=2.2,keepout_mm=1.0)]
    moved = [dict(initial[0],x_mm=5.0),initial[1]]
    fitted = [moved[0],dict(moved[1],x_mm=55.0)]
    plan = read(seed/'plan.json')
    setting, = [row for row in plan['settings'] if row['tool']=='board_constraints_set']
    setting['args']['mounting_holes'] = initial
    requirement = read(seed/'requirement.json')
    requirement['natural_language'] += ' Add two 2.2 mm nonplated mounting holes at (3,3) and (57,37) mm with 1 mm keepout.'
    requirement['software_acceptance'] = [
        *(row.model_dump(mode='json') for row in delivery_criteria()),
        dict(id='near_hole_x',report='pcb_verify',pointer='/details/mounting/holes/0/x_mm',
             operator='equals',expected=3),
        dict(id='far_hole_x',report='pcb_verify',pointer='/details/mounting/holes/1/x_mm',
             operator='equals',expected=57),
        dict(id='minimum_hole_clearance',report='pcb_verify',pointer='/details/mounting/holes/1/keepout_mm',
             operator='minimum',expected=1),
    ]
    contract = next(row['arguments']['contract'] for row in read(seed/'journal.json')['calls']
                    if row['tool']=='functional_check')
    server = StdioServerParameters(command=sys.executable,
        args=['-m','pcb_mcp_client.cli','--config',str(runtime),'--data-dir',str(out/'data'),'serve'],
        cwd='/private/tmp',env={key:value for key,value in os.environ.items() if key!='PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server,errlog=log) as (rx,tx), ClientSession(rx,tx) as session:
            await session.initialize()

            async def call(tool, request=None, error=None):
                reply=await session.call_tool(tool, {'request':request} if request is not None else {},
                                              read_timeout_seconds=timedelta(seconds=345))
                events.append(dict(tool=tool,request=request,response=reply.model_dump(mode='json')))
                save(out/'transcript.json',events)
                print(tool,error or '',flush=True)
                if error is not None:
                    assert reply.isError and error in str(reply),str(reply)
                    return None
                assert not reply.isError,str(reply)
                return reply.structuredContent['data']

            async def attempt(job, candidate, key, error=None):
                return await call('design_job_attempt',dict(job_id=job['job_id'],idempotency_key=key,
                    rationale='Preserve the frozen electrical design and verify the requested mechanical geometry.',
                    plan=dict(candidate,requirement_sha256=job['requirement_sha256'])),error)

            async def revise(parent, old, new, text, *, outline=None, error=None, removed_indices=None):
                change=dict(previous=old,revised=new)
                if removed_indices is not None:
                    change['removed_indices']=removed_indices
                doc=dict(natural_language=text,mounting_hole_change=change)
                if outline:
                    doc['outline_change']=outline
                return await call('design_job_create',dict(name='Revise the PCB mounting pattern',
                    requirement_json=json.dumps(doc),base_job=dict(job_id=parent['job_id'],attempt=1),
                    max_attempts=1),error)

            def verified(attempt_result, holes, width):
                assert attempt_result['status']=='passed',attempt_result['result']
                run=Path(attempt_result['artifact_directory'])/'run'
                checks=read(run/'checks.json')
                actual=checks['pcb_verify']['details'].get('mounting',dict(passed=True,holes=[]))
                assert actual['passed'] and checks['drc_run']['details']['unconnected_count']==0
                assert [{key:row[key] for key in ('x_mm','y_mm','diameter_mm','keepout_mm')}
                        for row in actual['holes']]==holes
                assert checks['pcb_verify']['details']['fabrication']['rectangle_bounds_mm']==[0,0,width,40]
                return run

            version=(await call('system_capabilities'))['contract_version']
            job=await call('design_job_create',dict(name='Deliver two-hole RC board from empty',
                requirement_json=json.dumps(requirement),contract=contract,max_attempts=1))
            first=await attempt(job,plan,'mount-first')
            original=verified(first,initial,60)
            original_dir=out/'data'/first['delivery']['project_id']
            original_files=snapshot(original_dir)
            old=first['delivery']
            text='Move the near mounting hole from x=3 to x=5 mm. Keep other holes, board dimensions, wiring and parts.'
            mode=dict(name='Move one mounting hole',mode='mounting_revision',base_sha256=old['base_sha256'])
            if remove_holes:
                kept=[initial[1]]
                delete=await revise(job,initial,kept,
                    'Remove the near mounting hole at (3,3) mm. Keep the far hole and its copper clearance.')
                one=await attempt(delete,dict(mode,name='Remove the near mounting hole'),'remove-near-hole')
                if remove_holes=='reproduce':
                    assert one['status']=='failed' and 'DECLARED_ACCEPTANCE_INCOMPLETE' in one['result']['error']
                    old_rows=read(Path(one['artifact_directory'])/'run/acceptance.json')['criteria']
                    assert {row['id'] for row in old_rows if row['verdict']=='incomplete'}=={
                        'far_hole_x','minimum_hole_clearance'}
                    assert snapshot(original_dir)==original_files
                    save(out/'verification.json',dict(version=version,first=first,failed=one,
                         acceptance=old_rows,parent_files_unchanged=len(original_files),formal_tasks_passed=0))
                    return
                single=verified(one,kept,60)
                checks={row['id']:row for row in read(single/'acceptance.json')['criteria']}
                assert 'near_hole_x' not in checks and checks['far_hole_x']['expected']==57
                assert checks['far_hole_x']['pointer']=='/details/mounting/holes/0/x_mm'
                assert checks['minimum_hole_clearance']['expected']==1
                assert checks['minimum_hole_clearance']['pointer']=='/details/mounting/holes/0/keepout_mm'
                summary=Path(one['revision_summary']['path']).read_text()
                assert r'移除交付检查 near\_hole\_x' in summary
                assert '安装孔 MH1 · (3, 3) mm' in summary and '(57, 37) mm' not in summary
                await revise(job,initial,[dict(kept[0],x_mm=56)],
                    'Remove the near hole and move the far hole.',error='MOUNTING_REVISION_HOLE_MAPPING_REQUIRED')
                await revise(job,initial,kept,'Remove the near hole.',
                             removed_indices=[0,0],error='MOUNTING_REVISION_INVALID_HOLE_INDICES')
                single_dir=out/'data'/one['delivery']['project_id']
                single_files=snapshot(single_dir)
                delete_last=await revise(delete,kept,[],
                    'Remove the remaining mounting hole. Keep all components and electrical checks.')
                none=await attempt(delete_last,dict(mode,name='Remove the last mounting hole',
                    base_sha256=one['delivery']['base_sha256']),'remove-last-hole')
                empty=verified(none,[],60)
                for result,run in ((one,single),(none,empty)):
                    assert result['delivery']['placements']==old['placements']
                    assert result['delivery']['contract_sha256']==old['contract_sha256']
                    for field in ('nets','components','power_sources'):
                        assert read(run/'state.json')[field]==read(original/'state.json')[field]
                assert {row['id'] for row in read(empty/'acceptance.json')['criteria']}=={
                    row.id for row in delivery_criteria()}
                assert snapshot(original_dir)==original_files and snapshot(single_dir)==single_files
                save(out/'verification.json',dict(version=version,first=first,revised=one,removed_last=none,
                    parent_files_unchanged=len(original_files),revised_parent_files_unchanged=len(single_files),
                    formal_tasks_passed=0))
                return
            if before:
                moving=await revise(job,initial,moved,text)
                await attempt(moving,mode,'mount-unsupported','mounting_revision')
                save(out/'verification.json',dict(version=version,first=first,unsupported=True))
                return
            await revise(job,[dict(initial[0],x_mm=4),initial[1]],moved,text,
                         error='MOUNTING_REVISION_BASE_CHANGED')
            await revise(job,initial,[dict(initial[0],x_mm=59),initial[1]],text,
                         error='MOUNTING_HOLE_OUTSIDE_BOARD')
            moving=await revise(job,initial,moved,text)
            await attempt(moving,dict(mode,mode='functional_revision'),'mount-wrong-mode',
                          'MOUNTING_REVISION_PLAN_MISMATCH')
            revised=await attempt(moving,mode,'mount-move')
            revised_run=verified(revised,moved,60)
            revised_dir=out/'data'/revised['delivery']['project_id']
            revised_files=snapshot(revised_dir)
            fit_job=await revise(moving,moved,fitted,
                'Shrink to 58 by 40 mm and move the far mounting hole to x=55 mm. Keep the near hole and all electronic components.',
                outline=dict(previous=dict(width_mm=60,height_mm=40),revised=dict(width_mm=58,height_mm=40)))
            shrunk=await attempt(fit_job,dict(mode,mode='outline_revision',base_sha256=revised['delivery']['base_sha256']),
                                 'mount-and-outline')
            shrunk_run=verified(shrunk,fitted,58)
            for result,run,holes in ((revised,revised_run,moved),(shrunk,shrunk_run,fitted)):
                assert result['delivery']['placements']==old['placements']
                assert result['delivery']['contract_sha256']==old['contract_sha256']
                for field in ('nets','components','power_sources'):
                    assert read(run/'state.json')[field]==read(original/'state.json')[field]
                criteria={row['id']:row for row in read(run/'acceptance.json')['criteria']}
                assert criteria['near_hole_x']['expected']==holes[0]['x_mm']
                assert criteria['far_hole_x']['expected']==holes[1]['x_mm']
                assert criteria['minimum_hole_clearance']['expected']==1
            assert snapshot(original_dir)==original_files and snapshot(revised_dir)==revised_files
            if check_summary:
                assert 'revision_summary' not in first
                for result, expected in ((revised, ('安装孔 MH1', '(3, 3) mm', '(5, 3) mm')),
                                         (shrunk, ('板框尺寸 · 60 × 40 mm → 58 × 40 mm', '安装孔 MH2',
                                                   '(57, 37) mm', '(55, 37) mm'))):
                    summary=result['revision_summary']; path=Path(summary['path']); content=path.read_bytes()
                    assert sha256(content).hexdigest()==summary['sha256']==result['result']['revision_summary_sha256']
                    assert summary['sha256']==result['delivery']['file_hashes'][path.name]
                    assert all(value in content.decode() for value in expected)
                    assert '## 器件与封装' not in content.decode()
                path=Path(shrunk['revision_summary']['path']); original_summary=path.read_bytes()
                path.write_bytes(original_summary+b'\nchanged for negative test\n')
                try:
                    await revise(fit_job,fitted,[dict(fitted[0],x_mm=6),fitted[1]],text,
                                 error='REVISION_BASE_SUMMARY_CHANGED')
                finally:
                    path.write_bytes(original_summary)
    save(out/'verification.json',dict(version=version,first=first,revised=revised,shrunk=shrunk,
        parent_files_unchanged=len(original_files),revised_parent_files_unchanged=len(revised_files),
        formal_tasks_passed=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('output','runtime','seed'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--before',action='store_true')
    parser.add_argument('--check-summary',action='store_true')
    parser.add_argument('--remove-holes',choices=('reproduce','verify'))
    args=parser.parse_args()
    asyncio.run(exercise(args.output.resolve(),args.runtime.resolve(),args.seed.resolve(),
                         args.before,args.check_summary,args.remove_holes))
