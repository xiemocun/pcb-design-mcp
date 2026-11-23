"""Incrementally repair AM2302 domains on copied ESP32 via real MCP only.

Remaining whole-board capacity/USB/physical qualification blockers stay intact.
"""
import asyncio,hashlib,json,os,shutil,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v07'
async def main():
    old=json.loads((OLD/'acceptance-live.json').read_text())['esp32_audit'];out=R/'acceptance-sensor-module';out.mkdir(exist_ok=True)
    data=out/('run-'+uuid.uuid4().hex);shutil.copytree(R/'data',data)
    source=(OLD/'data'/old['project_id']/'state.json');original_bytes=source.read_bytes();calls=[];checks=[]
    params=StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(params) as (r,w):
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
        caps=await call('system_capabilities');assert caps['contract_version']=='0.10.0'
        fork=await call('project_fork',dict(project_id=old['project_id'],expected_revision=old['revision'],name='ESP32 温湿度板：5V 传感器与受控电平转换原型',idempotency_key=uuid.uuid4().hex))
        pid=fork['project_id'];rev=0;s=await call('project_read',dict(project_id=pid));parts={c['ref']:c for c in s['components'].values()}
        def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
        def pin(ref,name=None,number=None):
            c=parts[ref];p=next(p for p in c['pins'] if (p['name']==name if name is not None else p['number']==number))
            return dict(component_id=c['id'],pin_id=p['pin_id'])
        part=await call('parts_search',dict(query='TXS0102DCTR'));assert part['total']==1
        source_part=await call('part_get',dict(candidate_id=part['candidates'][0]['candidate_id']));assert source_part['evidence_status']=='available'
        assert source_part['facts']['pin_mapping']=={'1':'B2','2':'GND','3':'VCCA','4':'A2','5':'A1','6':'OE','7':'VCCB','8':'B1'}
        defs=[('U4','Logic_LevelTranslator:TXS0102DCT','TXS0102DCT'),('R6','Device:R','100k'),('R7','Device:R','100'),('C7','Device:C','100nF'),('C8','Device:C','100nF')]
        for ref,sym,value in defs:
            found=await call('library_search',dict(query=sym));c=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
            added=await call('component_add',m(candidate_id=c['candidate_id'],ref=ref,value=value));rev=added['revision'];parts[ref]={**c,'id':added['component_id'],'pins':added['pins']}
            fp=(source_part['footprint_id'] if ref=='U4' else ('Resistor_SMD:R_0603_1608Metric' if ref.startswith('R') else 'Capacitor_SMD:C_0603_1608Metric'))
            found=await call('footprint_search',dict(query=fp));chosen=await call('footprint_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
            rev=(await call('footprint_bind',m(component_id=parts[ref]['id'],candidate_id=chosen['candidate_id'],reason='Source-family nominal geometry prototype; complete real MPN BOM and qualification still blocked')))['revision']
        rev=(await call('component_parameters_set',m(component_id=parts['R3']['id'],value='10k')))['revision']
        rev=(await call('pin_intent_set',m(pin=pin('U1','IO26'),intent='unspecified')))['revision']
        nets={n['name']:n for n in s['nets'].values()}
        move=[pin('U2','VDD'),pin('C5',number='1'),pin('R3',number='1')]
        nets['V3V3']['pins']=[p for p in nets['V3V3']['pins'] if p not in move]+[pin('U4','VCCA'),pin('C7',number='1')]
        rev=(await call('net_members_set',m(net_id=nets['V3V3']['id'],pins=nets['V3V3']['pins'])))['revision']
        nets['VUSB']['pins']+=move+[pin('U4','VCCB'),pin('C8',number='1')]
        rev=(await call('net_members_set',m(net_id=nets['VUSB']['id'],pins=nets['VUSB']['pins'])))['revision']
        nets['SENSOR_DATA']['pins']=[p for p in nets['SENSOR_DATA']['pins'] if p!=pin('U1','IO27')]+[pin('U4','B1')]
        rev=(await call('net_members_set',m(net_id=nets['SENSOR_DATA']['id'],pins=nets['SENSOR_DATA']['pins'])))['revision']
        nets['GND']['pins'] += [pin('U4','GND')]+[pin('R6',number='2'),pin('R7',number='2'),pin('C7',number='2'),pin('C8',number='2')]
        rev=(await call('net_members_set',m(net_id=nets['GND']['id'],pins=nets['GND']['pins'])))['revision']
        for name,pins in [('UNUSED_LOW',[pin('U4','A2'),pin('U4','B2'),pin('R7',number='1')]),('MCU_SENSOR_DATA',[pin('U1','IO27'),pin('U4','A1')]),('SENSOR_OE',[pin('U1','IO26'),pin('U4','OE'),pin('R6',number='1')])]:
            n=await call('net_create',m(name=name,kind='signal'));rev=n['revision'];n['id']=n['net_id'];n['pins']=pins;nets[name]=n
            rev=(await call('net_members_set',m(net_id=n['net_id'],pins=pins)))['revision']
        req=list(s['requirements']);req=[r for r in req if r['key'] not in ('sensor_variant','level_translation','firmware_oe')]
        req += [dict(key='sensor_variant',value='Source-correct AM2302 SIP4 on nominal USB 5V; prototype only',origin='proposed_assumption'),dict(key='level_translation',value='TXS0102 DCT: ESP32 IO27/A1, B1/AM2302, IO26/OE with 100k pulldown; idle A2/B2 use a shared 100 ohm pulldown network',origin='proposed_assumption'),dict(key='firmware_oe',value='Keep IO26 low until rails stable; configure bidirectional data protocol; firmware and loaded logic timing unverified',origin='proposed_assumption')]
        rev=(await call('requirements_set',m(items=req)))['revision']
        validation=await call('spec_validate',dict(project_id=pid));assert validation['verdict']=='pass',validation
        assert {'MODULE_SEQUENCING_UNVERIFIED','MODULE_LOADED_LOGIC_UNVERIFIED'} <= {f['code'] for f in validation['findings']}
        module=await call('module_constraints_check',dict(project_id=pid));assert module['verdict']=='incomplete' and not module['manufacturing_approved']
        # Semantic faults must stop builds. Each failed configuration is repaired explicitly.
        async def wrong_connection(code,p,from_name,to_name):
            nonlocal rev
            rev=(await call('net_members_set',m(net_id=nets[from_name]['id'],pins=[x for x in nets[from_name]['pins'] if x!=p])))['revision']
            rev=(await call('net_members_set',m(net_id=nets[to_name]['id'],pins=nets[to_name]['pins']+[p])))['revision']
            report=await call('spec_validate',dict(project_id=pid));assert code in {f['code'] for f in report['findings']},report
            await call('schematic_build',m(),'SPEC_INVALID')
            rev=(await call('net_members_set',m(net_id=nets[to_name]['id'],pins=nets[to_name]['pins'])))['revision']
            rev=(await call('net_members_set',m(net_id=nets[from_name]['id'],pins=nets[from_name]['pins'])))['revision']
            checks.append(code)
        await wrong_connection('MODULE_OE_DRIVER',pin('U4','OE'),'SENSOR_OE','VUSB')
        await wrong_connection('NOMINAL_VOLTAGE_OUT_OF_RANGE',pin('U4','VCCA'),'V3V3','VUSB')
        await wrong_connection('MODULE_UNUSED_CHANNEL',pin('U4','A2'),'UNUSED_LOW','V3V3')
        bypass=[pin('C4',number='1'),pin('C7',number='1')]
        rev=(await call('net_members_set',m(net_id=nets['V3V3']['id'],pins=[p for p in nets['V3V3']['pins'] if p not in bypass])))['revision']
        rev=(await call('net_members_set',m(net_id=nets['VUSB']['id'],pins=nets['VUSB']['pins']+bypass)))['revision']
        bad=await call('spec_validate',dict(project_id=pid));assert 'MODULE_BYPASS_MISSING' in {f['code'] for f in bad['findings']}
        await call('schematic_build',m(),'SPEC_INVALID')
        rev=(await call('net_members_set',m(net_id=nets['VUSB']['id'],pins=nets['VUSB']['pins'])))['revision']
        rev=(await call('net_members_set',m(net_id=nets['V3V3']['id'],pins=nets['V3V3']['pins'])))['revision']
        checks.append('MODULE_BYPASS_MISSING')
        await wrong_connection('MODULE_TRANSLATION_TOPOLOGY',pin('U4','A1'),'MCU_SENSOR_DATA','SENSOR_DATA')
        validation=await call('spec_validate',dict(project_id=pid));assert validation['verdict']=='pass',validation
        b=await call('schematic_build',m());sch=dict(project_id=pid,build_id=b['build_id'])
        assert (await call('schematic_verify',sch))['verdict']=='pass'
        erc=await call('erc_run',sch);assert erc['verdict']=='pass',erc
        await call('schematic_preview',sch);sch_export=await call('schematic_export',sch)
        # Actual netlist: the sensor and ESP32 data ports must be on different nets.
        trace_a=await call('schematic_pin_connections',{**sch,'pin':pin('U1','IO27')})
        trace_b=await call('schematic_pin_connections',{**sch,'pin':pin('U2','SDA')})
        assert trace_a['net']['name']=='MCU_SENSOR_DATA' and trace_b['net']['name']=='SENSOR_DATA'
        assert all(p['reference']!='U1' for p in trace_b['members'])
        pcb=await call('pcb_build',m(schematic_build_id=b['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
        assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass'
        layout=await call('pcb_layout_check',{**pt,'stage':'unrouted'});assert layout['verdict']=='pass',layout
        await call('pcb_autoroute',{**pt,'max_passes':60})
        assert (await call('pcb_verify',pt))['verdict']=='pass'
        layout=await call('pcb_layout_check',pt);assert layout['verdict']=='pass',layout
        drc=await call('drc_run',pt);assert drc['verdict']=='pass',drc
        preview=await call('pcb_preview',pt);pcb_export=await call('pcb_review_export',pt)
        # Fork the fixed topology to test complete voltage intervals without
        # pretending that the remaining whole-board supply budget is solved.
        checked=await call('project_fork',dict(project_id=pid,expected_revision=rev,name='端口电压范围诊断副本：整板仍被拦截',idempotency_key=uuid.uuid4().hex))
        source=next(iter(s['power_sources'].values()))
        env=dict(project_id=checked['project_id'],expected_revision=0,idempotency_key=uuid.uuid4().hex,positive_pin=source['boundary_pin'],ground_net_id=source['ground_net_id'],nominal_v=5.0,min_v=4.75,max_v=5.25,available_current_ma=500.0,ambient_c=25.0,origin='proposed_assumption',reason='Conditional board-side USB 5V interval for isolated static port check; current capability unverified')
        await call('power_envelope_set',env)
        module_bounded=await call('module_constraints_check',dict(project_id=checked['project_id']));assert module_bounded['verdict']=='pass',module_bounded
        assert module_bounded['whole_board_power_verdict']=='fail' and not module_bounded['manufacturing_approved']
        power=await call('power_integrity_check',dict(project_id=checked['project_id']));assert power['verdict']=='fail'
        assert 'SUPPLY_ENVELOPE_OUT_OF_RANGE' not in {f['code'] for f in power['findings']},power
        assert {'REGULATOR_CURRENT_CAPACITY','USB_CURRENT_CAPABILITY_UNVERIFIED'} <= {f['code'] for f in power['findings']}
        await call('schematic_build',dict(project_id=checked['project_id'],expected_revision=1,idempotency_key=uuid.uuid4().hex),'SPEC_INVALID')
        readiness=await call('design_readiness',dict(project_id=pid));assert readiness['status']=='blocked'
        await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED')
        await call('bom_export',dict(project_id=pid),'BOM_PART_MISSING')
        audit=await call('design_audit',dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']));assert audit['status']=='blocked'
        final=await call('project_read',dict(project_id=pid));assert len(final['components'])==21 and len(final['nets'])==13
    assert source_part['evidence_status']=='available'
    assert (OLD/'data'/old['project_id']/'state.json').read_bytes()==original_bytes
    demo=R/'demo-sensor-module';demo.mkdir(exist_ok=True)
    pcfolder=Path(pcb_export['bundle_path']).parent;schfolder=Path(sch_export['schematic_path']).parent
    for name in ['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','drc_report.json','layout_routed.json','board_routed.json','pcb_review_bundle.zip']:shutil.copy2(pcfolder/name,demo/name)
    for name in ['led.kicad_sch','actual.xml','erc.json','verify.json','spec.json','review_bundle.zip']:shutil.copy2(schfolder/name,demo/name)
    shutil.copy2(preview['preview_path'],demo/'pcb.svg');shutil.copy2(schfolder/'preview/led.svg',demo/'schematic.svg')
    shutil.copy2(audit['report_path'],demo/'检查报告.md')
    for name,value in [('module_static.json',module),('module_intervals.json',module_bounded),('whole_board_power.json',power),('actual_low_side_trace.json',trace_a),('actual_high_side_trace.json',trace_b),('readiness.json',readiness)]: (demo/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    report=dict(passed=True,call_count=len(calls),tool_count=62,project_id=pid,revision=rev,schematic_build_id=b['build_id'],pcb_build_id=pcb['pcb_build_id'],interval_project_id=checked['project_id'],data_root=str(data),module_bounded=module_bounded,power=power,audit=audit,checks=checks,original_preserved=True,component_count=21,net_count=13,whole_board_manufacturing_status='blocked',real_human_approval=False,new_gerber_generated=False)
    assert not any((data/pid).rglob('*.gbr'))
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['module_bounded','power','audit','checks']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
