"""Import a real outside-catalog MOSFET and its manufacturer subcircuit."""
import argparse,asyncio,base64,json,os,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp.functional import FunctionalContract
from pcb_mcp.registry import sha,canonical
from pcb_mcp.editing import spec_hash
from generalization_support import Fixture


def asset(path,kind,url,file):
    raw=Path(file).read_bytes();return dict(path=path,kind=kind,source_url=url,sha256=sha(raw),content_base64=base64.b64encode(raw).decode())


async def main(output):
    output.mkdir(parents=True,exist_ok=False)
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(output/'data')})
    async with stdio_client(server) as (a,b),ClientSession(a,b) as session:
        await session.initialize();f=Fixture(session,output)
        symbol=next(x for x in (await f.call('library_search',dict(query='Transistor_FET:2N7002')))['candidates'] if x['library_id']=='Transistor_FET:2N7002');symbol=await f.call('library_get',dict(candidate_id=symbol['candidate_id']))
        fp=next(x for x in (await f.call('footprint_search',dict(query='Package_TO_SOT_SMD:SOT-23')))['candidates'] if x['footprint_id']=='Package_TO_SOT_SMD:SOT-23');fp=await f.call('footprint_get',dict(candidate_id=fp['candidate_id']))
        document=asset('documents/2n7002e.pdf','document','https://www.vishay.com/docs/70860/2n7002e.pdf','work/generalization/2n7002e.pdf')
        def evidence(locator,interpretation):return [dict(asset=document['path'],locator=locator,interpretation=interpretation)]
        package=dict(name='Vishay 2N7002E manufacturer source development',namespace='EXT_VISHAY_DEV',assets=[document],parts=[dict(manufacturer='Vishay Siliconix',mpn='2N7002E-T1-GE3',kind='mosfet',symbol_id=symbol['library_id'],footprint_id=fp['footprint_id'],symbol_definition_hash=symbol['definition_hash'],footprint_definition_hash=fp['definition_hash'],pin_mapping={'1':'G','2':'S','3':'D'},facts=dict(channel='N',absolute_maximum=dict(vds_v=60.,vgs_v=20.,drain_current_a=.240,ambient_c=25.)),evidence=dict(identity=evidence('70860 Rev F, page1 ordering information','Exact2N7002E-T1-GE3 order code for the2N7002E family.'),package=evidence('70860 Rev F, page1 top view','SOT23 three physical terminals; geometric sample assembly untested.'),pin_mapping=evidence('70860 Rev F, page1 pin figure','Physical1 gate,2 source,3 drain.'),channel=evidence('70860 Rev F, page1 title','N-channel enhancement MOSFET.'),absolute_maximum=evidence('70860 Rev F, page1 absolute maximum table','60V drain-source,20V gate-source and240mA at25C are stress limits, not operating guarantees.')))])
        package['description']=package.pop('name');package['name']=package.pop('namespace')
        await f.call('catalog_import',package)
        part=(await f.call('parts_search',dict(query='2N7002E-T1-GE3')))['candidates'][0];part=await f.call('part_get',dict(candidate_id=part['candidate_id']))
        modeldoc=asset('documents/model.pdf','document','https://www.vishay.com/docs/72433/72433.pdf','work/generalization/2n7002e-model.pdf')
        modelasset=asset('models/2n7002e.txt','spice_model','https://www.vishay.com/docs/76769/2n7002e_.txt','work/generalization/2n7002e-model.txt')
        model=dict(name='2N7002E',part_definition_hash=part['definition_hash'],engine='spice_subcircuit',pins=['3','1','2'],excluded_pins={},assets=[document,modeldoc,modelasset],model_asset=modelasset['path'],temperature_range_c=[25.,25.],conditions='Manufacturer nominal SPICE reference at25C, tested here only with0/5V gate and5V supply through10kohm. No manufacturing-corner or physical thermal guarantee.',evidence=[dict(asset=modeldoc['path'],locator='72433 SPICE device model, schematic and subcircuit terminal order',interpretation='Model external order is drain, gate, source; bind to physical3,1,2. Original model data retained unchanged.'),dict(asset=document['path'],locator='70860 Rev F page1 pinout and ordering information',interpretation='Exact family and package identity checked against imported record.')])
        model.update(voltage_limits=[dict(positive='3',negative='2',minimum_v=-.1,maximum_v=6.),dict(positive='1',negative='2',minimum_v=-.1,maximum_v=5.1)],terminal_current_limits_a={'3':.240,'2':.240},power_limit_w=.35)
        (output/'part-package.json').write_text(json.dumps(package,indent=2)+'\n');(output/'model-package.json').write_text(json.dumps(model,indent=2)+'\n')
        imported=await f.call('verification_model_import',model)
        state=await f.create('Manufacturer MOSFET SPICE model development', [('Q1',part['mpn'],None),('R1','CRCW060310K0FKEA','10k'),('J1','61300211121',None),('J2','61300211121',None),('J3','61300211121',None)],
            [('VCC','power',[('J1','1'),('R1','1')]),('GND','ground',[('J1','2'),('J2','2'),('J3','2'),('Q1','2')]),('GATE','signal',[('J2','1'),('Q1','1')]),('DRAIN','signal',[('R1','2'),('Q1','3'),('J3','1')])])
        nets={n['name']:n['id'] for n in state['nets'].values()}
        await f.mutate('power_source_set',net_id=nets['VCC'],ground_net_id=nets['GND'],boundary_pin=f.pin('J1','1'),nominal_v=5.,reason='Explicit fixed5V external supply for nominal manufacturer model reference.')
        await f.mutate('power_envelope_set',positive_pin=f.pin('J1','1'),ground_net_id=nets['GND'],nominal_v=5.,min_v=5.,max_v=5.,available_current_ma=20.,ambient_c=25.,origin='proposed_assumption',reason='Fixed nominal source for these explicit model scenarios, not a continuous source range qualification.')
        await f.mutate('board_constraints_set',width_mm=60.,height_mm=40.,clearance_mm=.15,track_width_mm=.2)
        state=await f.call('project_read',dict(project_id=f.pid))
        scenarios=[];requirements=[]
        for name,gatev,lo,hi in [('off',0.,4.9,5.01),('on',5.,0.,.05)]:
            scenarios.append(dict(id=name,engine='spice_op',drives=[dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),dc_v=5.),dict(positive=dict(ref='J2',number='1'),negative=dict(ref='J2',number='2'),dc_v=gatev)]))
            requirements.append(dict(id=name,description='Drain output must meet the fixed unloaded nominal model voltage target.',positive=dict(ref='J3',number='1'),negative=dict(ref='J3',number='2'),quantity='voltage',minimum=lo,maximum=hi,scenarios=[name]))
        contract=FunctionalContract.model_validate(dict(description='Nominal manufacturer model MOSFET switching reference, exact stated source conditions only.',requirements=requirements,scenarios=scenarios)).model_dump(mode='json')
        args=dict(project_id=f.pid,revision=f.rev,expected_spec_sha256=spec_hash(state),contract=contract,contract_sha256=sha(canonical(contract)),model_bindings={'Q1':imported['model_id']})
        result=await f.call('functional_check',args);assert result['verdict']=='pass',result
        await f.call('functional_check',dict(args,model_bindings={'R1':imported['model_id']}),True)
        checks={}
        for tool in ('spec_validate','electrical_check','bom_check','power_budget_check'):
            check=await f.call(tool,dict(project_id=f.pid,revision=f.rev));checks[tool]=check
            assert not any(x['severity']=='error' for x in check['findings']),check
        assert checks['power_budget_check']['budget_verdict']=='conditional'
        sch=await f.mutate('schematic_build');st=dict(project_id=f.pid,build_id=sch['build_id'])
        for tool in ('schematic_verify','erc_run'):
            checks[tool]=await f.call(tool,st);assert checks[tool]['verdict']=='pass',checks[tool]
        await f.call('schematic_preview',st);await f.call('schematic_export',st)
        pcb=await f.mutate('pcb_build',schematic_build_id=sch['build_id']);pt=dict(project_id=f.pid,pcb_build_id=pcb['pcb_build_id'])
        for tool in ('pcb_verify','pcb_layout_check'):
            checks[tool+'_unrouted']=await f.call(tool,dict(pt,stage='unrouted'));assert checks[tool+'_unrouted']['verdict']=='pass',checks[tool+'_unrouted']
        await f.call('pcb_autoroute',pt)
        for tool in ('pcb_verify','drc_run','pcb_layout_check','pcb_copper_check'):
            checks[tool]=await f.call(tool,pt);key='geometry_verdict' if tool=='pcb_copper_check' else 'verdict';assert checks[tool][key]=='pass',checks[tool]
        await f.call('pcb_preview',pt);checks['review_export']=await f.call('pcb_review_export',pt)
        (output/'verification.json').write_text(json.dumps(dict(passed=True,result=result,checks=checks,model_id=imported['model_id'],new_part=part['mpn'],physical_verified=False,scope='development_source_model_integration_not_holdout'),indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();asyncio.run(main(a.output.resolve()))
