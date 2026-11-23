"""Real MCP prototype and semantic faults; no hardware/USB approval implied."""
import asyncio,json,os,shutil,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v09'

async def main():
    old=json.loads((OLD/'acceptance-power-module/report.json').read_text())
    out=R/'acceptance-usb-interlock';out.mkdir(exist_ok=True);data=out/('run-'+uuid.uuid4().hex)
    shutil.copytree(R/'data',data);original=(OLD/'data'/old['project_id']/'state.json').read_bytes();calls=[];checks=[]
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})) as (r,w):
      async with ClientSession(r,w) as session:
        await session.initialize();listed=await session.list_tools();assert len(listed.tools)==62
        (R/'tool-contract.json').write_text(json.dumps(listed.model_dump(mode='json'),ensure_ascii=False,indent=2))
        async def call(name,args=None,error=None):
            result=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=result.model_dump(mode='json')))
            (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
            if error:
                assert result.isError and error in '\n'.join(c.text for c in result.content if hasattr(c,'text')),(name,error,result)
                checks.append(error);return
            assert not result.isError,(name,result);return result.structuredContent['data']
        assert (await call('system_capabilities'))['contract_version']=='0.10.0'
        fork=await call('project_fork',dict(project_id=old['project_id'],expected_revision=old['revision'],name='ESP32 USB-C 稳态硬件互锁原型',idempotency_key=uuid.uuid4().hex))
        pid=fork['project_id'];rev=0;s=await call('project_read',dict(project_id=pid));comps={c['ref']:c for c in s['components'].values()};nets={n['name']:n for n in s['nets'].values()}
        def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
        def pin(ref,name=None,number=None):
            c=comps[ref];p=next(p for p in c['pins'] if (p['name']==name if name is not None else p['number']==number))
            return dict(component_id=c['id'],pin_id=p['pin_id'])
        # Replace the external Rd pair with the source-checked controller's Rd.
        removed={comps[ref]['id'] for ref in ['R1','R2']}
        for n in nets.values():
            if any(p['component_id'] in removed for p in n['pins']):
                n['pins']=[p for p in n['pins'] if p['component_id'] not in removed]
                rev=(await call('net_members_set',m(net_id=n['id'],pins=n['pins'])))['revision']
        for ref in ['R1','R2']:
            rev=(await call('footprint_unbind',m(component_id=comps[ref]['id'])))['revision']
            rev=(await call('component_remove',m(component_id=comps[ref]['id'])))['revision'];del comps[ref]
        source_parts={}
        for ref,mpn in [('U5','TUSB320LAIRWBR'),('U6','SN74LVC1G14DBVR'),('U7','TPS2553DBVR'),('U8','TLV75533PDBVR')]:
            found=await call('parts_search',dict(query=mpn));p=await call('part_get',dict(candidate_id=found['candidates'][0]['candidate_id']));assert p['evidence_status']=='available';source_parts[ref]=p
        definitions=[(ref,p['symbol_id'],p['symbol_id'].split(':')[1],p['footprint_id']) for ref,p in source_parts.items()]
        definitions += [(ref,'Device:R',value,'Resistor_SMD:R_0603_1608Metric') for ref,value in [('R8','887k'),('R9','10k'),('R10','100k'),('R11','30.1k')]]
        definitions += [(ref,'Device:C',value,'Capacitor_SMD:C_0603_1608Metric') for ref,value in [('C10','100nF'),('C11','100nF'),('C12','1uF'),('C13','1uF')]]
        for ref,lib,value,footprint in definitions:
            found=await call('library_search',dict(query=lib));c=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
            added=await call('component_add',m(candidate_id=c['candidate_id'],ref=ref,value=value));rev=added['revision'];comps[ref]={**c,'id':added['component_id'],'pins':added['pins']}
            found=await call('footprint_search',dict(query=footprint));fp=await call('footprint_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
            rev=(await call('footprint_bind',m(component_id=added['component_id'],candidate_id=fp['candidate_id'],reason='Manufacturer pin map and real-library geometry prototype; full MPN BOM, tolerances, startup and physical USB qualification incomplete')))['revision']
        old_vbus=pin('J1','VBUS')
        vbus_pins=[dict(component_id=comps['J1']['id'],pin_id=p['pin_id']) for p in comps['J1']['pins'] if p['name']=='VBUS']
        nets['VUSB']['pins']=[p for p in nets['VUSB']['pins'] if p not in vbus_pins]+[pin('U7','OUT')]
        rev=(await call('net_members_set',m(net_id=nets['VUSB']['id'],pins=nets['VUSB']['pins'])))['revision']
        for name,kind,pins in [
            ('VBUS_RAW','power',vbus_pins+[pin('U7','IN'),pin('U8','IN'),pin('U8','EN'),pin('C12',number='1'),pin('R8',number='1')]),
            ('VCC_AUX','power',[pin('U8','OUT'),pin('U5','VDD'),pin('U6','VCC'),pin('R9',number='1'),pin('C10',number='1'),pin('C11',number='1'),pin('C13',number='1')]),
            ('CC_CURRENT_N','signal',[pin('U5','OUT1'),pin('U6','A'),pin('R9',number='2')]),
            ('USB_LOAD_EN','signal',[pin('U6','Y'),pin('U7','EN'),pin('R10',number='1')]),
            ('USB_ILIM','signal',[pin('U7','ILIM'),pin('R11',number='1')]),
            ('VBUS_SENSE','signal',[pin('U5','VBUS_DET'),pin('R8',number='2')])]:
            n=await call('net_create',m(name=name,kind=kind));rev=n['revision'];nets[name]=dict(id=n['net_id'],name=name,pins=pins)
            rev=(await call('net_members_set',m(net_id=n['net_id'],pins=pins)))['revision']
        nets['CC1']['pins']+=[pin('U5','CC1')];nets['CC2']['pins']+=[pin('U5','CC2')]
        nets['GND']['pins'] += [pin('U5',n) for n in ['PORT','EN_N','GND']]+[pin(ref,'GND') for ref in ['U6','U7','U8']]+[pin(ref,number='2') for ref in ['R10','R11','C10','C11','C12','C13']]
        for name in ['CC1','CC2','GND']:rev=(await call('net_members_set',m(net_id=nets[name]['id'],pins=nets[name]['pins'])))['revision']
        for ref,names in [('U5',['ADDR','OUT2','OUT3','ID']),('U6',['NC']),('U7',['FAULT_N']),('U8',['NC'])]:
            for name in names:rev=(await call('pin_intent_set',m(pin=pin(ref,name),intent='no_connect',reason='Source-specific GPIO sink reference; intentionally unused or mode-setting NC')))['revision']
        source=next(iter(s['power_sources'].values()))
        rev=(await call('power_source_set',m(source_id=source['id'],net_id=nets['VBUS_RAW']['id'],ground_net_id=nets['GND']['id'],boundary_pin=old_vbus,nominal_v=5.0,reason='USB connector raw entry; capability still unmeasured and not a manufacturing waiver')))['revision']
        board=dict(s['board_constraints']);board.update(width_mm=130.0,height_mm=100.0,clearance_mm=.15,track_width_mm=.15)
        rev=(await call('board_constraints_set',m(**board)))['revision']
        req=s['requirements']+[dict(key='usb_interlock',value='GPIO sink: default/unattached main power off, advertised 1.5A or 3A main power on under stable auxiliary supply; startup and real USB qualification incomplete',origin='proposed_assumption')]
        rev=(await call('requirements_set',m(items=req)))['revision']
        validation=await call('spec_validate',dict(project_id=pid));assert validation['verdict']=='pass',validation
        contract=await call('usb_c_gate_scenarios',dict(project_id=pid));assert contract['verdict']=='pass' and not contract['live_cc_measurement'] and not contract['manufacturing_approved']
        assert [x['predicted_main_enabled'] for x in contract['scenarios']]==[False,False,True,True]
        assert .78<contract['current_limit']['min_a']<.8 and .94<contract['current_limit']['max_a']<.95
        assert not contract['current_limit']['physical_resistor_tolerance_verified']
        async def wrong_net(code,p,from_name,to_name):
            nonlocal rev
            rev=(await call('net_members_set',m(net_id=nets[from_name]['id'],pins=[x for x in nets[from_name]['pins'] if x!=p])))['revision']
            rev=(await call('net_members_set',m(net_id=nets[to_name]['id'],pins=nets[to_name]['pins']+[p])))['revision']
            bad=await call('spec_validate',dict(project_id=pid));assert code in {f['code'] for f in bad['findings']},bad
            assert (await call('usb_c_gate_scenarios',dict(project_id=pid)))['verdict']=='fail'
            await call('schematic_build',m(),'SPEC_INVALID')
            rev=(await call('net_members_set',m(net_id=nets[to_name]['id'],pins=nets[to_name]['pins'])))['revision']
            rev=(await call('net_members_set',m(net_id=nets[from_name]['id'],pins=nets[from_name]['pins'])))['revision'];checks.append(code)
        await wrong_net('USB_GATE_BOOTSTRAP_SUPPLY',pin('U5','VDD'),'VCC_AUX','V3V3')
        await wrong_net('USB_GATE_BOOTSTRAP_SUPPLY',pin('U3','OUT'),'V3V3','VCC_AUX')
        await wrong_net('USB_GATE_POWER_PATH',pin('U1','VDD'),'V3V3','VCC_AUX')
        await wrong_net('USB_GATE_POWER_PATH',pin('U3','IN'),'VUSB','VBUS_RAW')
        await wrong_net('USB_GATE_CURRENT_SIGNAL',pin('U6','A'),'CC_CURRENT_N','USB_LOAD_EN')
        rev=(await call('component_parameters_set',m(component_id=comps['R11']['id'],value='20k')))['revision']
        bad=await call('spec_validate',dict(project_id=pid));assert 'USB_GATE_CURRENT_LIMIT' in {f['code'] for f in bad['findings']}
        await call('schematic_build',m(),'SPEC_INVALID')
        rev=(await call('component_parameters_set',m(component_id=comps['R11']['id'],value='30.1k')))['revision']
        rev=(await call('pin_intent_set',m(pin=pin('U5','ADDR'),intent='unspecified')))['revision']
        rev=(await call('net_members_set',m(net_id=nets['GND']['id'],pins=nets['GND']['pins']+[pin('U5','ADDR')])))['revision']
        bad=await call('spec_validate',dict(project_id=pid));assert 'USB_GATE_GPIO_MODE' in {f['code'] for f in bad['findings']}
        await call('schematic_build',m(),'SPEC_INVALID')
        rev=(await call('net_members_set',m(net_id=nets['GND']['id'],pins=nets['GND']['pins'])))['revision']
        rev=(await call('pin_intent_set',m(pin=pin('U5','ADDR'),intent='no_connect',reason='Leave ADDR floating to select GPIO mode')))['revision']
        await call('usb_c_gate_scenarios',dict(project_id=pid,approved=True),'extra_forbidden')
        b=await call('schematic_build',m());sch=dict(project_id=pid,build_id=b['build_id'])
        assert (await call('schematic_verify',sch))['verdict']=='pass'
        erc=await call('erc_run',sch);assert erc['verdict']=='pass',erc
        await call('schematic_preview',sch);sch_export=await call('schematic_export',sch)
        traces={}
        for ref,name,expected in [('J1','VBUS','VBUS_RAW'),('U7','OUT','VUSB'),('U5','VDD','VCC_AUX'),('U7','EN','USB_LOAD_EN')]:
            trace=await call('schematic_pin_connections',{**sch,'pin':pin(ref,name)});assert trace['net']['name']==expected;traces[expected]=trace
        pcb=await call('pcb_build',m(schematic_build_id=b['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
        assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass'
        layout=await call('pcb_layout_check',{**pt,'stage':'unrouted'});assert layout['verdict']=='pass',layout
        await call('pcb_autoroute',{**pt,'max_passes':60})
        assert (await call('pcb_verify',pt))['verdict']=='pass'
        layout=await call('pcb_layout_check',pt);assert layout['verdict']=='pass',layout
        drc=await call('drc_run',pt);assert drc['verdict']=='pass',drc
        preview=await call('pcb_preview',pt);pcb_export=await call('pcb_review_export',pt)
        usb=await call('usb_c_power_check',dict(project_id=pid));assert usb['verdict']=='incomplete'
        assert abs(usb['ports'][0]['nominal_direct_capacitance_uf']-1.0)<.001
        assert not {'USB_CC_CURRENT_NOT_DETECTED','USB_DIRECT_CAP_NOMINAL_EXCESS','USB_ATTACH_LOAD_POLICY_MISSING'} & {f['code'] for f in usb['findings']}
        diag=await call('project_fork',dict(project_id=pid,expected_revision=rev,name='互锁条件化电压诊断，非制造批准',idempotency_key=uuid.uuid4().hex));did=diag['project_id'];drev=0
        def env(min_v=4.75):return dict(project_id=did,expected_revision=drev,idempotency_key=uuid.uuid4().hex,positive_pin=old_vbus,ground_net_id=nets['GND']['id'],nominal_v=5.0,min_v=min_v,max_v=5.25,available_current_ma=1500.0,ambient_c=25.0,origin='proposed_assumption',reason='Conditional board-entry voltage and advertised-current assumption; no real USB measurement or startup qualification')
        drev=(await call('power_envelope_set',env()))['revision']
        power=await call('power_integrity_check',dict(project_id=did));assert power['verdict']=='fail'
        assert not {'BUCK_INPUT_RANGE','USB_CONTROL_SUPPLY_RANGE','REGULATOR_CURRENT_CAPACITY'} & {f['code'] for f in power['findings']},power
        assert power['rails'][nets['VUSB']['id']]['min_v']>4.5
        assert (await call('module_constraints_check',dict(project_id=did)))['verdict']=='pass'
        drev=(await call('power_envelope_set',env(4.6)))['revision']
        bad=await call('power_integrity_check',dict(project_id=did));assert 'BUCK_INPUT_RANGE' in {f['code'] for f in bad['findings']}
        await call('schematic_build',dict(project_id=did,expected_revision=drev,idempotency_key=uuid.uuid4().hex),'SPEC_INVALID')
        drev=(await call('power_envelope_set',env()))['revision']
        await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED')
        await call('bom_export',dict(project_id=pid),'BOM_PART_MISSING')
        audit=await call('design_audit',pt);assert audit['status']=='blocked'
        assert next(x for x in audit['stages'] if x['check_id']=='USB_HARDWARE_INTERLOCK')['status']=='pass'
        final=await call('project_read',dict(project_id=pid));assert len(final['components'])==32 and len(final['nets'])==19
    assert (OLD/'data'/old['project_id']/'state.json').read_bytes()==original
    demo=R/'demo-usb-interlock';demo.mkdir(exist_ok=True)
    for folder,names in [(Path(pcb_export['bundle_path']).parent,['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','drc_report.json','layout_routed.json','board_routed.json','pcb_review_bundle.zip','fp-lib-table']),(Path(sch_export['schematic_path']).parent,['led.kicad_sch','actual.xml','erc.json','verify.json','spec.json','review_bundle.zip'])]:
        for name in names:shutil.copy2(folder/name,demo/name)
    shutil.copy2(preview['preview_path'],demo/'pcb.svg');shutil.copy2(Path(sch_export['schematic_path']).parent/'preview/led.svg',demo/'schematic.svg')
    shutil.copy2(audit['report_path'],demo/'检查报告.md')
    for name,value in [('usb_power.json',usb),('interlock.json',contract),('power_conditional.json',power),('actual_traces.json',traces),('audit.json',audit)]: (demo/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    report=dict(passed=True,call_count=len(calls),tool_count=62,project_id=pid,revision=rev,schematic_build_id=b['build_id'],pcb_build_id=pcb['pcb_build_id'],interval_project_id=did,interval_revision=drev,data_root=str(data),usb=usb,interlock=contract,power=power,audit=audit,checks=checks,original_preserved=True,component_count=32,net_count=19,whole_board_manufacturing_status='blocked',real_human_approval=False,new_gerber_generated=False)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ['usb','interlock','power','audit']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
