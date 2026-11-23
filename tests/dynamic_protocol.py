"""Actual project nets through the common transient/AC verification endpoint."""
import argparse,asyncio,json,os,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp.functional import FunctionalContract
from pcb_mcp.registry import sha,canonical
from pcb_mcp.editing import spec_hash


async def main(output):
    output.mkdir(parents=True,exist_ok=False);transcript=[]
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(output/'data')})
    async with stdio_client(server) as (a,b),ClientSession(a,b) as session:
        await session.initialize()
        async def call(name,args):
            reply=await session.call_tool(name,dict(request=args));transcript.append(dict(tool=name,request=args,response=reply.model_dump(mode='json')))
            (output/'transcript.json').write_text(json.dumps(transcript,indent=2)+'\n');assert not reply.isError,str(reply);return reply.structuredContent['data']
        created=await call('project_create',dict(name='RC transient and AC development fixture',profile='schematic_experiment'));pid=created['project_id'];rev=created['revision'];components={}
        async def mutate(tool_name,**args):
            nonlocal rev
            result=await call(tool_name,dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**args));rev=result['revision'];return result
        for ref,mpn,value in [('J1','61300211121',None),('J2','61300211121',None),('R1','CRCW060310K0FKEA','10k'),('C1','C1608X7R1H104K080AA','100n')]:
            part=(await call('parts_search',dict(query=mpn)))['candidates'][0];part=await call('part_get',dict(candidate_id=part['candidate_id']))
            symbol=next(x for x in (await call('library_search',dict(query=part['symbol_id'])))['candidates'] if x['library_id']==part['symbol_id'])
            symbol=await call('library_get',dict(candidate_id=symbol['candidate_id']))
            c=await mutate('component_add',candidate_id=symbol['candidate_id'],ref=ref,value=value or symbol['symbol']);components[ref]=c
            fp=next(x for x in (await call('footprint_search',dict(query=part['footprint_id'])))['candidates'] if x['footprint_id']==part['footprint_id'])
            fp=await call('footprint_get',dict(candidate_id=fp['candidate_id']))
            await mutate('footprint_bind',component_id=c['component_id'],candidate_id=fp['candidate_id'],reason='Exact source-selected package for dynamic model fixture')
            await mutate('part_bind',component_id=c['component_id'],candidate_id=part['candidate_id'])
        def pin(ref,n):return dict(component_id=components[ref]['component_id'],pin_id=next(p['pin_id'] for p in components[ref]['pins'] if p['number']==n))
        for name,kind,pins in [('INPUT','signal',[('J1','1'),('R1','1')]),('OUTPUT','signal',[('R1','2'),('C1','1'),('J2','1')]),('GND','ground',[('J1','2'),('J2','2'),('C1','2')])]:
            n=await mutate('net_create',name=name,kind=kind);await mutate('net_members_set',net_id=n['net_id'],pins=[pin(*p) for p in pins])
        state=await call('project_read',dict(project_id=pid));positive=dict(ref='J2',number='1');negative=dict(ref='J2',number='2');source=dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),dc_v=0.)
        contract=FunctionalContract.model_validate(dict(description='Verify the nominal ideal RC reference against explicit step response and small-signal AC requirements; no tolerance, DC bias or physical qualification.',
            requirements=[dict(id='one_tau',description='At1ms after an ideal small step, output must be0.062 to0.064V.',positive=positive,negative=negative,quantity='voltage',minimum=.062,maximum=.064,scenarios=['step'],time_window_s=[.001001,.001001],aggregation='final'),dict(id='lowpass_gain',description='Nominal gain over100 to200Hz must stay within0.62 to0.85.',positive=positive,negative=negative,denominator=dict(ref='J1',number='1'),quantity='gain',minimum=.62,maximum=.85,scenarios=['ac'])],
            scenarios=[dict(id='step',engine='spice_transient',drives=[dict(source,pulse=[0.,.1,.000001,.000000001,.000000001,.01,.02])],step_s=.000001,stop_s=.005),dict(id='ac',engine='spice_ac',drives=[dict(source,ac_v=.1)],frequency_hz=[100.,200.],points=101)])).model_dump(mode='json')
        device=dict(positive=dict(ref='R1',number='1'),negative=dict(ref='R1',number='2'))
        contract['requirements'].extend([
            dict(id='one_tau_resistor_current',description='At one time constant R current is approximately 0.1/e/10000 A, entering pin1.',**device,quantity='current',minimum=3.6e-6,maximum=3.8e-6,scenarios=['step'],time_window_s=[.001001,.001001],aggregation='final'),
            dict(id='one_tau_resistor_power',description='At one time constant total resistor dissipation is approximately (0.1/e)^2/10000 W.',**device,quantity='power',minimum=1.3e-7,maximum=1.4e-7,scenarios=['step'],time_window_s=[.001001,.001001],aggregation='final'),
            dict(id='late_output_rms',description='Time-weighted output RMS over4 to5ms remains between98 and100mV.',positive=positive,negative=negative,quantity='voltage',minimum=.098,maximum=.1,scenarios=['step'],time_window_s=[.004,.005],aggregation='rms'),
            dict(id='capacitor_average_current',description='Average capacitor current over first time constant equals C times voltage rise divided by interval.',positive=dict(ref='C1',number='1'),negative=dict(ref='C1',number='2'),quantity='current',minimum=6.2e-6,maximum=6.4e-6,scenarios=['step'],time_window_s=[.000001,.001001],aggregation='average'),
            dict(id='ac_resistor_current',description='Small-signal current magnitude over100 to200Hz is5.2 to8uA.',**device,quantity='current',minimum=5.2e-6,maximum=8e-6,scenarios=['ac'],aggregation='range')])
        contract['scenarios'].append(dict(id='periodic',engine='spice_transient',drives=[dict(source,pulse=[0.,.1,.000001,.000000001,.000000001,.01,.02])],step_s=.000002,stop_s=.080002))
        contract['requirements'].extend([
            dict(id='output_frequency',description='The settled model output must repeat at50Hz, measured at50mV with at least three complete cycles.',positive=positive,negative=negative,quantity='frequency',threshold=.05,minimum=49.99,maximum=50.01,scenarios=['periodic']),
            dict(id='output_duty',description='Output duty at50mV must remain49.9 to50.1percent over complete model cycles.',positive=positive,negative=negative,quantity='duty_cycle',threshold=.05,minimum=.499,maximum=.501,scenarios=['periodic'])])
        contract=FunctionalContract.model_validate(contract).model_dump(mode='json')
        args=dict(project_id=pid,revision=rev,expected_spec_sha256=spec_hash(state),contract=contract,contract_sha256=sha(canonical(contract)))
        result=await call('functional_check',args);assert result['verdict']=='pass',result
        bad=json.loads(json.dumps(contract));bad['requirements'][0]['maximum']=.061;bad['requirements'][0]['minimum']=.06
        rejected=await call('functional_check',dict(args,contract=bad,contract_sha256=sha(canonical(bad))));assert rejected['verdict']=='fail'
        bad_current=json.loads(json.dumps(contract));current=next(r for r in bad_current['requirements'] if r['id']=='one_tau_resistor_current');current.update(minimum=20e-6,maximum=21e-6)
        current_rejected=await call('functional_check',dict(args,contract=bad_current,contract_sha256=sha(canonical(bad_current))))
        assert current_rejected['verdict']=='fail' and any(r['requirement']=='one_tau_resistor_current' and not r['passed'] for r in current_rejected['measurements'])
        (output/'verification.json').write_text(json.dumps(dict(passed=True,project_id=pid,result=result,wrong_requirement_rejected=rejected,wrong_current_rejected=current_rejected,scope='nominal_ideal_RC_development_not_held_out_or_physical'),indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();asyncio.run(main(a.output.resolve()))
