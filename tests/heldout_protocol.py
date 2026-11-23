"""Post-freeze acceptance inputs. No changes to the implementation under test."""
import argparse,asyncio,base64,hashlib,json,os,sys
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp.functional import FunctionalContract

ROOT=Path('work/generalization/heldout-01')
def write(path,obj):path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
def pin(ref,n):return dict(ref=ref,number=str(n))
def net(name,kind,*pins):return dict(name=name,kind=kind,pins=[pin(*p) for p in pins])
def drive(ref,v,**kw):return dict(positive=pin(ref,1),negative=pin(ref,2),dc_v=v,**kw)
def requirement(ident,lo,hi,scenario,quantity='voltage',out='J2',**kw):
    return dict(id=ident,description='Frozen held-out '+ident+' acceptance at the stated model conditions.',positive=pin(out,1),negative=pin(out,2),quantity=quantity,minimum=lo,maximum=hi,scenarios=[scenario],**kw)
def settings(voltage):return [
    dict(tool='board_constraints_set',args=dict(width_mm=60.,height_mm=40.,clearance_mm=.15,track_width_mm=.2)),
    dict(tool='power_source_set',args=dict(net_id={'$net':'VCC'},ground_net_id={'$net':'GND'},boundary_pin={'$pin':['J1','1']},nominal_v=voltage,reason='Frozen held-out ideal external source, nominal reference conditions only.')),
    dict(tool='power_envelope_set',args=dict(positive_pin={'$pin':['J1','1']},ground_net_id={'$net':'GND'},nominal_v=voltage,min_v=voltage,max_v=voltage,available_current_ma=50.,ambient_c=25.,origin='proposed_assumption',reason='Frozen held-out fixed source and temperature; no physical or process claim.'))]

def asset(file,url,kind='document'):
    raw=(ROOT/'sources'/file).read_bytes()
    return dict(path=('documents/' if kind=='document' else 'models/')+file,kind=kind,source_url=url,sha256=hashlib.sha256(raw).hexdigest(),content_base64=base64.b64encode(raw).decode())

async def import_part(call,output,mpn,manufacturer,kind,symbol,footprint,pins,facts,document,locator,interpretation):
    sym=next(x for x in (await call('library_search',dict(query=symbol)))['candidates'] if x['library_id']==symbol)
    sym=await call('library_get',dict(candidate_id=sym['candidate_id']))
    fp=next(x for x in (await call('footprint_search',dict(query=footprint)))['candidates'] if x['footprint_id']==footprint)
    fp=await call('footprint_get',dict(candidate_id=fp['candidate_id']))
    evidence=[dict(asset=document['path'],locator=locator,interpretation=interpretation)]
    assets=[document];namespace='EXT_HELDOUT_'+mpn;symbol_hash=sym['definition_hash']
    if any(not x['name'] for x in sym['pins']):
        # Source-selected symbol data adapter only: retain KiCad geometry and
        # numbers, name unnamed terminals from the actual manufacturer's table.
        from pcb_mcp import backend
        from pcb_mcp.sexpr import parse,children,one
        original=next(s for s in backend.catalog([symbol.split(':')[0]]) if s['library_id']==symbol)['definition']
        replacements=[]
        for unit in children(parse(original),'symbol'):
            for terminal in children(unit,'pin'):
                name=children(terminal,'name')[0];number=children(terminal,'number')[0]['v'][1]
                if name['v'][1]=='':
                    fragment=original[name['start']:name['end']].replace('(name ""','(name '+json.dumps(pins[number]),1)
                    replacements.append((name['start'],name['end'],fragment))
        adapted=original
        for start,end,fragment in sorted(replacements,reverse=True):adapted=adapted[:start]+fragment+adapted[end:]
        oldname=symbol.split(':')[1];adapted=adapted.replace(oldname,mpn)
        raw=('(kicad_symbol_lib (version 20241209) (generator "heldout_source_adapter") '+adapted+')').encode()
        assets.append(dict(path='symbols/'+namespace+'.kicad_sym',kind='symbol',source_url='https://gitlab.com/kicad/libraries/kicad-symbols/-/blob/master/'+symbol.split(':')[0]+'.kicad_sym',sha256=hashlib.sha256(raw).hexdigest(),content_base64=base64.b64encode(raw).decode()))
        symbol=namespace+':'+mpn;symbol_hash=hashlib.sha256(adapted.encode()).hexdigest()
        write(output/'symbol-adaptation.json',dict(reason='Native symbol has unnamed inputs/output; add source-confirmed A/B/Y names without geometry or pin-number changes.',original_hash=sym['definition_hash'],adapted_hash=symbol_hash))
    package=dict(name=namespace,description='Post-freeze source-selected held-out part; no implementation branches.',assets=assets,parts=[dict(manufacturer=manufacturer,mpn=mpn,kind=kind,symbol_id=symbol,footprint_id=footprint,symbol_definition_hash=symbol_hash,footprint_definition_hash=fp['definition_hash'],pin_mapping=pins,facts=facts,evidence={k:evidence for k in ['identity','package','pin_mapping',*facts]})])
    write(output/'part-package.json',package);await call('catalog_import',package)
    p=next(x for x in (await call('parts_search',dict(query=mpn)))['candidates'] if x['mpn']==mpn)
    return await call('part_get',dict(candidate_id=p['candidate_id']))

