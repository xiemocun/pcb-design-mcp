"""Targeted real MCP: preserve30 identities, complete32, polarity/clear gates, rebuild."""
import asyncio,copy,json,os,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v12'
async def main():
 out=R/'acceptance-complete-bom';out.mkdir(exist_ok=True);calls=[];checks=[]
 # Work in the new managed version. Only forked projects may be edited.
 prior=json.loads((OLD/'acceptance-bom/report.json').read_text());source_id=prior['project_id'];original=(R/'data'/source_id/'state.json').read_bytes()
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();contract=await session.list_tools();assert len(contract.tools)==68
   (R/'tool-contract.json').write_text(json.dumps(contract.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,args=None,error=None):
    v=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=v.model_dump(mode='json')))
    (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    if error:
     assert v.isError and error in '\n'.join(c.text for c in v.content if hasattr(c,'text')),(name,error,v);checks.append(error);return
    assert not v.isError,(name,v);return v.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.13.0'
   b=await call('bom_check',dict(project_id=source_id));assert b['counts']['identity_checked']==30 and b['counts']['missing']==2
   fork=await call('project_fork',dict(project_id=source_id,expected_revision=prior['revision'],name='完整32项实物BOM与极性电容检查',idempotency_key=uuid.uuid4().hex));pid=fork['project_id'];rev=0
   s=await call('project_read',dict(project_id=pid));c={x['ref']:x for x in s['components'].values()}
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   selected={}
   for ref,mpn in [('J2','61300611121'),('C9','EEAFC1V270')]:
    rows=await call('parts_search',dict(query=mpn));assert rows['total']==1;part=await call('part_get',dict(candidate_id=rows['candidates'][0]['candidate_id']));selected[ref]=part
   await call('part_bind',m(component_id=c['J2']['id'],candidate_id=selected['J2']['candidate_id']),'PART_BINDING_MISMATCH')
   fp=await call('footprint_search',dict(query=selected['J2']['footprint_id']));assert fp['total']==1
   fp=await call('footprint_get',dict(candidate_id=fp['candidates'][0]['candidate_id']))
   rev=(await call('footprint_bind',m(component_id=c['J2']['id'],candidate_id=fp['candidate_id'],reason='Manufacturer recommended1.10mm holes,2.54mm pitch; nominal drawing pairing, not physical qualification')))['revision']
   for ref,p in selected.items():rev=(await call('part_bind',m(component_id=c[ref]['id'],candidate_id=p['candidate_id'])))['revision']
   b=await call('bom_check',dict(project_id=pid));assert b['identity_verdict']=='pass' and b['counts']['identity_checked']==32 and b['counts']['missing']==0
   assert b['declared_rating_screen']=='incomplete' # new capacitor has no declared requirement yet
   reqs=copy.deepcopy(s['passive_rating_requirements']['requirements'])
   reqs.append(dict(component_id=c['C9']['id'],max_applied_voltage_v=5.5,max_tolerance_fraction=.2,allowed_dielectrics=['aluminum_oxide'],polarized_connection='positive_to_power_negative_to_ground'))
   def requirements(rows):return m(requirements=rows,origin='proposed_assumption',reason='Declared conservative5.5V nominal rating screen; not measured stress, waveform, ripple, ESR, temperature, life, effective capacitance or manufacture approval')
   wrong=copy.deepcopy(reqs);wrong[-1].pop('polarized_connection')
   await call('passive_ratings_set',requirements(wrong),'RATING_POLARITY_REQUIRED')
   wrong=copy.deepcopy(reqs);wrong[-1]['max_tolerance_fraction']=.1
   rev=(await call('passive_ratings_set',requirements(wrong)))['revision'];assert (await call('bom_check',dict(project_id=pid)))['declared_rating_screen']=='fail'
   wrong=copy.deepcopy(reqs);wrong[-1]['max_applied_voltage_v']=36.0
   rev=(await call('passive_ratings_set',requirements(wrong)))['revision'];assert (await call('bom_check',dict(project_id=pid)))['declared_rating_screen']=='fail'
   request=requirements(reqs);rev=(await call('passive_ratings_set',request))['revision'];assert (await call('passive_ratings_set',request))['revision']==rev
   # Two real typed edits swap the polarised capacitor, then prove hard failure.
   pn=lambda num:dict(component_id=c['C9']['id'],pin_id=next(p['pin_id'] for p in c['C9']['pins'] if p['number']==num))
   positive=next(n for n in s['nets'].values() if pn('1') in n['pins']);negative=next(n for n in s['nets'].values() if pn('2') in n['pins'])
   rev=(await call('net_members_set',m(net_id=positive['id'],pins=[p for p in positive['pins'] if p!=pn('1')])))['revision']
   rev=(await call('net_members_set',m(net_id=negative['id'],pins=[p for p in negative['pins'] if p!=pn('2')]+[pn('1')])))['revision']
   rev=(await call('net_members_set',m(net_id=positive['id'],pins=[p for p in positive['pins'] if p!=pn('1')]+[pn('2')])))['revision']
   bad=await call('bom_check',dict(project_id=pid));assert bad['verdict']=='fail' and any(f['code']=='POLARIZED_CAP_REVERSED' for f in bad['findings'])
   await call('schematic_build',m(),'SPEC_INVALID')
   rev=(await call('net_members_set',m(net_id=positive['id'],pins=[p for p in positive['pins'] if p!=pn('1')])))['revision']
   rev=(await call('net_members_set',m(net_id=negative['id'],pins=negative['pins'])))['revision']
   rev=(await call('net_members_set',m(net_id=positive['id'],pins=positive['pins'])))['revision']
   good=await call('bom_check',dict(project_id=pid));assert good['identity_verdict']=='pass' and good['declared_rating_screen']=='conditional' and good['counts']['passive_screened']==22
   assert good['verdict']=='incomplete' and not good['physical_qualified'] and not good['manufacturing_approved']
   assert next(x for x in good['rows'] if x['reference']=='C9')['rating_status']=='conditional'
   bom=await call('bom_export',dict(project_id=pid));assert len(bom['rows'])==32
   assert all(x['supplier_selection']=='not_selected' and x['supplier_sku']=='' for x in bom['rows'] if x['reference'] in ['J2','C9'])
   draft=await call('bom_draft_export',dict(project_id=pid));assert len(draft['report']['rows'])==32
   ready=await call('design_readiness',dict(project_id=pid));assert next(g for g in ready['gates'] if g['check_id']=='REAL_PARTS')['status']=='pass' and ready['status']=='blocked'
   assert (await call('spec_validate',dict(project_id=pid)))['verdict']=='pass'
   sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id'])
   assert (await call('schematic_verify',st))['verdict']=='pass';assert (await call('erc_run',st))['verdict']=='pass';await call('schematic_preview',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
   assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass';assert (await call('pcb_layout_check',{**pt,'stage':'unrouted'}))['verdict']=='pass'
   await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt)
   assert drc['verdict']=='pass',drc
   assert (await call('pcb_layout_check',pt))['verdict']=='pass';copper=await call('pcb_copper_check',pt);assert copper['verdict']=='incomplete'
   preview=await call('pcb_preview',pt);await call('pcb_review_export',pt)
   await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED')
   # Clear all requirements via the actual tool, on a separate spec-only fork.
   clear=await call('project_fork',dict(project_id=pid,expected_revision=rev,name='额定要求清空与幂等性回归',idempotency_key=uuid.uuid4().hex))
   request=dict(project_id=clear['project_id'],expected_revision=0,idempotency_key=uuid.uuid4().hex,requirements=[],origin='proposed_assumption',reason='Explicitly clear all declared rating assumptions in isolated fork')
   cleared=await call('passive_ratings_set',request);assert await call('passive_ratings_set',request)==cleared
   cb=await call('bom_check',dict(project_id=clear['project_id']));assert cb['identity_verdict']=='pass' and cb['declared_rating_screen']=='incomplete'
   assert not (await call('project_read',dict(project_id=clear['project_id'])))['passive_rating_requirements']['requirements']
   # ExistingLED identity/stress calculation remains valid; no unnecessary reroute.
   led=json.loads((OLD/'acceptance-parts/report.json').read_text())
   assert (await call('bom_check',dict(project_id=led['project_id'])))['identity_verdict']=='pass'
   assert (await call('engineering_check',dict(project_id=led['project_id'])))['verdict']=='pass'
   assert (R/'data'/source_id/'state.json').read_bytes()==original
 report=dict(passed=True,call_count=len(calls),tool_count=68,project_id=pid,revision=rev,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],bom=bom,check=good,draft=draft,preview=preview,source_project_unchanged=True,clear_test_project=clear['project_id'],checks=checks,new_gerber_generated=False,real_human_approval=False,ordered=False)
 (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['bom','check','draft','preview']},indent=2))
if __name__=='__main__':asyncio.run(main())
