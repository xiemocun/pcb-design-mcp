"""Source-query -> preserved-parent fork -> protection circuit -> actual EDA."""
import asyncio,copy,json,os,sys,time,uuid
from pathlib import Path
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/analog-input-protection';sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts,analog_models,editing,__version__
PARENT='8f5b7aa0dac846ac961ebcfee740b6cc'
async def main():
 start=time.monotonic();frozen=parts.rules_hash();resume='--resume' in sys.argv
 out=Path((W/'latest-run.txt').read_text()) if resume else W/('run-'+uuid.uuid4().hex)
 out.mkdir(exist_ok=True);(W/'latest-run.txt').write_text(str(out));calls=[];phase='cad' if resume else 'design'
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (a,b):
  async with ClientSession(a,b) as session:
   await session.initialize();listing=await session.list_tools();assert {'component_add','usb_c_power_check','manufacturing_review_prepare'} <= {t.name for t in listing.tools};(out/'tools.json').write_text(listing.model_dump_json(indent=2))
   async def call(tool,args=None):
    print(tool,'START',flush=True);v=await session.call_tool(tool,{} if args is None else dict(request=args),read_timeout_seconds=timedelta(seconds=240));calls.append(dict(tool=tool,arguments=args,result=v.model_dump(mode='json')));(out/(phase+'-transcript.json')).write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert not v.isError,(tool,v);return v.structuredContent['data']
   caps=await call('system_capabilities');assert caps['contract_version']==__version__
   parent=await call('project_read',dict(project_id=PARENT))
   if resume:
    saved=json.loads((out/'state.json').read_text());pid=saved['id'];state=await call('project_read',dict(project_id=pid));assert editing.spec_hash(state)==editing.spec_hash(saved);rev=state['revision']
   else:
    fork=await call('project_fork',dict(project_id=PARENT,expected_revision=parent['revision'],name='模拟缓冲板：保持输入范围并加入断电保护',idempotency_key=uuid.uuid4().hex));pid=fork['project_id'];rev=0
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   async def change(tool,**kw):
    nonlocal rev
    v=await call(tool,m(**kw));rev=v['revision'];return v
   if not resume:
    comps={c['ref']:copy.deepcopy(c) for c in parent['components'].values()};nets={n['name']:copy.deepcopy(n) for n in parent['nets'].values()}
    for ref,mpn,value in [('U2','ADG4612BRUZ','ADG4612BRUZ'),('R4','CRCW060310K0FKEA','10k'),('R5','CRCW060310K0FKEA','10k'),('C4','C1608X7R1H104K080AA','100nF')]:
     q=await call('parts_search',dict(query=mpn));assert len(q['candidates'])==1;p=await call('part_get',dict(candidate_id=q['candidates'][0]['candidate_id']))
     q=await call('library_search',dict(query=p['symbol_id']));assert q['total']==1;sym=await call('library_get',dict(candidate_id=q['candidates'][0]['candidate_id']))
     q=await call('footprint_search',dict(query=p['footprint_id']));assert q['total']==1;fp=await call('footprint_get',dict(candidate_id=q['candidates'][0]['candidate_id']))
     c=await change('component_add',candidate_id=sym['candidate_id'],ref=ref,value=value);c['id']=c['component_id'];comps[ref]=c
     await change('footprint_bind',component_id=c['id'],candidate_id=fp['candidate_id'],reason='Source reviewed package and real KiCad pad map')
     await change('part_bind',component_id=c['id'],candidate_id=p['candidate_id'])
    def pin(ref,num):
     c=comps[ref];return dict(component_id=c['id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
    # Remove the original direct input path before connecting the protected path.
    nets['ANALOG_IN']['pins']=[p for p in nets['ANALOG_IN']['pins'] if p!=pin('U1','1')]
    nets['ANALOG_IN']['pins'].append(pin('U2','3'))
    nets['V5']['pins'] += [pin('U2','13'),pin('U2','1'),pin('R5','1'),pin('C4','1')]
    nets['GND']['pins'] += [pin('U2',n) for n in ['4','5','6','7','8','9','10','11','14','15','16']]+[pin('R5','2'),pin('C4','2')]
    for name in ['ANALOG_IN','V5','GND']:await change('net_members_set',net_id=nets[name]['id'],pins=nets[name]['pins'])
    for name,pins in [('ANALOG_SWITCH_OUT',[pin('U2','2'),pin('R4','1')]),('ANALOG_PROTECTED',[pin('R4','2'),pin('U1','1')])]:
     n=await change('net_create',name=name,kind='signal');await change('net_members_set',net_id=n['net_id'],pins=pins)
    await change('pin_intent_set',pin=pin('U2','12'),intent='no_connect',reason='ADG4612 TSSOP Table8 pin12 is NC')
    ratings=copy.deepcopy(parent['passive_rating_requirements'])
    for ref in ['R4','R5']:ratings['requirements'].append(dict(component_id=comps[ref]['id'],max_applied_voltage_v=5.25,max_tolerance_fraction=.01,min_resistor_rated_power_w=.1))
    ratings['requirements'].append(dict(component_id=comps['C4']['id'],max_applied_voltage_v=5.25,max_tolerance_fraction=.1,allowed_dielectrics=['X7R']))
    await change('passive_ratings_set',**ratings)
    budget=copy.deepcopy(parent['power_budget_policy']);budget['loads'].append(dict(pin=pin('U2','13'),max_current_ma=.2,scope='supply_terminal_dc_including_io_drive'));budget['reason']='Declared0.2mA switch supply plus existing3mA reserve covers control input and other unmodeled DC; not a measured total maximum'
    await change('power_budget_set',**budget)
    state=await call('project_read',dict(project_id=pid));(out/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
   assert state['requirements']==parent['requirements'] and state['board_constraints']==parent['board_constraints']
   analog=analog_models.analyze(state);assert not analog['findings'],analog;protection=analog['amplifiers'][0]['input_protection'];assert protection['power_off_topology_supported'] and protection['nonnegative_dc_series_current_reference_a']<.01
   spec=await call('spec_validate',dict(project_id=pid));assert spec['verdict']=='pass',spec
   electrical=await call('electrical_check',dict(project_id=pid));assert electrical['verdict']=='pass',electrical
   bom=await call('bom_check',dict(project_id=pid));assert bom['identity_verdict']=='pass',bom
   usb=await call('usb_c_power_check',dict(project_id=pid));errors={f['code'] for f in usb['findings'] if f['severity']=='error'}
   assert 'USB_EXTERNAL_INPUT_BACKFEED_PATH' not in errors and 'USB_INPUT_PROTECTION_INVALID' not in errors
   assert errors=={'USB_PSD_CURRENT_BOUND_UNPROVEN','USB_PSD_ATTACH_LOAD_UNPROVEN','USB_PSD_EFFECTIVE_CAPACITANCE_UNPROVEN'},errors
   design=dict(project_id=pid,revision=rev,parent_project_id=PARENT,source_evidence_hash=frozen,requirements_unchanged=True,board_dimensions_unchanged=True,analog=analog,usb=usb,whole_board_function_qualified=False)
   (out/'design-report.json').write_text(json.dumps(design,ensure_ascii=False,indent=2))
   if '--design-only' in sys.argv:print(json.dumps(dict(project_id=pid,revision=rev,design_checks_passed=True),indent=2));return
   sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id']);assert (await call('schematic_verify',st))['verdict']=='pass';erc=await call('erc_run',st);assert erc['verdict']=='pass',erc;await call('schematic_preview',st);await call('schematic_export',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']);assert (await call('pcb_verify',dict(**pt,stage='unrouted')))['verdict']=='pass';layout=await call('pcb_layout_check',dict(**pt,stage='unrouted'));assert layout['verdict']=='pass',layout
   await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt);assert drc['verdict']=='pass',drc;assert (await call('pcb_layout_check',pt))['verdict']=='pass';copper=await call('pcb_copper_check',pt);assert copper['geometry_verdict']=='pass' and not any(f['severity']=='error' for f in copper['findings'])
   await call('pcb_preview',pt);bundle=await call('pcb_review_export',pt);await call('bom_export',dict(project_id=pid))
   failed=await session.call_tool('manufacturing_review_prepare',dict(request=dict(**pt,qualification_declaration='Eligibility diagnosis only; no human signing or manufacturing export.')));calls.append(dict(tool='manufacturing_review_prepare',arguments=pt,result=failed.model_dump(mode='json')));(out/(phase+'-transcript.json')).write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert failed.isError and 'PROTOTYPE_CHECKS_REQUIRED' in str(failed);assert all(code in str(failed) for code in errors);assert parts.rules_hash()==frozen
   result=dict(**design,source_version=__version__,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],digital_checks_passed=True,erc=erc,drc=drc,copper=copper,bundle=bundle,physical_verified=False,new_gerber_generated=False,prototype_review_blocked=True,elapsed_seconds=round(time.monotonic()-start,2),manual_interventions=['Existing analog design fork, not a new empty-project or new-function benchmark.','AI selected ADG4612 from primary source, created single-unit physical-pin adapter, added10k limiter/rail bleed and100nF bypass.','Frozen input0.5..4V,25C,external source<=1k,load>=100k,cap<=100pF preserved.','Generic geometry backend handles shared-rail local capacitors and individual native footprint sizes; no LLM routing coordinates.'])
   (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(W/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:result[k] for k in ['project_id','revision','source_evidence_hash','digital_checks_passed','prototype_review_blocked','physical_verified','elapsed_seconds']},indent=2))
if __name__=='__main__':asyncio.run(main())