async def digital_candidate(call,component,output,task):
    doc=asset('sn74lvc1g08.pdf','https://www.ti.com/lit/ds/symlink/sn74lvc1g08.pdf')
    p=await import_part(call,output,'SN74LVC1G08DBVR','Texas Instruments','logic_gate','74xGxx:74LVC1G08','Package_TO_SOT_SMD:SOT-23-5',{'1':'A','2':'B','3':'GND','4':'Y','5':'VCC'},dict(power_budget_model='supply_pin_load',supply_pins=['VCC'],supply_min_v=1.65,supply_max_v=5.5),doc,'SCES217AA pin functions, recommended operating conditions, electrical characteristics and DBV ordering table','SN74LVC1G08DBVR is DBV SOT23-5; pins1 A,2 B,3 GND,4 Y,5 VCC. VCC1.65..5.5V. Supply-pin DC load fact identifies VCC; external declared budget is separate.')
    model=dict(name='TI_SN74LVC1G08_static_5V5',part_definition_hash=p['definition_hash'],engine='digital_truth',pins=['1','2','3','4','5'],assets=[doc],evidence=[dict(asset=doc['path'],locator='SCES217AA sections5 pin functions,6.3 operating conditions,6.5 electrical characteristics,8.4 truth table',interpretation='At5.5V: VIH0.7VCC=3.85,VIL0.3VCC=1.65; VOH>=VCC-.1,VOL<=.1 at100uA for-40..85C; input leakage5uA. All4 AND input states.')],supply_range_v=[5.5,5.5],temperature_range_c=[-40.,85.],conditions='Unloaded static AND at exact5.5V; both inputs at0 or5.5V. No timing or transient qualification.',truth=dict(inputs=['1','2'],outputs=['4'],supply=['5','3'],rows={'00':'0','01':'0','10':'0','11':'1'},vil_max_v=1.65,vih_min_v=3.85,vol_max_v=.1,voh_min_v=5.4,input_leakage_max_a=5e-6,output_current_max_a=100e-6))
    write(output/'model-package.json',model);modelid=(await call('verification_model_import',model))['model_id']
    h=await component('J1','61300211121');u=await component('U1',p['mpn']);c=await component('C1','C1608X7R1H104K080AA','100n')
    bounds=lambda lo,hi:dict(minimum=lo,maximum=hi,unit='V')
    ground=lambda pins:dict(name='ground',kind='reference',direction='passive',pins=pins,basis='Shared explicit circuit return')
    def port(name,kind,direction,pins,lo,hi,current):return dict(name=name,kind=kind,direction=direction,pins=pins,reference='ground',basis='Frozen held-out explicit static interface',**({'drive_voltage':bounds(lo,hi),'available_current':dict(value=current,unit='mA')} if direction=='output' else {'accept_voltage':bounds(lo,hi),'required_current':dict(value=current,unit='mA')}))
    modules=[dict(id='supply',function='External fixed5.5V source',components=[h],ports=[port('out','power','output',[pin('J1',1)],5.5,5.5,50.),ground([pin('J1',2)])])]
    for ident in ('input_a','input_b'):modules.append(dict(id=ident,function='External independent binary input',components=[h],ports=[port('out','signal','output',[pin('J1',1)],0.,5.5,1.),ground([pin('J1',2)])]))
    modules.append(dict(id='logic',function='Source-bound two-input AND gate',components=[u,c],nets=[net('VCC','power',('U1',5),('C1',1)),net('GND','ground',('U1',3),('C1',2))],ports=[port('power','power','input',[pin('U1',5)],5.5,5.5,1.),port('a','signal','input',[pin('U1',1)],0.,5.5,.005),port('b','signal','input',[pin('U1',2)],0.,5.5,.005),port('out','signal','output',[pin('U1',4)],0.,5.5,.1),ground([pin('U1',3)])]))
    modules.append(dict(id='measurement',function='Unloaded binary observation',components=[h],ports=[port('in','signal','input',[pin('J1',1)],0.,5.5,0.),ground([pin('J1',2)])]))
    formal=[dict(id='state_'+bits,description='Output AND truth for input '+bits,metric='logic',target=['logic','out'],allowed=dict(minimum=float(v),maximum=float(v),unit='1'),conditions={}) for bits,v in task['truth'].items()]
    proposal=dict(name='Held-out composed two-input interlock',intent=dict(description=task['requirement'],requirements=formal),modules=modules,connections=[dict(ports=[['supply','out'],['logic','power']]),dict(ports=[['input_a','out'],['logic','a']]),dict(ports=[['input_b','out'],['logic','b']]),dict(ports=[['logic','out'],['measurement','in']]),dict(ports=[[m['id'],'ground'] for m in modules])],settings=[])
    # Source settings are already generic placeholders; map them to composed ports.
    for s in settings(5.5):
        args=s['args']
        for k,v in list(args.items()):
            if isinstance(v,dict):
                args[k]={'$port_net':['supply','ground' if v.get('$net')=='GND' else 'out']} if '$net' in v else {'$port_pin':['supply','out']}
        proposal['settings'].append(s)
    proposal['settings'].append(dict(tool='power_budget_set',args=dict(profile='direct_source_dc_v1',loads=[dict(pin={'$module_pin':['logic','U1','5']},max_current_ma=.01)],stages=[],reserves=[dict(net_id={'$port_net':['supply','out']},unmodeled_dc_reserve_ma=0.)],origin='proposed_assumption',reason='Datasheet10uA maximum static ICC, unloaded output and rail inputs; capacitor leakage remains unqualified.')))
    write(output/'proposal.json',proposal);composed=await call('design_compose',proposal);write(output/'composition.json',composed);assert composed['verdict']=='pass',composed
    refs=composed['reference_map'];j=refs['measurement:J1'];ja=refs['input_a:J1'];jb=refs['input_b:J1'];js=refs['supply:J1']
    scenarios=[dict(id='state_'+bits,engine='digital_truth',drives=[drive(js,5.5),drive(ja,5.5*int(bits[0])),drive(jb,5.5*int(bits[1]))]) for bits in task['truth']]
    requirements=[requirement('state_'+bits,float(v),float(v),'state_'+bits,quantity='logic',out=j) for bits,v in task['truth'].items()]
    return composed['plan'],scenarios,requirements,{refs['logic:U1']:modelid}

