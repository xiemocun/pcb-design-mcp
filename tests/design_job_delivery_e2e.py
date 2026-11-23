"""Check durable publication of actual two-layer and four-layer deliveries."""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp_client.journal import save, snapshot


def read(path):
    return json.loads(path.read_bytes())


async def main(project, out, runtime, case, timeout, require_delivery, retention=None):
    out.mkdir(parents=True, exist_ok=False)
    dist = project/'work/v1/distribution'
    run = dist/'dev25-qualification'/('combined' if case == 'four-layer' else 'rc/execution')
    document = read(run/'requirement.json')
    plan = read(run/'plan.json')
    functional = next(row['arguments'] for row in read(run/'journal.json')['calls'] if row['tool'] == 'functional_check')
    dimensions = {'voltage': 'V', 'current': 'A', 'frequency': 'Hz', 'power': 'W'}
    document['formal_requirements'] = [
        dict(id=row['id'], allowed=dict(minimum=row['minimum'], maximum=row['maximum'], unit=dimensions[row['quantity']]),
             conditions={'observation_window': dict(minimum=row['time_window_s'][0], maximum=row['time_window_s'][1], unit='s')}
                        if row.get('time_window_s') else {})
        for row in functional['contract']['requirements']]
    if case == 'default-acceptance':
        document.pop('software_acceptance')
    data = out/'data'
    data.mkdir()
    imported = {}
    if case == 'four-layer':
        # Reuse only the data packages already imported and validated by dev25.
        # There are no prior designs or tool candidate caches in the test root.
        for name in ('catalog_packages', 'verification_models'):
            source = dist/'dev25-qualification/combined-data'/name
            shutil.copytree(source, data/name)
            imported[name] = snapshot(data/name)
            assert imported[name] == snapshot(source)
    save(out/'retained-source-packages.json', imported)
    events = []
    retention_evidence = {}
    server = StdioServerParameters(command=sys.executable,
        args=['-m', 'pcb_mcp_client.cli', '--config', str(runtime), '--data-dir', str(data), 'serve'],
        cwd='/private/tmp', env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server, errlog=log) as (rx, tx), ClientSession(rx, tx) as session:
            await session.initialize()

            async def call(tool, request=None, reject=None):
                response = await session.call_tool(tool, {'request': request} if request is not None else {},
                                                   read_timeout_seconds=timedelta(seconds=(timeout or 300)+45))
                events.append(dict(tool=tool, request=request, response=response.model_dump(mode='json')))
                save(out/'transcript.json', events)
                if reject:
                    assert response.isError and reject in str(response), str(response)
                    print(tool, reject, flush=True)
                    return None
                assert not response.isError, str(response)
                result = response.structuredContent['data']
                print(tool, result.get('status', 'ok'), flush=True)
                return result

            version = (await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            create = dict(name='Verify complete PCB delivery', requirement_json=json.dumps(document, ensure_ascii=False),
                          contract=functional['contract'], max_attempts=2 if retention else 1)
            if timeout is not None:
                create['attempt_timeout_s'] = timeout
            job = await call('design_job_create', create)
            began = time.monotonic()
            first_request = dict(job_id=job['job_id'],
                plan=dict(plan, requirement_sha256=job['requirement_sha256']),
                model_bindings=functional['model_bindings'], idempotency_key='verify-complete-delivery',
                rationale='Use the original source-bound development circuit and original functional conditions.')
            result = await call('design_job_attempt', first_request)
            elapsed = time.monotonic() - began
            job = await call('design_job_read', dict(job_id=job['job_id']))
            projects = (await call('project_list'))['projects']
            if require_delivery:
                assert result['status'] == 'passed' and result['delivery']['contract_sha256'] == job['contract_sha256'], result
                assert job['latest_delivery']['project_id'] == projects[0]['project_id']
                evidence = Path(result['artifact_directory'])/'run'
                assert len(read(evidence/'effective-acceptance.json')) == 9
                assert read(evidence/'requirement.json') == document
                revision = dict(name='Revise the verified PCB delivery', max_attempts=1,
                    base_job=dict(job_id=job['job_id'], attempt=1),
                    requirement_json=json.dumps(dict(natural_language='Move one connector on the delivered board.')))
                assert (await call('design_job_create', revision))['base'] == result['delivery']
                if case == 'default-acceptance':
                    # Fault injection confined to the isolated development delivery.
                    bundle = Path(result['delivery']['bundle_path'])
                    original = bundle.read_bytes()
                    from zipfile import ZipFile
                    from io import BytesIO
                    try:
                        with ZipFile(BytesIO(original)) as old, ZipFile(bundle, 'w') as incomplete:
                            for name in old.namelist():
                                if name != 'positions.csv':
                                    incomplete.writestr(name, old.read(name))
                        await call('design_job_create', revision, reject='REVISION_BASE_BUNDLE_MANIFEST_CHANGED')
                    finally:
                        bundle.write_bytes(original)
                    assert bundle.read_bytes() == original
                if retention:
                    base = result['delivery']
                    parent = data/base['project_id']
                    files = snapshot(parent)
                    components = plan['components']
                    stale = dict(first_request['plan'],
                                 components=[dict(components[0],part_definition_hash='0'*64),*components[1:]])
                    failed = await call('design_job_attempt',dict(first_request,plan=stale,
                        idempotency_key='reject-stale-part-candidate',
                        rationale='Replay a stale source binding after the verified first delivery.'))
                    assert failed['status']=='failed' and 'PART_EVIDENCE_CHANGED' in failed['result']['error']
                    current = await call('design_job_read',dict(job_id=job['job_id']))
                    assert [row['status'] for row in current['attempts']]==['passed','failed']
                    expected = 'needs_attention_attempt_budget' if retention=='reproduce' else 'completed_reference_design'
                    assert current['status']==expected and current['latest_delivery']==job['latest_delivery']
                    stored=read(data/'design_jobs'/job['job_id']/'job.json')
                    assert stored['status']==expected
                    assert await call('design_job_attempt',first_request)==result
                    assert (await call('project_list'))['projects']==projects and snapshot(parent)==files
                    retention_evidence=dict(failed_candidate=failed,job_after_failure=current,
                                            parent_files_unchanged=len(files))
                    if retention=='verify':
                        pose=base['placements']['J2']
                        moved=dict(pose,x_mm=pose['x_mm']+2,rotation_deg=int(pose['rotation_deg']))
                        branch=await call('design_job_create',dict(revision,requirement_json=json.dumps(dict(
                            natural_language='Move J2 right by 2 mm and preserve the original circuit and checks.',
                            placement_requirements=[dict(ref='J2',x_mm=moved['x_mm'])]))))
                        changed=await call('design_job_attempt',dict(job_id=branch['job_id'],
                            plan=dict(name='Move J2 after a failed candidate',mode='mechanical_revision',
                                base_sha256=base['base_sha256'],requirement_sha256=branch['requirement_sha256'],
                                placements=[dict(ref='J2',reason='Shift the output connector as requested.',**moved)]),
                            idempotency_key='revise-retained-delivery',
                            rationale='Apply the requested two millimeter shift to the retained delivery.'))
                        assert changed['status']=='passed',changed
                        assert changed['delivery']['placements']==dict(base['placements'],J2=moved)
                        assert changed['delivery']['contract_sha256']==base['contract_sha256']
                        assert snapshot(parent)==files
                        retention_evidence['revision']=changed
    save(out/'verification.json', dict(version=version, case=case, elapsed_s=elapsed, attempt=result,
         job=job, project_count=len(projects), formal_tasks_passed=0,**retention_evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--case', choices=('default-acceptance', 'four-layer'), required=True)
    parser.add_argument('--attempt-timeout', type=int)
    parser.add_argument('--require-delivery', action='store_true')
    parser.add_argument('--retention', choices=('reproduce','verify'))
    args = parser.parse_args()
    if args.retention and (args.case!='default-acceptance' or not args.require_delivery):
        parser.error('--retention requires default-acceptance and --require-delivery')
    asyncio.run(main(args.project.resolve(), args.output.resolve(), args.runtime.resolve(), args.case,
                     args.attempt_timeout, args.require_delivery, args.retention))
