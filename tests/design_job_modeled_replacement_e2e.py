"""Qualify source-bound part replacement on a newly delivered version of the known RC board."""
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
from pcb_mcp_client.journal import digest, save, snapshot


def read(path):
    return json.loads(path.read_bytes())


def initial_design(seed, *, first_key='first', name='RC board with independent diode-isolated signal'):
    evidence = read(seed/'verification.json')
    first = Path(evidence[first_key]['artifact_directory'])/'run'
    extension_run = Path(evidence['revised']['artifact_directory'])/'run'
    requirement = read(first/'requirement.json')
    addition = read(extension_run/'requirement.json')
    plan, extension = read(first/'plan.json'), read(extension_run/'plan.json')
    contract = read(extension_run.parents[1]/'contract.json')
    requirement['natural_language'] += '\n'+addition['natural_language']
    requirement['formal_requirements'] = [dict(id=r['id'], allowed=dict(minimum=r['minimum'], maximum=r['maximum'],
                                                                    unit='V')) for r in contract['requirements']]
    raw = json.dumps(requirement)
    plan.update(name=name,requirement_sha256=hashlib.sha256(raw.encode()).hexdigest())
    plan['components'].extend(extension['components'])
    nets = {row['name']:row for row in plan['nets']}
    for attachment in extension['attachments']:
        nets[attachment['name']]['pins'].extend(attachment['pins'])
    plan['nets'].extend(extension['nets'])
    placements = next(row['args']['placements'] for row in plan['settings'] if row['tool']=='board_constraints_set')
    for pose in extension['placements']:
        placements.append(dict({key:value for key,value in pose.items() if key!='ref'},component_id={'$component':pose['ref']}))
    return raw, plan, contract


