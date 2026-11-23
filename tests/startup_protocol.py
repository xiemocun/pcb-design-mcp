"""Real MCP targeted read-only startup scenarios. No state/build/approval changes."""
import asyncio,copy,json,os,sys,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v14'
async def main():
 start=time.monotonic();fixture=json.loads((OLD/'increment-report.json').read_text());pid=fixture['project_id'];out=R/'acceptance-startup';out.mkdir(exist_ok=True);calls=[];checks=[]
 before={p: p.read_bytes() for p in (R/'data').glob('*/state.json')}
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();listing=await session.list_tools();assert len(listing.tools)==71
   (R/'tool-contract.json').write_text(json.dumps(listing.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,args=None,error=None):
    v=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=v.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    if error:
     assert v.isError and error in '\n'.join(c.text for c in v.content if hasattr(c,'text')),(name,error,v);checks.append(error);return
    assert not v.isError,(name,v);return v.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.15.0'
   s=await call('project_read',dict(project_id=pid));nets={n['name']:n['id'] for n in s['nets'].values()}
   baseline=await call('power_startup_check',dict(project_id=pid,revision=s['revision']));assert baseline['reference_slew_verdict']=='incomplete' and baseline['coverage']==dict(capacitors_found=13,direct_power_capacitors=12,signal_or_other_capacitors=1,declared_slew_rails=0,required_rails=4,calculated_rails=0,full_startup_qualified=False)
   rows={r['name']:r for r in baseline['rails']};assert abs(rows['VUSB']['nominal_capacitance_uf']-28.2)<1e-10 and abs(rows['VUSB']['nominal_tolerance_capacitance_uf']-33.72)<1e-10 and rows['VBUS_RAW']['capacitor_references']==['C12']
   assert next(c for c in baseline['capacitors'] if c['reference']=='C6')['scope']=='signal_or_unsupported_connection'
   budget=await call('power_budget_check',dict(project_id=pid));assert budget['budget_verdict']=='conditional' and budget['verdict']=='incomplete'
   scenario=dict(project_id=pid,revision=s['revision'],slew_assumptions=[dict(net_id=n['id'],max_slew_v_per_ms=1) for n in s['nets'].values() if n['kind']=='power'],origin='proposed_assumption',reason='Illustrative instantaneous slew ceilings only; not observed device waveforms or physical guarantees')
   good=await call('power_startup_check',scenario);assert good['reference_slew_verdict']=='conditional' and good['verdict']=='incomplete'
   usb=next(r for r in good['rails'] if r['name']=='VUSB');assert abs(usb['local_capacitor_current_reference_a']-.03372)<1e-12 and usb['max_allowed_slew_reference_v_per_ms']>1 and usb['current_margin_reference_a']>0
   assert await call('power_startup_check',scenario)==good
   fast=copy.deepcopy(scenario);next(p for p in fast['slew_assumptions'] if p['net_id']==nets['VUSB'])['max_slew_v_per_ms']=20
   over=await call('power_startup_check',fast);assert over['reference_slew_verdict']=='fail' and any(f['code']=='STARTUP_REFERENCE_CURRENT_EXCEEDED' for f in over['findings'])
   partial=copy.deepcopy(scenario);partial['slew_assumptions'].pop();incomplete=await call('power_startup_check',partial);assert incomplete['reference_slew_verdict']=='incomplete'
   variants=[]
   p=copy.deepcopy(scenario);p['slew_assumptions'].append(p['slew_assumptions'][0]);variants.append((p,'DUPLICATE_STARTUP_RAIL'))
   p=copy.deepcopy(scenario);p['slew_assumptions'][0]['net_id']=nets['GND'];variants.append((p,'STARTUP_POWER_NET_REQUIRED'))
   p=copy.deepcopy(scenario);p['slew_assumptions'][0]['net_id']='0'*32;variants.append((p,'STARTUP_POWER_NET_REQUIRED'))
   p=copy.deepcopy(scenario);p.pop('origin');variants.append((p,'STARTUP_ORIGIN_REQUIRED'))
   p=copy.deepcopy(scenario);p['reason']='x';variants.append((p,'STARTUP_ORIGIN_REQUIRED'))
   p=copy.deepcopy(scenario);p['origin']='measured';variants.append((p,'literal_error'))
   p=copy.deepcopy(scenario);p['slew_assumptions'][0]['max_slew_v_per_ms']=0;variants.append((p,'greater_than'))
   p=copy.deepcopy(scenario);p['slew_assumptions'][0]['rise_time_ms']=1;variants.append((p,'extra_forbidden'))
   p=copy.deepcopy(scenario);p['manufacturing_approved']=True;variants.append((p,'extra_forbidden'))
   p=copy.deepcopy(scenario);p['capacity_a']=3;variants.append((p,'extra_forbidden'))
   p=copy.deepcopy(scenario);p['profile']='generic_all_circuits';variants.append((p,'literal_error'))
   for p,error in variants:await call('power_startup_check',p,error)
   await call('power_startup_check',dict(scenario,revision=s['revision']-1),'REVISION_CONFLICT')
   ready=await call('design_readiness',dict(project_id=pid));gates={g['check_id']:g['status'] for g in ready['gates']};assert gates['POWER_STARTUP']=='blocked' and gates['DECLARED_DC_BUDGET']=='pass' and not ready['manufacturing_approved']
   await call('manufacturing_review_prepare',dict(project_id=pid,pcb_build_id='0'*32),'UNKNOWN_PCB_BUILD')
   led=json.loads((R.parent/'pcb-design-mcp-v12/acceptance-parts/report.json').read_text())
   assert (await call('engineering_check',dict(project_id=led['project_id'])))['verdict']=='pass'
   assert (await call('bom_check',dict(project_id=pid)))['identity_verdict']=='pass'
   for report in [baseline,good,over,incomplete]:assert not report['physical_qualified'] and not report['manufacturing_approved'] and report['actual_peak_input_current_upper_bound_a'] is None and report['actual_startup_time_upper_bound_ms'] is None
 for p,data in before.items():assert p.read_bytes()==data,p
 report=dict(passed=True,call_count=len(calls),tool_count=71,project_id=pid,revision=s['revision'],baseline=baseline,scenario_request=scenario,scenario_report=good,overload_request=fast,overload_report=over,readiness=ready,guard_checks=checks,project_states_unchanged=len(before),previous_geometry_rebuilt=False,new_gerber_generated=False,manufacturing_approved=False,ordered=False,seconds=round(time.monotonic()-start,2))
 (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['baseline','scenario_request','scenario_report','overload_request','overload_report','readiness']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
