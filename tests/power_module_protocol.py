"""Real MCP incremental power prototype; never claims USB/production approval."""
import asyncio,hashlib,json,os,shutil,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v08'

async def main():
    old=json.loads((OLD/'acceptance-sensor-module/report.json').read_text())
    out=R/'acceptance-power-module';out.mkdir(exist_ok=True)
    data=out/('run-'+uuid.uuid4().hex);shutil.copytree(R/'data',data)
    original=(OLD/'data'/old['project_id']/'state.json').read_bytes();calls=[];checks=[]
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})) as (r,w):
      async with ClientSession(r,w) as session:
        await session.initialize();listed=await session.list_tools();assert len(listed.tools)==62
        (R/'tool-contract.json').write_text(json.dumps(listed.model_dump(mode='json'),ensure_ascii=False,indent=2))
        async def call(name,args=None,error=None):
            result=await session.call_tool(name,{} if args is None else {'request':args})
            calls.append(dict(tool=name,arguments=args,result=result.model_dump(mode='json')))
            (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
            if error:
                assert result.isError and error in '\n'.join(c.text for c in result.content if hasattr(c,'text')),(name,error,result)
                checks.append(error);return
            assert not result.isError,(name,result);return result.structuredContent['data']
        assert (await call('system_capabilities'))['contract_version']=='0.10.0'
        fork=await call('project_fork',dict(project_id=old['project_id'],expected_revision=old['revision'],name='ESP32 电源模块改版：USB条件待修复',idempotency_key=uuid.uuid4().hex))
        pid=fork['project_id'];rev=0;s=await call('project_read',dict(project_id=pid))
        comps={c['ref']:c for c in s['components'].values()};nets={n['name']:n for n in s['nets'].values()}
        def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
        def pin(ref,name=None,number=None):
            c=comps[ref];p=next(p for p in c['pins'] if (p['name']==name if name else p['number']==number))
            return dict(component_id=c['id'],pin_id=p['pin_id'])
        old_id=comps['U3']['id']
        for n in nets.values():
            if any(p['component_id']==old_id for p in n['pins']):
                n['pins']=[p for p in n['pins'] if p['component_id']!=old_id]
                rev=(await call('net_members_set',m(net_id=n['id'],pins=n['pins'])))['revision']
        for intent in s['pin_intents'].values():
            if intent['pin']['component_id']==old_id:rev=(await call('pin_intent_set',m(pin=intent['pin'],intent='unspecified')))['revision']
        rev=(await call('footprint_unbind',m(component_id=old_id)))['revision']
        rev=(await call('component_remove',m(component_id=old_id)))['revision']
        found=await call('parts_search',dict(query='R-78K3.3-1.0'))
        part=await call('part_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
        assert part['evidence_status']=='available' and part['facts']['rated_output_current_a']==1.0
        assert part['facts']['pin_mapping']=={'1':'IN','2':'GND','3':'OUT'}
        for ref,symbol,value,footprint in [('U3',part['symbol_id'],'R78K3V3_1A',part['footprint_id']),('C9','Device:C_Polarized','27uF','Capacitor_THT:CP_Radial_D6.3mm_P2.50mm')]:
            found=await call('library_search',dict(query=symbol));candidate=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
            added=await call('component_add',m(candidate_id=candidate['candidate_id'],ref=ref,value=value));rev=added['revision']
            comps[ref]={**candidate,'id':added['component_id'],'pins':added['pins']}
            found=await call('footprint_search',dict(query=footprint));fp=await call('footprint_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
            rev=(await call('footprint_bind',m(component_id=added['component_id'],candidate_id=fp['candidate_id'],reason='Source pin order/pitch; conservative prototype geometry; full package/assembly qualification and all real MPN bindings remain incomplete')))['revision']
        for name,pins in [('VUSB',[pin('U3','IN'),pin('C9',number='1')]),('GND',[pin('U3','GND'),pin('C9',number='2')]),('V3V3',[pin('U3','OUT')])]:
            nets[name]['pins']+=pins
            rev=(await call('net_members_set',m(net_id=nets[name]['id'],pins=nets[name]['pins'])))['revision']
        req=[x for x in s['requirements'] if x['key']!='main_power']
        req.append(dict(key='main_power',value='RECOM R-78K3.3-1.0 1A prototype with nominal 27uF input electrolysis; input current, USB attach/inrush, ripple, transient and physical assembly unqualified',origin='proposed_assumption'))
        rev=(await call('requirements_set',m(items=req)))['revision']
        spec=await call('spec_validate',dict(project_id=pid));assert spec['verdict']=='pass',spec
        rev=(await call('component_parameters_set',m(component_id=comps['C9']['id'],value='10uF')))['revision']
        bad=await call('spec_validate',dict(project_id=pid));assert 'BUCK_HOTPLUG_CAP_MISSING' in {f['code'] for f in bad['findings']}
        await call('schematic_build',m(),'SPEC_INVALID')
        rev=(await call('component_parameters_set',m(component_id=comps['C9']['id'],value='27uF')))['revision']
        # Swapping capacitor polarity must fail before backend construction.
        for name,remove,add in [('VUSB',pin('C9',number='1'),pin('C9',number='2')),('GND',pin('C9',number='2'),pin('C9',number='1'))]:
            rev=(await call('net_members_set',m(net_id=nets[name]['id'],pins=[p for p in nets[name]['pins'] if p!=remove])))['revision']
        for name,add in [('VUSB',pin('C9',number='2')),('GND',pin('C9',number='1'))]:
            remove=pin('C9',number='1' if name=='VUSB' else '2')
            rev=(await call('net_members_set',m(net_id=nets[name]['id'],pins=[p for p in nets[name]['pins'] if p!=remove]+[add])))['revision']
        bad=await call('spec_validate',dict(project_id=pid));assert 'BUCK_HOTPLUG_CAP_MISSING' in {f['code'] for f in bad['findings']}
        await call('schematic_build',m(),'SPEC_INVALID')
        for name,remove in [('VUSB',pin('C9',number='2')),('GND',pin('C9',number='1'))]:
            keep=[p for p in nets[name]['pins'] if p['component_id']!=comps['C9']['id']]
            rev=(await call('net_members_set',m(net_id=nets[name]['id'],pins=keep)))['revision']
        for name in ['VUSB','GND']:rev=(await call('net_members_set',m(net_id=nets[name]['id'],pins=nets[name]['pins'])))['revision']
        checks += ['Hard-plug capacitor undersized or reversed is rejected before build']
        b=await call('schematic_build',m());sch=dict(project_id=pid,build_id=b['build_id'])
        assert (await call('schematic_verify',sch))['verdict']=='pass'
        erc=await call('erc_run',sch);assert erc['verdict']=='pass',erc
        await call('schematic_preview',sch);sch_export=await call('schematic_export',sch)
        trace=await call('schematic_pin_connections',{**sch,'pin':pin('U3','OUT')});assert trace['net']['name']=='V3V3'
        pcb=await call('pcb_build',m(schematic_build_id=b['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
        assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass'
        layout=await call('pcb_layout_check',{**pt,'stage':'unrouted'});assert layout['verdict']=='pass',layout
        await call('pcb_autoroute',{**pt,'max_passes':60})
        assert (await call('pcb_verify',pt))['verdict']=='pass'
        layout=await call('pcb_layout_check',pt);assert layout['verdict']=='pass',layout
        drc=await call('drc_run',pt);assert drc['verdict']=='pass',drc
        preview=await call('pcb_preview',pt);pcb_export=await call('pcb_review_export',pt)
        usb=await call('usb_c_power_check',dict(project_id=pid));assert usb['verdict']=='fail' and not usb['manufacturing_approved']
        assert {'USB_DIRECT_CAP_NOMINAL_EXCESS','USB_CC_CURRENT_NOT_DETECTED','USB_ATTACH_LOAD_POLICY_MISSING'} <= {f['code'] for f in usb['findings']}
        await call('usb_c_power_check',dict(project_id=pid,available_current_ma=3000.0),'extra_forbidden')
        checked=await call('project_fork',dict(project_id=pid,expected_revision=rev,name='1A 电源条件化诊断，仍不可制造',idempotency_key=uuid.uuid4().hex));did=checked['project_id'];drev=0
        source=next(iter(s['power_sources'].values()))
        def env(min_v=4.75,current=1500.0,ambient=25.0):return dict(project_id=did,expected_revision=drev,idempotency_key=uuid.uuid4().hex,positive_pin=source['boundary_pin'],ground_net_id=source['ground_net_id'],nominal_v=5.0,min_v=min_v,max_v=5.25,available_current_ma=current,ambient_c=ambient,origin='proposed_assumption',reason='Conditional board-side interval only; not measured CC advertisement or manufacturer full input-current proof')
        drev=(await call('power_envelope_set',env()))['revision']
        power=await call('power_integrity_check',dict(project_id=did));assert power['verdict']=='fail'
        assert 'REGULATOR_CURRENT_CAPACITY' not in {f['code'] for f in power['findings']}
        buck=next(x for x in power['calculations'] if x['mpn_basis']=='R-78K3.3-1.0');assert buck['known_output_capacity_screen']=='pass' and not buck['guaranteed_efficiency_available']
        assert (await call('usb_c_power_check',dict(project_id=did)))['verdict']=='fail'
        module=await call('module_constraints_check',dict(project_id=did));assert module['verdict']=='pass' and not module['manufacturing_approved']
        drev=(await call('power_envelope_set',env(min_v=4.4)))['revision']
        bad=await call('power_integrity_check',dict(project_id=did));assert 'BUCK_INPUT_RANGE' in {f['code'] for f in bad['findings']}
        await call('schematic_build',dict(project_id=did,expected_revision=drev,idempotency_key=uuid.uuid4().hex),'SPEC_INVALID')
        drev=(await call('power_envelope_set',env(current=100.0)))['revision']
        bad=await call('power_integrity_check',dict(project_id=did));assert 'SOURCE_CURRENT_BUDGET_LOWER_BOUND' in {f['code'] for f in bad['findings']}
        drev=(await call('power_envelope_set',env(ambient=40.0)))['revision']
        bad=await call('power_integrity_check',dict(project_id=did));assert 'BUCK_TEMPERATURE_SCOPE_UNSUPPORTED' in {f['code'] for f in bad['findings']}
        drev=(await call('power_envelope_set',env()))['revision']
        await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED')
        await call('bom_export',dict(project_id=pid),'BOM_PART_MISSING')
        audit=await call('design_audit',pt);assert audit['status']=='blocked'
        assert next(x for x in audit['stages'] if x['check_id']=='USB_SINK_POWER')['status']=='fail'
        final=await call('project_read',dict(project_id=pid));assert len(final['components'])==22 and len(final['nets'])==13
    assert (OLD/'data'/old['project_id']/'state.json').read_bytes()==original
    demo=R/'demo-power-module';demo.mkdir(exist_ok=True)
    for folder,names in [(Path(pcb_export['bundle_path']).parent,['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','drc_report.json','layout_routed.json','board_routed.json','pcb_review_bundle.zip','fp-lib-table']),(Path(sch_export['schematic_path']).parent,['led.kicad_sch','actual.xml','erc.json','verify.json','spec.json','review_bundle.zip'])]:
        for name in names:shutil.copy2(folder/name,demo/name)
    shutil.copy2(preview['preview_path'],demo/'pcb.svg');shutil.copy2(Path(sch_export['schematic_path']).parent/'preview/led.svg',demo/'schematic.svg')
    shutil.copy2(audit['report_path'],demo/'检查报告.md')
    for name,value in [('usb_power.json',usb),('power_conditional.json',power),('module_conditional.json',module),('actual_regulator_output.json',trace),('audit.json',audit)]: (demo/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    report=dict(passed=True,call_count=len(calls),tool_count=62,project_id=pid,revision=rev,schematic_build_id=b['build_id'],pcb_build_id=pcb['pcb_build_id'],interval_project_id=did,interval_revision=drev,data_root=str(data),power=power,usb=usb,audit=audit,checks=checks,original_preserved=True,component_count=22,net_count=13,whole_board_manufacturing_status='blocked',real_human_approval=False,new_gerber_generated=False)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ['power','usb','audit']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
