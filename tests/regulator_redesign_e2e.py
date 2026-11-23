"""Actual source query and explicit power-stage redesign via existing MCP tools."""
import asyncio,copy,json,hashlib,os,sys,time,uuid
from pathlib import Path
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];W=Path(os.environ.get('PCB_MCP_REGULATOR_AUDIT_DIR',str(R.parents[1]/'work/regulator-redesign')));sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts,regulator_models,__version__
SOURCE='4c509e681ec947678a65312980925652'
async def main():
 start=time.monotonic();frozen=parts.rules_hash();out=W/('run-'+uuid.uuid4().hex);out.mkdir();(W/'latest-run.txt').write_text(str(out));calls=[]
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (a,b):
  async with ClientSession(a,b) as session:
   await session.initialize()
   async def call(name,args=None):
    print(name,'START',flush=True);t=time.monotonic();v=await session.call_tool(name,{} if args is None else {'request':args},read_timeout_seconds=timedelta(seconds=240));calls.append(dict(tool=name,arguments=args,seconds=round(time.monotonic()-t,3),result=v.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert not v.isError,(name,v);return v.structuredContent['data']
   caps=await call('system_capabilities');assert caps['contract_version']==__version__;old=await call('project_read',dict(project_id=SOURCE));fork=await call('project_fork',dict(project_id=SOURCE,expected_revision=old['revision'],idempotency_key=uuid.uuid4().hex,name='主板3.3V替换：来源绑定的强制PWM反馈电源'));pid=fork['project_id'];rev=0;(out/'project.json').write_text(json.dumps(fork))
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   async def change(tool,**kw):
    nonlocal rev
    v=await call(tool,m(**kw));rev=v['revision'];return v
   state=copy.deepcopy(old);cs={c['ref']:c for c in old['components'].values()};oldbuck=cs['U3'];nets={n['name']:copy.deepcopy(n) for n in old['nets'].values()};budget=copy.deepcopy(old['power_budget_policy']);budget['stages']=[x for x in budget['stages'] if x['component_id']!=oldbuck['id']];await change('power_budget_set',**budget)
   for n in nets.values():
    pp=[p for p in n['pins'] if p['component_id']!=oldbuck['id']]
    if pp!=n['pins']:n['pins']=pp;await change('net_members_set',net_id=n['id'],pins=pp)
   await change('part_unbind',component_id=oldbuck['id']);await change('footprint_unbind',component_id=oldbuck['id']);await change('component_remove',component_id=oldbuck['id'])
   records={};symbols={};fps={}
   async def resolve(mpn):
    if mpn in records:return records[mpn]
    found=await call('parts_search',dict(query=mpn));assert len(found['candidates'])==1;part=await call('part_get',dict(candidate_id=found['candidates'][0]['candidate_id']));records[mpn]=part;lib=part['symbol_id'];fid=part['footprint_id']
    if lib not in symbols:
     found=await call('library_search',dict(query=lib));assert found['total']==1;symbols[lib]=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
    if fid not in fps:
     found=await call('footprint_search',dict(query=fid));assert found['total']==1;fps[fid]=await call('footprint_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
    return part
   async def bind(c,p):
    await change('footprint_bind',component_id=c['id'],candidate_id=fps[p['footprint_id']]['candidate_id'],reason='Source-reviewed exact physical pin groups and manufacturer nominal package geometry');await change('part_bind',component_id=c['id'],candidate_id=p['candidate_id'])
   for ref,mpn,value in [('U3','TPSM82821ASILR','TPSM82821A_SIL'),('R12','TNPW0805453KBEEA','453k'),('R13','TNPW0805100KBEEA','100k'),('C14','06035A121JAT2A','120pF'),('C15','C2012X5R1V106K125AC','10uF')]:
    p=await resolve(mpn);v=await change('component_add',candidate_id=symbols[p['symbol_id']]['candidate_id'],ref=ref,value=value);cs[ref]=dict(id=v['component_id'],pins=v['pins'],ref=ref);await bind(cs[ref],p)
   p=await resolve('C2012X5R1V106K125AC')
   for ref in ['C1','C2']:
    c=cs[ref];await change('part_unbind',component_id=c['id']);await change('footprint_unbind',component_id=c['id']);await change('component_parameters_set',component_id=c['id'],value='10uF');await bind(c,p)
   def pin(ref,name):
    c=cs[ref];q=next(p for p in c['pins'] if p['name']==name or p['number']==name);return dict(component_id=c['id'],pin_id=q['pin_id'])
   additions={'VUSB':[pin('U3','IN'),pin('U3','EN')],'V3V3':[pin('U3','OUT'),pin('R12','1'),pin('C14','1'),pin('C15','1')],'GND':[pin('U3','GND'),pin('R13','2'),pin('C15','2')]}
   for name,pp in additions.items():nets[name]['pins']+=pp;await change('net_members_set',net_id=nets[name]['id'],pins=nets[name]['pins'])
   fb=await change('net_create',name='REG_FB',kind='signal');await change('net_members_set',net_id=fb['net_id'],pins=[pin('U3','FB'),pin('R12','2'),pin('R13','1'),pin('C14','2')]);await change('pin_intent_set',pin=pin('U3','PG'),intent='no_connect',reason='Unused open-drain power-good per manufacturer pin table')
   budget['stages'].append(dict(component_id=cs['U3']['id'],kind='buck',efficiency_lower_bound=.8,input_overhead_ma=20.));budget['reason']='Preserve existing terminal load/efficiency assumptions;20mA FPWM overhead design assumption exceeds8mA typical but is not a guaranteed maximum.';await change('power_budget_set',**budget)
   rating=copy.deepcopy(old['passive_rating_requirements'])
   for ref in ['R12','R13','C14','C15']:
    is_r=ref.startswith('R');rating['requirements'].append(dict(component_id=cs[ref]['id'],max_applied_voltage_v=5.5,max_tolerance_fraction=.001 if is_r else .05 if ref=='C14' else .1,**(dict(min_resistor_rated_power_w=.1) if is_r else dict(allowed_dielectrics=['C0G'] if ref=='C14' else ['X5R','X7R']))))
   await change('passive_ratings_set',**rating)
   policy=copy.deepcopy(old['pcb_copper_policy'])
   for rule in policy['rules']:
    if old['nets'][rule['net_id']]['name'] in ['V3V3','VUSB','GND']:rule['min_width_mm']=.3
   policy['reason']='Same declared current/thickness/rise inputs;0.30mm conductors meet IPC reference current screen and fit0.5mm module pad pitch. Verify actual paths against improved source interval; not a width waiver.';await change('pcb_net_rules_set',**policy)
   req=copy.deepcopy(old['requirements']);next(x for x in req if x['key']=='main_power')['value']='Source-selected TPSM82821ASILR forcedPWM,453k/100k0.1%25ppm divider,120pF feedforward,10uF input and2x10uF local output; actual effectiveC/ripple/transients unverified';await change('requirements_set',items=req)
   state=await call('project_read',dict(project_id=pid));(out/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2));reg=regulator_models.analyze(state);assert not any(f['severity']=='error' for f in reg['findings']),reg;assert reg['regulators'][0]['output_interval_v']['min_v']>3.2
   for name,key,value in [('spec_validate','verdict','pass'),('module_constraints_check','verdict','pass'),('usb_c_gate_scenarios','verdict','pass'),('power_budget_check','budget_verdict','conditional'),('bom_check','identity_verdict','pass')]:
    v=await call(name,dict(project_id=pid));assert v[key]==value,(name,v)
   sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id']);assert (await call('schematic_verify',st))['verdict']=='pass';erc=await call('erc_run',st);assert erc['verdict']=='pass',erc;await call('schematic_preview',st);await call('schematic_export',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']);assert (await call('pcb_verify',dict(**pt,stage='unrouted')))['verdict']=='pass';layout=await call('pcb_layout_check',dict(**pt,stage='unrouted'));assert layout['verdict']=='pass',layout
   await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt)
   for retry in range(2):
    if drc['verdict']=='pass':break
    assert all(f['type'] in ['unconnected_items','via_dangling'] for f in drc['findings']),drc
    pcb=await call('pcb_route_retry_prepare',m(pcb_build_id=pt['pcb_build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']);await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt)
   assert drc['verdict']=='pass',drc;assert (await call('pcb_layout_check',pt))['verdict']=='pass';copper=await call('pcb_copper_check',pt);assert copper['geometry_verdict']=='pass' and not any(f['severity']=='error' for f in copper['findings']),copper
   assert all(p['status']=='within_reference' for p in copper['dc_track_paths']),copper['dc_track_paths'];await call('pcb_preview',pt);bundle=await call('pcb_review_export',pt);await call('bom_export',dict(project_id=pid))
   assert parts.rules_hash()==frozen
   for f,sha in json.loads((W/'baseline/states.json').read_text()).items():assert hashlib.sha256(Path(f).read_bytes()).hexdigest()==sha,f
   result=dict(passed=True,project_id=pid,revision=rev,schematic_build_id=sch['build_id'],pcb_build_id=pt['pcb_build_id'],source_evidence_hash=frozen,regulator=reg,erc=erc,drc=drc,copper=copper,bundle=bundle,elapsed_seconds=round(time.monotonic()-start,2),original_projects_unchanged=True,physical_verified=False,new_gerber_generated=False,template_used='existingESP32board_modified_power_stage_not_empty_project')
   (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(W/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['erc','drc','copper','bundle']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