async def discrete_candidate(call,component,output,task):
    zener=task['id']=='power_shunt_reference'
    filename='1n5226b' if zener else 'cmut3904';mpn='1N5226B' if zener else 'CMUT3904'
    url='https://central.aeminc.com/datasheets/'+('1N5221B-5281B.PDF' if zener else 'CMUT3904-3906.PDF')
    doc=asset(filename+'.pdf',url)
    p=await import_part(call,output,mpn,'Central Semiconductor','zener_diode' if zener else 'bipolar_transistor','Device:D_Zener' if zener else 'Transistor_BJT:Q_NPN_BEC','Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal' if zener else 'Package_TO_SOT_SMD:SOT-523',{'1':'K','2':'A'} if zener else {'1':'B','2':'E','3':'C'},dict(nominal_zener_v=3.3) if zener else dict(polarity='NPN',absolute_maximum=dict(vce_v=40.,collector_current_a=.2,power_w=.25)),doc,'Manufacturer datasheet pages1-2 electrical table and package outline/lead code','DO35 cathode band assigned pad1 K, pad2 A;1N5226B3.3V nominal at20mA,500mW atTL75C.' if zener else 'CMUT3904 NPN SOT523 physical1 Base,2 Emitter,3 Collector;40V VCEO,200mA collector,250mW rated under stated datasheet conditions. No physical thermal qualification.')
    mod=asset(filename+'.lib','https://central.aeminc.com/docs/ENGINEERING/CSM/'+('CSM1603.LIB' if zener else 'CMUT3904.LIB'),'spice_model')
    model=dict(name=mpn,part_definition_hash=p['definition_hash'],engine='spice_subcircuit' if zener else 'spice_device',pins=['2','1'] if zener else ['3','1','2'],assets=[doc,mod],model_asset=mod['path'],temperature_range_c=[25.,25.],conditions='Original manufacturer nominal model unchanged, exact25C and0/5V boundary conditions. Declared conservative reference stress domain; not process or physical thermal qualification.',evidence=[dict(asset=doc['path'],locator='Pages1-2 manufacturer electrical table, ratings and pinout',interpretation='Model terminal mapping follows actual K/A or collector/base/emitter roles. Zener20mA limit is the source test-current operating scope,5.1V reverse limit is the declared source domain;500mW rating assumesTL75C. BJT uses40V collector limit,200mA collector limit;1mA base and201mA emitter are conservative declared operating scope, not separate manufacturer absolute ratings. All source models retained byte-for-byte.')])
    if zener:model.update(voltage_limits=[dict(positive='1',negative='2',minimum_v=-1.2,maximum_v=5.1)],terminal_current_limits_a={'1':.02,'2':.02},power_limit_w=.5)
    else:model.update(device='Q',voltage_limits=[dict(positive='3',negative='2',minimum_v=-.1,maximum_v=40.),dict(positive='1',negative='2',minimum_v=-6.,maximum_v=1.)],terminal_current_limits_a={'3':.2,'1':.001,'2':.201},power_limit_w=.25)
    write(output/'model-package.json',model);mid=(await call('verification_model_import',model))['model_id']
    components=[await component('J1','61300211121'),await component('J2','61300211121'),await component('R1','CRCW0603100RFKEA' if zener else 'CRCW060310K0FKEA','100' if zener else '10k'),await component('R2','CRCW060310K0FKEA','10k'),await component('D1' if zener else 'Q1',mpn)]
    if zener:
        nets=[net('VCC','power',('J1',1),('R1',1)),net('OUTPUT','signal',('R1',2),('R2',1),('D1',1),('J2',1)),net('GND','ground',('J1',2),('J2',2),('R2',2),('D1',2))]
        scenarios=[dict(id='loaded_reference',engine='spice_op',drives=[drive('J1',5.)])]
        requirements=[requirement('loaded_output',task['output_min_v'],task['output_max_v'],'loaded_reference')]
    else:
        components.append(await component('J3','61300211121'))
        nets=[net('VCC','power',('J1',1),('R1',1)),net('OUTPUT','signal',('R1',2),('Q1',3),('J2',1)),net('INPUT','signal',('J3',1),('R2',1)),net('BASE','signal',('R2',2),('Q1',1)),net('GND','ground',('J1',2),('J2',2),('J3',2),('Q1',2))]
        scenarios=[dict(id=name,engine='spice_op',drives=[drive('J1',5.),drive('J3',v)]) for name,v in [('off',0.),('on',5.)]]
        requirements=[requirement(name+'_output',task[name+'_min_v'],task[name+'_max_v'],name) for name in ('off','on')]
    return dict(version=1,name='Held-out '+task['id'],components=components,nets=nets,no_connects=[],settings=settings(5.)),scenarios,requirements,{'D1' if zener else 'Q1':mid}