async def exercise(out, runtime, seed, sources, parent_sources, continue_first):
    if continue_first:
        transcript=read(out/'transcript.json')
    else:
        out.mkdir(parents=True,exist_ok=False)
        transcript=[]
    document,plan,contract=initial_design(seed)
    server=StdioServerParameters(command=sys.executable,
        args=['-m','pcb_mcp_client.cli','--config',str(runtime),'--data-dir',str(out/'data'),'serve'],
        cwd='/private/tmp',env={key:value for key,value in os.environ.items() if key!='PYTHONPATH'})
    with (out/'server.stderr').open('a' if continue_first else 'w') as log:
        async with stdio_client(server,errlog=log) as (rx,tx),ClientSession(rx,tx) as session:
            await session.initialize()

            async def call(tool,request=None,package=None):
                response=await session.call_tool(tool,{'request':request} if request is not None else {},
                    read_timeout_seconds=timedelta(seconds=345))
                record=dict(source=str(package),request_sha256=digest(request)) if package else request
                transcript.append(dict(tool=tool,request=record,response=response.model_dump(mode='json')))
                save(out/'transcript.json',transcript)
                assert not response.isError,str(response)
                print(tool,flush=True)
                return response.structuredContent['data']

            async def import_asset(tool,path,**extra):
                return await call(tool,dict(read(path),**extra),package=path)

            async def attempt(job,candidate,key,models=None):
                return await call('design_job_attempt',dict(job_id=job['job_id'],idempotency_key=key,
                    rationale='Preserve the frozen bipolar signal, supply and output requirements.',
                    model_bindings=models or {},plan=dict(candidate,requirement_sha256=job['requirement_sha256'])))

            version=(await call('system_capabilities'))['contract_version']
            projects=(await call('project_list'))['projects']
            if continue_first:
                def retained_result(tool):
                    return next(row['response']['structuredContent']['data'] for row in transcript if row['tool']==tool)
                first,first_job,old=map(retained_result,('design_job_attempt','design_job_create','verification_model_import'))
                assert len(projects)==1 and first_job['requirement_sha256']==plan['requirement_sha256']
            else:
                assert not projects
                await import_asset('catalog_import',parent_sources/'catalog.package.json')
                old=await import_asset('verification_model_import',parent_sources/'diode-model.package.json')
                first_job=await call('design_job_create',dict(name=plan['name'],requirement_json=document,contract=contract,max_attempts=1))
                first=await attempt(first_job,plan,'modeled-replacement-first',{'D1':old['model_id']})
            assert first['status']=='passed',first['result']
            base=first['delivery']
            parent=out/'data'/base['project_id']
            retained=snapshot(parent)

            await import_asset('catalog_import',sources/'catalog.package.json')
            selected,=[p for p in (await call('parts_search',dict(query='1N4148WS')))['candidates'] if p['mpn']=='1N4148WS']
            part=await call('part_get',dict(candidate_id=selected['candidate_id']))
            new=await import_asset('verification_model_import',sources/'model.template.json',part_definition_hash=part['definition_hash'])
            requirement=dict(natural_language='Replace D1 with the Diotec 1N4148WS in SOD-323F and its own vendor model. '
                'Keep the existing connectivity, placements, 5 V power startup, 1 mA source limit and bipolar signal acceptance.')
            job=await call('design_job_create',dict(name='Replace the modeled switching diode',requirement_json=json.dumps(requirement),
                base_job=dict(job_id=first_job['job_id'],attempt=1),max_attempts=3))
            target=dict(ref='D1',mpn=part['mpn'],part_definition_hash=part['definition_hash'])
            candidate=dict(mode='component_revision',name='Diotec diode substitution',base_sha256=base['base_sha256'],replacement=target)
            save(out/'replacement-candidate.json',dict(candidate,requirement_sha256=job['requirement_sha256']))
            for name,replacement,error in (('missing',target,'REVISION_REPLACEMENT_REQUIRES_NEW_MODEL_BINDING'),
                ('wrong',dict(target,model_id=old['model_id']),'FUNCTIONAL_MODEL_PART_MISMATCH')):
                failed=await attempt(job,dict(candidate,replacement=replacement),'modeled-replacement-'+name)
                assert failed['status']=='failed' and failed['result']['project_id'] is None and error in failed['result']['error'],failed
            assert len((await call('project_list'))['projects'])==1
            selected_plan=dict(candidate,replacement=dict(target,model_id=new['model_id']))
            revised=await attempt(job,selected_plan,'modeled-replacement-correct')
            assert revised['status']=='passed',revised['result']
            assert revised['delivery']['contract_sha256']==base['contract_sha256']
            assert revised['delivery']['placements']==base['placements']
            assert await attempt(job,selected_plan,'modeled-replacement-correct')==revised
            assert snapshot(parent)==retained
            run=Path(revised['artifact_directory'])/'run'
            journal=read(run/'journal.json')['calls']
            candidate_check, post_check=[row for row in journal if row['tool']=='functional_check']
            applied,=[row for row in journal if row['tool']=='component_part_replace']
            assert journal.index(candidate_check)<journal.index(applied)<journal.index(post_check)
            for check in (candidate_check,post_check):
                assert check['arguments']['model_bindings']=={'D1':new['model_id']}
                report=check['response']['structuredContent']['data']
                assert report['verdict']=='pass' and len(report['measurements'])==3
            assert applied['arguments']['functional_run_id']==candidate_check['response']['structuredContent']['data']['run_id']
            changes=read(run/'revision.json')
            assert changes['replacement_model_binding']==dict(ref='D1',previous_model_id=old['model_id'],model_id=new['model_id'])
            assert changes['replacement']['after']['part']['mpn']=='1N4148WS'
            assert read(run/'checks.json')['drc_run']['details']['unconnected_count']==0
    save(out/'verification.json',dict(version=version,first=first,revised=revised,old_model=old,new_model=new,
        parent_files_unchanged=len(retained),formal_tasks_passed=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for argument in ('output','runtime','seed','sources','parent-sources'):
        parser.add_argument('--'+argument,type=Path,required=True)
    parser.add_argument('--continue-first',action='store_true')
    args=parser.parse_args()
    asyncio.run(exercise(args.output.resolve(),args.runtime.resolve(),args.seed.resolve(),args.sources.resolve(),
        args.parent_sources.resolve(),args.continue_first))
