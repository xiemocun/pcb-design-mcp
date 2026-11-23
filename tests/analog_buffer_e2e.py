"""Frozen analog buffer function, empty project, real queries and EDA backends."""
import asyncio,copy,json,os,sys,uuid,time,hashlib
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/analog-buffer-benchmark';sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts,analog_models
async def main():
 W.mkdir(exist_ok=True);frozen=parts.rules_hash();start=time.monotonic();out=W/('design-'+uuid.uuid4().hex);out.mkdir();(W/'latest-design.txt').write_text(str(out));calls=[]
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (a,b):
  async with ClientSession(a,b) as session:
   await session.initialize()
   available={t.name for t in (await session.list_tools()).tools};assert {'project_create','library_search','parts_search','schematic_build','pcb_autoroute','manufacturing_review_prepare'}<=available
   async def call(name,args=None):
    print(name,'START',flush=True);r=await session.call_tool(name,{} if args is None else {'request':args},read_timeout_seconds=timedelta(seconds=240));calls.append(dict(tool=name,arguments=args,result=r.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert not r.isError,(name,r);return r.structuredContent['data']
   created=await call('project_create',dict(name='从空项目设计：USB-C 模拟电压缓冲板',profile='schematic_experiment'));pid=created['project_id'];rev=0
   (out/'project.json').write_text(json.dumps(created));empty=await call('project_read',dict(project_id=pid));assert not empty['components'] and not empty['nets'] and not empty.get('provenance',{}).get('parent_project_id')
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   async def mutate(tool,**kw):
    nonlocal rev
    result=await call(tool,m(**kw));rev=result['revision'];return result
   req=dict(function='USB-C5V供电，0.5–4.0V缓慢变化的模拟电压缓冲；不用MCU或固件。',acceptance='Frozen usb_analog_buffer_01: source-bound negative unity feedback and actual netlist/ERC/PCB/layout/DRC; physical accuracy/bandwidth/stability separate.',analog_input_min_v=.5,analog_input_max_v=4.,analog_source_max_ohm=1000.,analog_external_load_min_ohm=100000.,analog_external_cap_max_pf=100.,analog_input_connector='J2',analog_output_connector='J3')
   await mutate('requirements_set',items=[dict(key=k,value=v,origin='proposed_assumption') for k,v in req.items()])
   components={};part_records={};symbols={};footprints={}
   specs=[('J1','Connector:USB_C_Receptacle_USB2.0_16P','USB_C_Receptacle_USB2.0_16P','USB4105-GF-A'),('U1','Amplifier_Operational:TLV9001IDCK','TLV9001IDCK','TLV9001IDCKR'),('J2','Connector_Generic:Conn_01x02','Conn_01x02','61300211121'),('J3','Connector_Generic:Conn_01x02','Conn_01x02','61300211121'),('R1','Device:R','5.1k','CRCW06035K10FKEA'),('R2','Device:R','5.1k','CRCW06035K10FKEA'),('R3','Device:R','100k','CRCW0603100KFKEA'),('C1','Device:C','100nF','C1608X7R1H104K080AA'),('C2','Device:C','1uF','C1608X5R1C105K080AA'),('C3','Device:C','1uF','C1608X5R1C105K080AA')]

   for ref,lib,value,mpn in specs:
    if lib not in symbols:
     found=await call('library_search',dict(query=lib));assert found['total']==1,(lib,found);symbols[lib]=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
    if mpn not in part_records:
     found=await call('parts_search',dict(query=mpn));assert len(found['candidates'])==1,(mpn,found);part_records[mpn]=await call('part_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
    part=part_records[mpn];fid=part['footprint_id']
    if fid not in footprints:
     found=await call('footprint_search',dict(query=fid));assert found['total']==1;footprints[fid]=await call('footprint_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
    comp=await mutate('component_add',candidate_id=symbols[lib]['candidate_id'],ref=ref,value=value);components[ref]=comp
    await mutate('footprint_bind',component_id=comp['component_id'],candidate_id=footprints[fid]['candidate_id'],reason='Actual manufacturer package and real local pad numbering')
    await mutate('part_bind',component_id=comp['component_id'],candidate_id=part['candidate_id'])
   def pin(ref,num):
    c=components[ref];return dict(component_id=c['component_id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
   def named(ref,name):
    c=components[ref];return [dict(component_id=c['component_id'],pin_id=p['pin_id']) for p in c['pins'] if p['name']==name]
   nets={}
   mapping={'V5':named('J1','VBUS')+[pin('U1','5'),pin('C1','1'),pin('C2','1'),pin('C3','1')],'GND':named('J1','GND')+named('J1','SHIELD')+[pin('U1','2'),pin('J2','2'),pin('J3','2'),pin('R1','2'),pin('R2','2'),pin('R3','2'),pin('C1','2'),pin('C2','2'),pin('C3','2')],'CC1':named('J1','CC1')+[pin('R1','1')],'CC2':named('J1','CC2')+[pin('R2','1')],'ANALOG_IN':[pin('J2','1'),pin('U1','1')],'ANALOG_OUT':[pin('U1','4'),pin('U1','3'),pin('J3','1'),pin('R3','1')]}

   for name,pp in mapping.items():
    n=await mutate('net_create',name=name,kind='power' if name=='V5' else 'ground' if name=='GND' else 'signal');nets[name]=n['net_id'];await mutate('net_members_set',net_id=n['net_id'],pins=pp)
   connected={p['pin_id'] for pp in mapping.values() for p in pp}
   for p in components['J1']['pins']:
    if p['pin_id'] not in connected:await mutate('pin_intent_set',pin=dict(component_id=components['J1']['component_id'],pin_id=p['pin_id']),intent='no_connect',reason='Type-C power-only benchmark; data/SBU unused')
   positive=named('J1','VBUS')[0]
   await mutate('power_source_set',net_id=nets['V5'],ground_net_id=nets['GND'],boundary_pin=positive,nominal_v=5.,reason='Frozen low-power benchmark board-edge5V input assumption')
   await mutate('power_envelope_set',positive_pin=positive,ground_net_id=nets['GND'],nominal_v=5.,min_v=4.75,max_v=5.25,available_current_ma=100.,ambient_c=25.,origin='proposed_assumption',reason='Benchmark supply range/capacity assumption, not a measurement')
   ratings=[]
   for ref,lib,value,mpn in specs:
    if lib not in ['Device:R','Device:C']:continue
    r=dict(component_id=components[ref]['component_id'],max_applied_voltage_v=5.25,max_tolerance_fraction=.01 if lib=='Device:R' else .2)
    if lib=='Device:R':r['min_resistor_rated_power_w']=.1
    else:r['allowed_dielectrics']=['X5R','X7R']
    ratings.append(r)
   await mutate('passive_ratings_set',requirements=ratings,origin='proposed_assumption',reason='All passive identities and required voltage/tolerance bounds for this benchmark')
   await mutate('power_budget_set',profile='direct_source_dc_v1',loads=[dict(pin=pin('U1','5'),max_current_ma=1.,scope='supply_terminal_dc_including_io_drive')],stages=[],reserves=[dict(net_id=nets['V5'],unmodeled_dc_reserve_ma=3.)],origin='proposed_assumption',reason='No conversion stage;1mA opamp-terminal budget including drive and3mA reserve; design assumptions, not measured maxima')
   await mutate('board_constraints_set',width_mm=70.,height_mm=50.,clearance_mm=.15,track_width_mm=.2,edge_clearance_mm=.5,min_drill_mm=.3,hole_clearance_mm=.15,hole_to_hole_mm=.25,placement_profile='bounded_grid')
   await mutate('pcb_net_rules_set',rules=[dict(net_id=nets[name],min_width_mm=.3,design_current_a=.02,reference_drop_budget_v=.1) for name in ['V5','GND']],copper_thickness_um=35.,temperature_rise_c=20.,origin='proposed_assumption',reason='20mA direct-supply/return screen; physical material and current not measured')
   state=await call('project_read',dict(project_id=pid));(out/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2));analog=analog_models.analyze(state);assert not analog['findings'] and len(analog['amplifiers'])==1
   spec=await call('spec_validate',dict(project_id=pid));assert spec['verdict']=='pass',spec
   budget=await call('power_budget_check',dict(project_id=pid));assert budget['budget_verdict']=='conditional',budget
   usb=await call('usb_c_power_check',dict(project_id=pid));usb_errors=[f['code'] for f in usb['findings'] if f['severity']=='error'];assert 'USB_DIRECT_CAP_NOMINAL_EXCESS' not in usb_errors;assert usb['ports'][0]['nominal_direct_capacitance_uf']<10.
   engineering=await call('electrical_check',dict(project_id=pid));assert engineering['verdict']=='pass',engineering
   sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id']);assert (await call('schematic_verify',st))['verdict']=='pass';erc=await call('erc_run',st);assert erc['verdict']=='pass',erc;await call('schematic_preview',st);await call('schematic_export',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']);assert (await call('pcb_verify',dict(**pt,stage='unrouted')))['verdict']=='pass';layout=await call('pcb_layout_check',dict(**pt,stage='unrouted'));assert layout['verdict']=='pass',layout
   await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt);assert drc['verdict']=='pass',drc;assert (await call('pcb_layout_check',pt))['verdict']=='pass';copper=await call('pcb_copper_check',pt);assert copper['geometry_verdict']=='pass' and not any(f['severity']=='error' for f in copper['findings']),copper
   await call('pcb_preview',pt);bundle=await call('pcb_review_export',pt);await call('bom_export',dict(project_id=pid))
   failed=await session.call_tool('manufacturing_review_prepare',{'request':dict(**pt,qualification_declaration='Check eligibility only; no human signing or manufacturing export requested.')});calls.append(dict(tool='manufacturing_review_prepare',arguments=pt,result=failed.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert failed.isError and 'PROTOTYPE_CHECKS_REQUIRED' in str(failed),failed;assert all(code in str(failed) for code in usb_errors),failed;assert parts.rules_hash()==frozen
   result=dict(digital_artifact_benchmark_passed=True,requirement_id='usb_analog_buffer_01',project_id=pid,revision=rev,source_evidence_hash=frozen,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],started_from_empty=True,template_fork_used=False,component_count=len(state['components']),analog_reference=analog,usb=usb,open_known_errors=usb_errors,usb_qualification_passed=False,full_function_qualified=False,prototype_review_blocked_by_known_usb_errors=True,budget=budget,engineering=engineering,erc=erc,drc=drc,copper=copper,bundle=bundle,elapsed_seconds=round(time.monotonic()-start,2),physical_verified=False,new_gerber_generated=False,manual_interventions=['AI selected source-grounded TLV9001 DCK negative-unity topology after actual5pin query; physical pin1=input,4=output,2=Vminus.','AI authored this function-specific MCP orchestration and selected70x50mm geometry; not a repeated-model success-rate evaluation.','Generic native-geometry placement used; no ESP32/555 reference template copied.'])
   (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(W/'design-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['budget','engineering','erc','drc','copper','bundle','usb']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
