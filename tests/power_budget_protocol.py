"""Targeted real stdio MCP checks; only a new analysis fork is edited."""
import asyncio,copy,json,os,sys,uuid,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v13'
async def main():
 out=R/'acceptance-power-budget';out.mkdir(exist_ok=True);calls=[];checks=[];started=time.monotonic()
 prior=json.loads((OLD/'acceptance-complete-bom/report.json').read_text());source_id=prior['project_id'];original=(R/'data'/source_id/'state.json').read_bytes()
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();listing=await session.list_tools();assert len(listing.tools)==70
   (R/'tool-contract.json').write_text(json.dumps(listing.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,args=None,error=None):
    v=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=v.model_dump(mode='json')))
    (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    if error:
     message='\n'.join(c.text for c in v.content if hasattr(c,'text'));assert v.isError and error in message,(name,error,v);checks.append(error);return
    assert not v.isError,(name,v);return v.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.14.0'
   fork=await call('project_fork',dict(project_id=source_id,expected_revision=prior['revision'],name='逐供电脚声明的直流预算验证',idempotency_key=uuid.uuid4().hex));pid=fork['project_id'];rev=0
   s=await call('project_read',dict(project_id=pid));c={x['ref']:x for x in s['components'].values()};nets={n['name']:n['id'] for n in s['nets'].values()};source=next(iter(s['power_sources'].values()))
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   def pin(ref,name):return dict(component_id=c[ref]['id'],pin_id=next(p['pin_id'] for p in c[ref]['pins'] if p['name']==name))
   async def set_policy(policy):
    nonlocal rev
    rev=(await call('power_budget_set',m(**policy)))['revision']
   async def check(expected,code=None):
    result=await call('power_budget_check',dict(project_id=pid));assert result['budget_verdict']==expected,(expected,result)
    assert result['verdict']!='pass' and result['actual_input_current_upper_bound_a'] is None and not result['physical_qualified'] and not result['manufacturing_approved']
    if code:assert any(f['code']==code for f in result['findings']),(code,result)
    return result
   await check('incomplete','POWER_BUDGET_MISSING')
   envelope=dict(positive_pin=source['boundary_pin'],ground_net_id=source['ground_net_id'],nominal_v=5,min_v=4.75,max_v=5.25,available_current_ma=1500,ambient_c=25,origin='proposed_assumption',reason='Illustrative declared board input conditions; not measured or independently verified')
   rev=(await call('power_envelope_set',m(**envelope)))['revision']
   policy=dict(profile='esp32_usb_enabled_dc_v1',loads=[dict(pin=pin(ref,name),max_current_ma=current) for ref,name,current in [('U1','VDD',650),('U2','VDD',5),('U4','VCCA',30),('U4','VCCB',30),('U5','VDD',10),('U6','VCC',1)]],stages=[dict(component_id=c[ref]['id'],kind=kind,input_overhead_ma=overhead,efficiency_lower_bound=eta) for ref,kind,overhead,eta in [('U3','buck',1,.8),('U8','ldo',.1,None),('U7','load_switch',1,None)]],reserves=[dict(net_id=nets[name],unmodeled_dc_reserve_ma=current) for name,current in [('VBUS_RAW',2),('VUSB',5),('V3V3',5),('VCC_AUX',2)]],origin='proposed_assumption',reason='Illustrative declared terminal loads, efficiency floor and reserves; not verified worst-case device values')
   request=m(**policy);rev=(await call('power_budget_set',request))['revision'];assert (await call('power_budget_set',request))['revision']==rev
   good=await check('conditional');assert good['coverage']['valid_part_bindings']==32 and len(good['loads'])==6 and len(good['stages'])==3
   limit=next(row['current_limit'] for row in good['stages'] if row['kind']=='load_switch');assert limit['bound_catalog_tolerance_checked'] and limit['assumed_resistor_tolerance_fraction'] is None and limit['resistor_mpn']=='CRCW060330K1FKEA' and not limit['physical_resistor_tolerance_verified']
   # Rejected declarations are atomic and must not consume a revision.
   variants=[]
   p=copy.deepcopy(policy);p['loads'].append(copy.deepcopy(p['loads'][0]));variants.append((p,'DUPLICATE_BUDGET_LOAD'))
   p=copy.deepcopy(policy);p['loads'][0]['pin']=pin('U1','GND');variants.append((p,'BUDGET_LOAD_PIN_UNSUPPORTED'))
   p=copy.deepcopy(policy);p['stages'][0]['kind']='ldo';variants.append((p,'BUDGET_STAGE_ROLE_MISMATCH'))
   p=copy.deepcopy(policy);p['stages'][1]['efficiency_lower_bound']=.9;variants.append((p,'BUDGET_EFFICIENCY_ROLE'))
   p=copy.deepcopy(policy);p['stages'][0]['efficiency_lower_bound']=1.1;variants.append((p,'less_than_equal'))
   p=copy.deepcopy(policy);p['reserves'][0]['net_id']=nets['GND'];variants.append((p,'BUDGET_RESERVE_NET_UNSUPPORTED'))
   p=copy.deepcopy(policy);p['origin']='measured';variants.append((p,'literal_error'))
   p=copy.deepcopy(policy);p['manufacturing_approved']=True;variants.append((p,'extra_forbidden'))
   for p,error in variants:await call('power_budget_set',m(**p),error)
   await call('power_budget_set',dict(m(**policy),expected_revision=rev-1),'REVISION_CONFLICT')
   assert (await call('project_read',dict(project_id=pid)))['revision']==rev
   # Missing declarations remain incomplete; source and stage over-budget cases fail.
   p=copy.deepcopy(policy);p['loads'].pop();await set_policy(p);await check('incomplete','BUDGET_LOAD_MISSING')
   p=copy.deepcopy(policy);p['loads'][0]['max_current_ma']=1200;await set_policy(p);over=await check('fail','BUDGET_STAGE_CAPACITY')
   await set_policy(policy);rev=(await call('power_envelope_set',m(**dict(envelope,available_current_ma=100))))['revision'];await check('fail','BUDGET_SOURCE_CAPACITY')
   rev=(await call('power_envelope_set',m(**dict(envelope,max_v=5.7))))['revision'];await check('fail','LDO_INPUT_RANGE')
   rev=(await call('power_envelope_set',m(**dict(envelope,min_v=4.4))))['revision'];await check('fail','BUCK_INPUT_RANGE')
   rev=(await call('power_envelope_set',m(**envelope)))['revision']
   await set_policy(dict(policy,loads=[],stages=[],reserves=[]));await check('incomplete','BUDGET_LOAD_MISSING')
   await set_policy(policy);good=await check('conditional')
   ready=await call('design_readiness',dict(project_id=pid));assert ready['status']=='blocked' and not ready['manufacturing_approved'];gates={g['check_id']:g['status'] for g in ready['gates']};assert gates['DECLARED_DC_BUDGET']=='pass' and gates['POWER_INTEGRITY']=='blocked' and gates['CURRENT_PCB']=='blocked'
   await call('schematic_build',m(),'SPEC_INVALID')
   # Current-bound tolerance is also used by the existing diagnostics.
   legacy=await call('power_integrity_check',dict(project_id=pid));assert any(row.get('conditional_current_limit',{}).get('bound_catalog_tolerance_checked') for row in legacy['calculations'])
   led=json.loads((R.parent/'pcb-design-mcp-v12/acceptance-parts/report.json').read_text())
   assert (await call('bom_check',dict(project_id=led['project_id'])))['identity_verdict']=='pass'
   assert (await call('engineering_check',dict(project_id=led['project_id'])))['verdict']=='pass'
   assert (R/'data'/source_id/'state.json').read_bytes()==original
 result=dict(passed=True,call_count=len(calls),tool_count=70,project_id=pid,revision=rev,budget=good,readiness=ready,overload_report=over,guard_checks=checks,source_project_unchanged=True,previous_geometry_rebuilt=False,new_gerber_generated=False,real_human_approval=False,ordered=False,seconds=round(time.monotonic()-started,2))
 (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['budget','readiness','overload_report']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
