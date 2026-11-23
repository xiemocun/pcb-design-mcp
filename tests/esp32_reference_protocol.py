"""A real stdio MCP client: source-grounded 16-part ESP32/AM2302/USB-C experiment.

Intentionally nominal-only: no worst-case supply, thermal or USB current proof.
Every source selection and design edit uses the public MCP contract.
"""
import asyncio,json,os,sys,uuid,shutil
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]


async def run():
    out=ROOT/'acceptance-esp32-reference';out.mkdir(exist_ok=True)
    data=out/('run-'+uuid.uuid4().hex);calls=[];checks=[]
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_server.py')],
                                env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as session:
            await session.initialize()
            tools=await session.list_tools();assert len(tools.tools)==68
            (out/'tools.json').write_text(json.dumps(tools.model_dump(mode='json'),ensure_ascii=False,indent=2))
            async def call(name,args=None,error=None):
                response=await asyncio.wait_for(session.call_tool(name,{} if args is None else {'request':args}),145)
                calls.append(dict(tool=name,arguments=args,result=response.model_dump(mode='json')))
                (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
                if error:
                    assert response.isError,(name,response)
                    assert error in '\n'.join(c.text for c in response.content if hasattr(c,'text')),response
                    checks.append(error);return None
                assert not response.isError,(name,response)
                return response.structuredContent['data']
            caps=await call('system_capabilities');assert caps['contract_version']=='0.12.0'
            rules=await call('rules_list');assert len(rules['rulepacks'])==7
            for item in rules['rulepacks']:
                assert item['evidence']['url'].startswith('https://')
                assert (await call('rules_get',dict(rulepack_id=item['id'])))['rulepack']==item
            p=await call('project_create',dict(name='ESP32 + AM2302 + Type-C nominal schematic experiment'))
            pid=p['project_id'];rev=p['revision']
            def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
            reqs=[dict(key='function',value='ESP32 temperature/humidity acquisition via AM2302',origin='proposed_assumption'),
                  dict(key='input_power',value='USB Type-C nominal 5V, current availability unverified',origin='proposed_assumption'),
                  dict(key='sensor_variant',value='KiCad PCB_MCP:AM2302_SIP4 symbol only; exact purchased DHT22/AM2302 package unresolved',origin='proposed_assumption'),
                  dict(key='qualification',value='Nominal schematic experiment; not a functional or manufacturing approval',origin='proposed_assumption')]
            rev=(await call('requirements_set',m(items=reqs)))['revision']
            search=await call('library_search',dict(query='DHT22'))
            assert any(c['library_id']=='Sensor:AM2302' for c in search['candidates'])
            checks.append('Description search finds real AM2302 without silently asserting a purchased DHT22 package')
            candidates={}
            for name in ['RF_Module:ESP32-WROOM-32E','PCB_MCP:AM2302_SIP4','Regulator_Linear:TLV75533PDBV',
                         'Connector:USB_C_Receptacle_USB2.0_16P','Connector_Generic:Conn_01x06','Device:R','Device:C','power:PWR_FLAG']:
                found=await call('library_search',dict(query=name));assert found['total']==1,found
                c=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
                assert c['supported'],c;candidates[name]=c
            assert candidates['Regulator_Linear:TLV75533PDBV']['inherited']
            assert any(len(p['physical_numbers'])==4 for p in candidates['RF_Module:ESP32-WROOM-32E']['pins'])
            await call('component_add',m(candidate_id=candidates['power:PWR_FLAG']['candidate_id'],ref='P1',value='PWR_FLAG'),'DECLARATION_REQUIRED')
            parts={}
            definitions=[('U1','RF_Module:ESP32-WROOM-32E',None),('U2','PCB_MCP:AM2302_SIP4',None),
                         ('U3','Regulator_Linear:TLV75533PDBV',None),('J1','Connector:USB_C_Receptacle_USB2.0_16P',None),
                         ('J2','Connector_Generic:Conn_01x06',None)]
            definitions += [('R'+str(i),'Device:R','5.1k' if i<=3 else '10k') for i in range(1,6)]
            definitions += [('C'+str(i),'Device:C',v) for i,v in enumerate(['1uF','1uF','10uF','100nF','100nF','1uF'],1)]
            for ref,name,value in definitions:
                c=candidates[name]
                part=await call('component_add',m(candidate_id=c['candidate_id'],ref=ref,value=value or c['symbol']))
                rev=part['revision'];parts[ref]=part
            def pin(ref,number):
                c=parts[ref]
                return dict(component_id=c['component_id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==number))
            def named(ref,name):
                c=parts[ref]
                return dict(component_id=c['component_id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['name']==name))
            def all_named(ref,name):
                c=parts[ref]
                return [dict(component_id=c['component_id'],pin_id=p['pin_id']) for p in c['pins'] if p['name']==name]
            mapping={
              'VUSB':all_named('J1','VBUS')+[named('U3','IN'),named('U3','EN'),pin('C1','1')],
              'V3V3':[named('U3','OUT'),named('U1','VDD'),named('U2','VDD'),pin('J2','2')]+[pin('R'+str(i),'1') for i in [3,4,5]]+[pin('C'+str(i),'1') for i in [2,3,4,5]],
              'GND':all_named('J1','GND')+all_named('J1','SHIELD')+all_named('U1','GND')+all_named('U2','GND')+[named('U3','GND'),pin('J2','1')]+[pin('R'+str(i),'2') for i in [1,2]]+[pin('C'+str(i),'2') for i in range(1,7)],
              'CC1':[named('J1','CC1'),pin('R1','1')],
              'CC2':[named('J1','CC2'),pin('R2','1')],
              'SENSOR_DATA':[named('U1','IO27'),named('U2','SDA'),pin('R3','2')],
              'MCU_EN':[named('U1','EN'),pin('R4','2'),pin('C6','1'),pin('J2','5')],
              'MCU_BOOT':[named('U1','IO0'),pin('R5','2'),pin('J2','6')],
              'UART_RX':[named('U1','RXD0/IO3'),pin('J2','3')],
              'UART_TX':[named('U1','TXD0/IO1'),pin('J2','4')],
            }
            nets={}
            for name in mapping:
                kind='ground' if name=='GND' else ('power' if name in ['VUSB','V3V3'] else 'signal')
                n=await call('net_create',m(name=name,kind=kind));rev=n['revision'];nets[name]=n['net_id']
                rev=(await call('net_members_set',m(net_id=n['net_id'],pins=mapping[name])))['revision']
            used={(p['component_id'],p['pin_id']) for group in mapping.values() for p in group}
            for ref,part in parts.items():
                for p in part['pins']:
                    if (part['component_id'],p['pin_id']) not in used:
                        reason=('USB data and SBU deliberately unused in this power-only experiment' if ref=='J1' else 'Reserved or unused module pin; not required by this experimental circuit')
                        rev=(await call('pin_intent_set',m(pin=pin(ref,p['number']),intent='no_connect',reason=reason)))['revision']
            missing=await call('electrical_check',dict(project_id=pid))
            assert missing['verdict']=='fail' and any(f['code']=='VOLTAGE_EVIDENCE_MISSING' for f in missing['findings'])
            await call('schematic_build',m(),'SPEC_INVALID')
            await call('power_source_set',m(net_id=nets['V3V3'],ground_net_id=nets['GND'],boundary_pin=named('U1','VDD'),nominal_v=3.3,reason='Incorrect internal pin claim'),'INVALID_POWER_BOUNDARY')
            source=await call('power_source_set',m(net_id=nets['VUSB'],ground_net_id=nets['GND'],boundary_pin=all_named('J1','VBUS')[0],nominal_v=5.0,reason='External USB Type-C source assumed nominal 5V; current unverified'))
            rev=source['revision']
            # Removing declarations must invalidate evidence; restoration is explicit.
            rev=(await call('power_source_remove',m(source_id=source['entity_id'])))['revision']
            assert (await call('electrical_check',dict(project_id=pid)))['verdict']=='fail'
            source=await call('power_source_set',m(net_id=nets['VUSB'],ground_net_id=nets['GND'],boundary_pin=all_named('J1','VBUS')[0],nominal_v=5.0,reason='External nominal 5V assumption for schematic-only experiment'))
            rev=source['revision']
            rev=(await call('power_source_set',m(source_id=source['entity_id'],net_id=nets['VUSB'],ground_net_id=nets['GND'],boundary_pin=all_named('J1','VBUS')[0],nominal_v=9.0,reason='Unsupported PD voltage test without a PD controller')))['revision']
            assert any(f['code']=='USB_NOMINAL_5V_POLICY' for f in (await call('electrical_check',dict(project_id=pid)))['findings'])
            await call('schematic_build',m(),'SPEC_INVALID')
            rev=(await call('power_source_set',m(source_id=source['entity_id'],net_id=nets['VUSB'],ground_net_id=nets['GND'],boundary_pin=all_named('J1','VBUS')[0],nominal_v=5.0,reason='Restored nominal 5V-only experiment')))['revision']
            checks.append('Power evidence required; direct flags and internal GPIO power declarations rejected')
            # Correct connectivity/ERC would not catch a 330-ohm Type-C Rd: electrical pack must.
            rev=(await call('component_parameters_set',m(component_id=parts['R1']['component_id'],value='330')))['revision']
            bad=await call('spec_validate',dict(project_id=pid));assert any(f['code']=='USB_RD_MISSING' for f in bad['findings'])
            await call('schematic_build',m(),'SPEC_INVALID')
            rev=(await call('component_parameters_set',m(component_id=parts['R1']['component_id'],value='5.1k')))['revision']
            # Remove MCU VDD from 3V3, then place on 5V while preserving pin accounting.
            vdd=named('U1','VDD')
            rev=(await call('net_members_set',m(net_id=nets['V3V3'],pins=[p for p in mapping['V3V3'] if p!=vdd])))['revision']
            rev=(await call('net_members_set',m(net_id=nets['VUSB'],pins=mapping['VUSB']+[vdd])))['revision']
            bad=await call('electrical_check',dict(project_id=pid));assert any(f['code']=='NOMINAL_VOLTAGE_OUT_OF_RANGE' for f in bad['findings'])
            await call('schematic_build',m(),'SPEC_INVALID')
            rev=(await call('net_members_set',m(net_id=nets['VUSB'],pins=mapping['VUSB'])))['revision']
            rev=(await call('net_members_set',m(net_id=nets['V3V3'],pins=mapping['V3V3'])))['revision']
            checks.append('Wrong CC Rd and ESP32 on 5V both fail before generation, then recover after repair')
            # Select actual local footprints before freezing the schematic revision.
            for ref,part in parts.items():
                fp={'U1':'RF_Module:ESP32-WROOM-32E','U2':'Sensor:ASAIR_AM2302_P2.54mm_Vertical','U3':'Package_TO_SOT_SMD:SOT-23-5','J1':'Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal','J2':'Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical'}.get(ref,'Resistor_SMD:R_0603_1608Metric' if ref.startswith('R') else ('Capacitor_SMD:C_0805_2012Metric' if ref=='C3' else 'Capacitor_SMD:C_0603_1608Metric'))
                found=await call('footprint_search',dict(query=fp));assert found['total']==1,found
                chosen=await call('footprint_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
                rev=(await call('footprint_bind',m(component_id=part['component_id'],candidate_id=chosen['candidate_id'],reason='Prototype geometry only; real MPN/rating qualification remains blocked')))['revision']
            rev=(await call('board_constraints_set',m(width_mm=100.0,height_mm=80.0,clearance_mm=.2,track_width_mm=.25,min_drill_mm=.2,hole_clearance_mm=.15)))['revision']
            assert any(f['code']=='RF_LAYOUT_PROFILE_REQUIRED' for f in (await call('pcb_preflight',dict(project_id=pid)))['findings'])
            rev=(await call('board_constraints_set',m(width_mm=100.0,height_mm=80.0,clearance_mm=.2,track_width_mm=.25,min_drill_mm=.2,hole_clearance_mm=.15,placement_profile='esp32_sensor_reference')))['revision']
            valid=await call('spec_validate',dict(project_id=pid));assert valid['verdict']=='pass',valid
            electrical=await call('electrical_check',dict(project_id=pid))
            assert electrical['qualification']=='experimental_nominal_checks_only'
            assert any(f['code']=='USB_CURRENT_CAPABILITY_UNVERIFIED' for f in electrical['findings'])
            assert any(f['code']=='TOLERANCE_THERMAL_BUDGET_UNVERIFIED' for f in electrical['findings'])
            assert any(f['code']=='SENSOR_SUPPLY_MARGIN_UNVERIFIED' for f in electrical['findings'])
            built=await call('schematic_build',m())
            target=dict(project_id=pid,build_id=built['build_id'])
            verify=await call('schematic_verify',target);assert verify['verdict']=='pass',verify
            assert all(('U1',n) in [tuple(v) for v in verify['actual_nets']['GND']] for n in ['1','15','38','39'])
            erc=await call('erc_run',target);assert erc['verdict']=='pass',erc
            await call('schematic_preview',target)
            export=await call('schematic_export',target)
            assert export['manifest']['unresolved_findings'] and export['manifest']['not_manufacturing_ready']
            folder=Path(export['schematic_path']).parent
            for file in ['electrical_validation.json','rulepacks.json','power_flag_snapshot.json']:
                original=(folder/file).read_bytes();(folder/file).write_bytes(original+b'\n')
                await call('schematic_export',target,'ARTIFACT_CHANGED');(folder/file).write_bytes(original)
            checks.append('16 components, 10 nets, inherited LDO, grouped ESP32 ground: exact KiCad netlist and zero ERC')
            checks.append('Source metadata and flag evidence hash-protected; unresolved limits survive export')
            pcb=await call('pcb_build',m(schematic_build_id=built['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
            assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass'
            await call('pcb_autoroute',pt,'LAYOUT_VALIDATION_REQUIRED')
            layout=await call('pcb_layout_check',{**pt,'stage':'unrouted'});assert layout['verdict']=='pass',layout
            await call('pcb_autoroute',{**pt,'max_passes':50})
            assert (await call('pcb_verify',pt))['verdict']=='pass'
            layout=await call('pcb_layout_check',pt);assert layout['verdict']=='pass',layout
            drc=await call('drc_run',pt);assert drc['verdict']=='pass',drc
            preview=await call('pcb_preview',pt)
            pcb_export=await call('pcb_review_export',pt)
            power=await call('power_integrity_check',dict(project_id=pid));assert power['verdict']=='incomplete',power
            assert {'SUPPLY_ENVELOPE_OUT_OF_RANGE','REGULATOR_CURRENT_CAPACITY','USB_CURRENT_CAPABILITY_UNVERIFIED'}<={f['code'] for f in power['findings']}
            readiness=await call('design_readiness',dict(project_id=pid));assert readiness['status']=='blocked' and not readiness['manufacturing_approved'],readiness
            assert 'POWER_INTEGRITY' in [g['code'] for g in readiness['blockers']]
            await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED')
            checks+=['Source-correct AM2302 pin3=NC/pin4=GND; exact real KiCad netlist/ERC','Actual 16-part two-layer PCB; constrained antenna/proximity layout before and after Freerouting; DRC','Insufficient sensor rail margin/LDO budget/USB current evidence explicitly block manufacturing review']
            demo=ROOT/'demo-esp32-reference';demo.mkdir(exist_ok=True)
            for name in ['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','board_routed.json','drc_report.json','layout_routed.json','pcb_review_bundle.zip']:
                shutil.copy2(Path(pcb_export['bundle_path']).parent/name,demo/name)
            shutil.copy2(preview['preview_path'],demo/'pcb.svg')
            (demo/'power_integrity.json').write_text(json.dumps(power,ensure_ascii=False,indent=2))
            (demo/'readiness.json').write_text(json.dumps(readiness,ensure_ascii=False,indent=2))

            for name in ['led.kicad_sch','review_bundle.zip','erc.json','actual.xml','verify.json','manifest.json','electrical_validation.json','rulepacks.json','spec.json']:
                shutil.copy2(folder/name,demo/name)
            shutil.copy2(folder/'preview/led.svg',demo/'preview.svg')
            (out/'successful_build.json').write_text(json.dumps(dict(project_id=pid,revision=rev,build=built,export=export),ensure_ascii=False,indent=2))
    report=dict(passed=True,call_count=len(calls),checks=checks,data_root=str(data),project_id=pid,revision=rev,pcb=pcb,readiness=readiness,power=power,
                qualification='Actual routed and checked prototype PCB, with explicit power/MPN/manufacturing blockers; no human manufacturing approval or Gerber export.')
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(run())
