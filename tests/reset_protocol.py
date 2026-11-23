"""Targeted real MCP protocol: topology, waveforms, strict declarations and fail-closed readiness."""
import asyncio,copy,json,os,sys,time
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v15'
async def main():
 start=time.monotonic();fixture=json.loads((OLD/'increment-report.json').read_text());pid=fixture['project_id'];out=R/'acceptance-reset';out.mkdir(exist_ok=True);calls=[];guards=[];before={p:p.read_bytes() for p in (R/'data').glob('*/state.json')}
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();listing=await session.list_tools();assert len(listing.tools)==72;(R/'tool-contract.json').write_text(json.dumps(listing.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,args=None,error=None):
    v=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=v.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    if error:
     assert v.isError and error in '\n'.join(c.text for c in v.content if hasattr(c,'text')),(name,error,v);guards.append(error);return
    assert not v.isError,(name,v);return v.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.16.0'
   s=await call('project_read',dict(project_id=pid));base=dict(project_id=pid,revision=s['revision'])
   baseline=await call('reset_timing_check',base);assert baseline['reference_timing_verdict']=='incomplete' and baseline['topology']['pullup_references']==['R4'] and baseline['topology']['capacitor_references']==['C6'] and baseline['topology']['external_ports']==['J2']
   calc=baseline['calculation'];assert abs(calc['tau_nominal_ms']-10.)<1e-12 and all(abs(a-b)<1e-12 for a,b in zip(calc['tau_initial_tolerance_reference_ms'],[8.91,11.11]))
   scenario=dict(**base,scenario=dict(supply_rise_ms=.1,supply_stable_at_ms=.1,initial_en_v=0.,external_en_ports='assumed_disconnected'),origin='proposed_assumption',reason='Illustrative unloaded RC and disconnected programmer; no real rail or reset waveform qualification')
   good=await call('reset_timing_check',scenario);assert good['reference_timing_verdict']=='conditional' and good['verdict']=='incomplete' and good['calculation']['conservative_low_hold_margin_reference_v']>0 and len(good['corners'])==4
   assert await call('reset_timing_check',scenario)==good
   slow=copy.deepcopy(scenario);slow['scenario'].update(supply_rise_ms=100.,supply_stable_at_ms=100.)
   failed=await call('reset_timing_check',slow);assert failed['reference_timing_verdict']=='fail' and any(f['code']=='RESET_LOW_HOLD_NOT_ESTABLISHED' for f in failed['findings'])
   retained=copy.deepcopy(scenario);retained['scenario']['initial_en_v']=.7;r=await call('reset_timing_check',retained);assert r['reference_timing_verdict']=='fail'
   ports=copy.deepcopy(scenario);ports['scenario'].pop('external_en_ports');unknown=await call('reset_timing_check',ports);assert unknown['reference_timing_verdict']=='incomplete' and any(f['code']=='RESET_EXTERNAL_PORT_UNKNOWN' for f in unknown['findings'])
   variants=[]
   p=copy.deepcopy(scenario);p['scenario']['supply_stable_at_ms']=0.;variants.append((p,'RESET_STABILITY_BEFORE_RAMP_END'))
   p=copy.deepcopy(scenario);p.pop('origin');variants.append((p,'RESET_SCENARIO_ORIGIN_REQUIRED'))
   p=copy.deepcopy(scenario);p['reason']='x';variants.append((p,'RESET_SCENARIO_ORIGIN_REQUIRED'))
   p=copy.deepcopy(scenario);p['origin']='measured';variants.append((p,'literal_error'))
   p=copy.deepcopy(scenario);p['scenario']['initial_en_v']=-1.;variants.append((p,'greater_than_equal'))
   p=copy.deepcopy(scenario);p['scenario']['threshold_v']=2.5;variants.append((p,'extra_forbidden'))
   p=copy.deepcopy(scenario);p['scenario']['external_en_ports']='measured_disconnected';variants.append((p,'literal_error'))
   p=copy.deepcopy(scenario);p['manufacturing_approved']=True;variants.append((p,'extra_forbidden'))
   p=copy.deepcopy(scenario);p['profile']='esp32s3';variants.append((p,'literal_error'))
   for p,error in variants:await call('reset_timing_check',p,error)
   await call('reset_timing_check',dict(scenario,revision=s['revision']-1),'REVISION_CONFLICT')
   ready=await call('design_readiness',base);assert ready['status']=='blocked' and not ready['manufacturing_approved'] and next(g for g in ready['gates'] if g['check_id']=='EN_RESET_TIMING')['status']=='blocked'
   startup=await call('power_startup_check',base);assert startup['reference_slew_verdict']=='incomplete' and startup['coverage']['capacitors_found']==13
   budget=await call('power_budget_check',base);assert budget['budget_verdict']=='conditional' and budget['verdict']=='incomplete'
   assert (await call('bom_check',base))['identity_verdict']=='pass'
   led=json.loads((R.parent/'pcb-design-mcp-v12/acceptance-parts/report.json').read_text());assert (await call('engineering_check',dict(project_id=led['project_id'])))['verdict']=='pass'
   await call('manufacturing_review_prepare',dict(project_id=pid,pcb_build_id='0'*32),'UNKNOWN_PCB_BUILD')
   for report in [baseline,good,failed,r,unknown]:assert report['verdict']!='pass' and not report['physical_qualified'] and not report['manufacturing_approved'] and report['actual_reset_release_time_bounds_ms'] is None
 for p,data in before.items():assert p.read_bytes()==data,p
 report=dict(passed=True,call_count=len(calls),tool_count=72,project_id=pid,revision=s['revision'],baseline=baseline,fast_request=scenario,fast_report=good,slow_request=slow,slow_report=failed,precharge_report=r,unknown_port_report=unknown,readiness=ready,guard_checks=guards,project_states_unchanged=len(before),geometry_rebuilt=False,new_gerber_generated=False,manufacturing_approved=False,ordered=False,seconds=round(time.monotonic()-start,2))
 (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['baseline','fast_request','fast_report','slow_request','slow_report','precharge_report','unknown_port_report','readiness']},indent=2))
if __name__=='__main__':asyncio.run(main())