async def main(case,output):
    frozen=json.loads(Path('work/generalization/freeze-01.json').read_text())
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen.items()),'CORE_CHANGED'
    task=next(c for c in json.loads((ROOT/'requirements.json').read_text())['cases'] if c['id']==case)
    output.mkdir(parents=True,exist_ok=False);transcript=[]
    config=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(output/'data')})
    async with stdio_client(config) as (a,b),ClientSession(a,b) as session:
        await session.initialize()
        async def call(name,args):
            result=await session.call_tool(name,dict(request=args),read_timeout_seconds=timedelta(seconds=360))
            logged=dict(package_sha256=hashlib.sha256(json.dumps(args,sort_keys=True).encode()).hexdigest()) if name in ('catalog_import','verification_model_import') else args
            transcript.append(dict(tool=name,request=logged,response=result.model_dump(mode='json')));write(output/'transcript.json',transcript)
            assert not result.isError,str(result)
            return result.structuredContent['data']
        async def component(ref,mpn,value=None):
            found=next(p for p in (await call('parts_search',dict(query=mpn)))['candidates'] if p['mpn']==mpn)
            p=await call('part_get',dict(candidate_id=found['candidate_id']))
            return dict(ref=ref,mpn=mpn,value=value,part_definition_hash=p['definition_hash'])
        bindings={}
        if case=='passive_bandpass':
            components=[await component('J1','61300211121'),await component('J2','61300211121')]
            components += [await component(ref,'CRCW060310K0FKEA','10k') for ref in ('R1','R2')]
            components += [await component(ref,'C1608X7R1H104K080AA','100n') for ref in ('C1','C2')]
            nets=[net('VCC','power',('J1',1),('C1',1)),net('MID','signal',('C1',2),('R1',1),('R2',1)),net('OUT','signal',('R2',2),('C2',1),('J2',1)),net('GND','ground',('J1',2),('J2',2),('R1',2),('C2',2))]
            scenarios=[dict(id='dc',engine='spice_op',drives=[drive('J1',.1)])]
            requirements=[requirement('dc_output',task['dc_min_v'],task['dc_max_v'],'dc')]
            for i,(lo,hi,glo,ghi) in enumerate(task['bands']):
                ident='band_'+str(i);scenarios.append(dict(id=ident,engine='spice_ac',drives=[drive('J1',.1,ac_v=.1)],frequency_hz=[lo,hi],points=201))
                requirements.append(requirement(ident,glo,ghi,ident,quantity='gain',denominator=pin('J1',1)))
            plan=dict(version=1,name='Held-out passive bandpass',components=components,nets=nets,no_connects=[],settings=settings(.1))
        elif case=='two_input_interlock':plan,scenarios,requirements,bindings=await digital_candidate(call,component,output,task)
        elif case in ('power_shunt_reference','bipolar_low_side_switch'):plan,scenarios,requirements,bindings=await discrete_candidate(call,component,output,task)
        else:raise ValueError('CASE_INPUT_NOT_PREPARED')
        contract=FunctionalContract.model_validate(dict(description=task['requirement'],requirements=requirements,scenarios=scenarios)).model_dump(mode='json')
        formal=[dict(id=r['id'],description=r['description'],allowed=dict(minimum=r['minimum'],maximum=r['maximum'],unit='V' if r['quantity']=='voltage' else '1'),conditions={}) for r in requirements]
        raw=json.dumps(dict(natural_language=task['requirement'],formal_requirements=formal),indent=2)+'\n'
        plan['requirement_sha256']=hashlib.sha256(raw.encode()).hexdigest()
        write(output/'plan.json',plan);write(output/'contract.json',contract);(output/'requirement.json').write_text(raw)
        job=await call('design_job_create',dict(name=case,requirement_json=raw,contract=contract,max_attempts=6));write(output/'job.json',job)
        attempt=await call('design_job_attempt',dict(job_id=job['job_id'],plan=plan,model_bindings=bindings,idempotency_key='heldout-initial-candidate',rationale='Source-selected candidate for the post-freeze task; implementation and acceptance unchanged.'))
        final=await call('design_job_read',dict(job_id=job['job_id']))
        write(output/'verification.json',dict(passed=attempt['status']=='passed',case=case,attempt=attempt,job=final,physical_verified=False,core_freeze_unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen.items())))
        print(attempt['status'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--case',required=True);p.add_argument('--output',required=True,type=Path);a=p.parse_args();asyncio.run(main(a.case,a.output.resolve()))
