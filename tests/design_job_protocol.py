"""Known actual failure repaired by source-selected parts, acceptance unchanged."""
import argparse,asyncio,copy,json,os,sys
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp.functional import FunctionalContract


async def main(output):
    output.mkdir(parents=True,exist_ok=False);transcript=[]
    base=Path('work/generalization/composition-protocol-02');plan=json.loads((base/'plan.json').read_text());requirement=(base/'requirement.json').read_text()
    contract=FunctionalContract.model_validate(dict(description='Frozen unloaded divider output target2.3..2.7V with actual4.75..5.25V source; tolerance and source range cannot be removed to repair the design.',requirements=[dict(id='output_voltage',description='Unloaded output remains between2.3 and2.7V throughout the declared source and resistor intervals.',positive=dict(ref='J2',number='1'),negative=dict(ref='J2',number='2'),quantity='voltage',minimum=2.3,maximum=2.7,scenarios=['corners'])],scenarios=[dict(id='corners',engine='linear_dc',linear_sources=[dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),minimum_v=4.75,nominal_v=5.,maximum_v=5.25)])])).model_dump(mode='json')
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(output/'data')})
    async with stdio_client(server) as (a,b),ClientSession(a,b) as session:
        await session.initialize()
        async def call(name,args,error=False):
            reply=await session.call_tool(name,dict(request=args),read_timeout_seconds=timedelta(seconds=360));transcript.append(dict(tool=name,request=args,response=reply.model_dump(mode='json')))
            (output/'transcript.json').write_text(json.dumps(transcript,indent=2)+'\n');assert bool(reply.isError)==error,str(reply)
            return reply.structuredContent['data'] if not error else None
        job=await call('design_job_create',dict(name='Repair the real divider tolerance failure',requirement_json=requirement,contract=contract,max_attempts=4));jid=job['job_id']
        args=dict(job_id=jid,plan=plan,rationale='Initial composed divider preserved as the failing reference candidate.',idempotency_key='original-candidate')
        failed=await call('design_job_attempt',args);assert failed['status']=='failed',failed
        assert any(f.get('code')=='FUNCTIONAL_REQUIREMENT_FAILED' for f in failed['diagnosis']['findings']),failed
        assert 'schematic_build_id' not in failed['result'],failed
        assert (await call('design_job_attempt',args))==failed
        changed=copy.deepcopy(plan);changed['requirement_sha256']='0'*64
        await call('design_job_attempt',dict(args,plan=changed,idempotency_key='changed-requirement'),True)
        narrowed=copy.deepcopy(plan)
        for setting in narrowed['settings']:
            if setting['tool']=='power_envelope_set':setting['args'].update(min_v=5.,max_v=5.)
        rejected=await call('design_job_attempt',dict(args,plan=narrowed,idempotency_key='illegal-source-narrowing',rationale='Negative test: narrowing source conditions must not repair a frozen range failure.'))
        assert rejected['status']=='failed' and any(f.get('code')=='FROZEN_SOURCE_RANGE_CHANGED' for f in rejected['diagnosis']['findings']),rejected
        # Existing real0.1%/25ppm source record; same100k values, larger real0805 packages.
        part=(await call('parts_search',dict(query='TNPW0805100KBEEA')))['candidates'][0];part=await call('part_get',dict(candidate_id=part['candidate_id']))
        repaired=copy.deepcopy(plan)
        for c in repaired['components']:
            if c['ref'] in ('R1','R2'):c.update(mpn=part['mpn'],part_definition_hash=part['definition_hash'])
        passed=await call('design_job_attempt',dict(args,plan=repaired,idempotency_key='precision-resistor-revision',rationale='Replace both source-bound100k resistors with actual0.1%25ppm0805 parts. Preserve topology, source range, output requirement and every original attempt.'))
        assert passed['status']=='passed',passed
        final=await call('design_job_read',dict(job_id=jid));assert final['status']=='completed_reference_design' and len(final['attempts'])==3
        assert final['requirement_sha256']==job['requirement_sha256'] and final['contract_sha256']==job['contract_sha256']
        (output/'verification.json').write_text(json.dumps(dict(passed=True,job=final,failed_attempt_preserved=True,requirement_unchanged=True,physical_verified=False,scope='development_repair_loop_not_held_out'),indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();asyncio.run(main(a.output.resolve()))
