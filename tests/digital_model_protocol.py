"""Real MCP model import and digital verification; development case, not holdout."""
import argparse,asyncio,base64,json,os,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp.functional import FunctionalContract
from pcb_mcp.registry import sha,canonical
from pcb_mcp.editing import spec_hash


async def main(output):
    output.mkdir(parents=True,exist_ok=False);transcript=[]
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(output/'data')})
    async with stdio_client(server) as (reader,writer),ClientSession(reader,writer) as session:
        await session.initialize()
        async def call(name,request,error=False):
            reply=await session.call_tool(name,dict(request=request));transcript.append(dict(tool=name,request=dict(model_sha256=sha(canonical(request))) if name=='verification_model_import' else request,response=reply.model_dump(mode='json')))
            (output/'transcript.json').write_text(json.dumps(transcript,indent=2)+'\n')
            assert bool(reply.isError)==error,str(reply)
            return reply.structuredContent['data'] if not error else None
        gate=(await call('parts_search',dict(query='SN74LVC1G17DBVR')))['candidates'][0]
        header=(await call('parts_search',dict(query='61300211121')))['candidates'][0]
        gate=await call('part_get',dict(candidate_id=gate['candidate_id']));header=await call('part_get',dict(candidate_id=header['candidate_id']))
        raw=Path('evidence-cache/sn74lvc1g17.pdf').read_bytes()
        model=dict(name='TI_SN74LVC1G17_static_5V5',part_definition_hash=gate['definition_hash'],engine='digital_truth',pins=['2','4','5','3'],excluded_pins={'1':'Manufacturer explicitly specifies physical pin 1 as NC.'},
            assets=[dict(path='documents/sn74lvc1g17.pdf',kind='document',source_url='https://www.ti.com/lit/ds/symlink/sn74lvc1g17.pdf',sha256=sha(raw),content_base64=base64.b64encode(raw).decode())],
            evidence=[dict(asset='documents/sn74lvc1g17.pdf',locator='SCES351Y, pin table, electrical characteristics page 6, truth table',interpretation='At exactly VCC=5.5V, use VT+ max3.33V, VT- min1.88V; output within0.1V of rails at100uA; static unloaded output only. No interpolation or timing qualification.')],
            supply_range_v=[5.5,5.5],temperature_range_c=[-40.,85.],conditions='Exact 5.5V supply, inputs at GND or5.5V; output unloaded except explicitly modeled input leakage; no slow-edge, dynamic or continuous supply envelope claim.',
            truth=dict(inputs=['2'],outputs=['4'],supply=['5','3'],rows={'0':'0','1':'1'},vil_max_v=1.88,vih_min_v=3.33,vol_max_v=.1,voh_min_v=5.4,input_leakage_max_a=5e-6,output_current_max_a=100e-6))
        (output/'model-package.json').write_text(json.dumps(model,indent=2)+'\n')
        imported=await call('verification_model_import',model);assert (await call('verification_model_import',model))['already_imported']
        bad=dict(model,pins=['2','4','5']);await call('verification_model_import',bad,True)
        created=await call('project_create',dict(name='Source linked static buffer development',profile='schematic_experiment'));pid=created['project_id'];rev=created['revision'];components={}
        async def mutate(tool_name,**args):
            nonlocal rev
            result=await call(tool_name,dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**args));rev=result['revision'];return result
        for ref,part in [('U1',gate),('J1',header),('J2',header),('J3',header)]:
            symbols=(await call('library_search',dict(query=part['symbol_id'])))['candidates'];symbol=next(s for s in symbols if s['library_id']==part['symbol_id'])
            symbol=await call('library_get',dict(candidate_id=symbol['candidate_id']))
            component=await mutate('component_add',candidate_id=symbol['candidate_id'],ref=ref,value=symbol['symbol']);components[ref]=component
            fp=next(f for f in (await call('footprint_search',dict(query=part['footprint_id'])))['candidates'] if f['footprint_id']==part['footprint_id'])
            fp=await call('footprint_get',dict(candidate_id=fp['candidate_id']))
            await mutate('footprint_bind',component_id=component['component_id'],candidate_id=fp['candidate_id'],reason='Actual manufacturer package from source-linked record')
            await mutate('part_bind',component_id=component['component_id'],candidate_id=part['candidate_id'])
        def pin(ref,number):
            c=components[ref];return dict(component_id=c['component_id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==number))
        for name,kind,endpoints in [('VCC','power',[('J1','1'),('U1','5')]),('GND','ground',[('J1','2'),('J2','2'),('J3','2'),('U1','3')]),('INPUT','signal',[('J2','1'),('U1','2')]),('OUTPUT','signal',[('U1','4'),('J3','1')])]:
            net=await mutate('net_create',name=name,kind=kind);await mutate('net_members_set',net_id=net['net_id'],pins=[pin(*p) for p in endpoints])
        await mutate('pin_intent_set',pin=pin('U1','1'),intent='no_connect',reason='Manufacturer physical pin1 is NC.')
        state=await call('project_read',dict(project_id=pid));scenarios=[];requirements=[]
        for bit in (0,1):
            ident='input_'+str(bit)
            scenarios.append(dict(id=ident,engine='digital_truth',drives=[dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),dc_v=5.5),dict(positive=dict(ref='J2',number='1'),negative=dict(ref='J2',number='2'),dc_v=5.5*bit)]))
            requirements.append(dict(id='output_'+str(bit),description='Static buffer output must equal the explicit binary input.',positive=dict(ref='J3',number='1'),negative=dict(ref='J3',number='2'),quantity='logic',minimum=float(bit),maximum=float(bit),scenarios=[ident]))
        contract=FunctionalContract.model_validate(dict(description='Exact 5.5V static unloaded noninverting buffer, source table test conditions only.',requirements=requirements,scenarios=scenarios)).model_dump(mode='json')
        args=dict(project_id=pid,revision=rev,expected_spec_sha256=spec_hash(state),contract=contract,contract_sha256=sha(canonical(contract)),model_bindings={'U1':imported['model_id']})
        result=await call('functional_check',args);assert result['verdict']=='pass',result
        wrong=json.loads(json.dumps(contract));wrong['scenarios'][0]['drives'][0]['dc_v']=3.3
        rejected=await call('functional_check',dict(args,contract=wrong,contract_sha256=sha(canonical(wrong))))
        assert rejected['verdict']=='fail' and any('SUPPLY_OUTSIDE_MODEL' in f['code'] for f in rejected['findings']),rejected
        (output/'verification.json').write_text(json.dumps(dict(passed=True,project_id=pid,model_id=imported['model_id'],positive=result,outside_scope_rejected=rejected,physical_verified=False),indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();asyncio.run(main(a.output.resolve()))
