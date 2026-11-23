"""Actual stdio: source-linked 30/32 identities, ratings, fresh native PCB checks."""
import asyncio,copy,hashlib,json,os,shutil,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v11'
async def main():
 out=R/'acceptance-bom';out.mkdir(exist_ok=True);data=out/('run-'+uuid.uuid4().hex);shutil.copytree(R/'data',data);calls=[];checks=[]
 prior=json.loads((OLD/'acceptance-copper/report.json').read_text());source_id=prior['project_id'];before=(data/source_id/'state.json').read_bytes()
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();contract=await session.list_tools();assert len(contract.tools)==68
   (R/'tool-contract.json').write_text(json.dumps(contract.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,req=None,error=None):
    v=await session.call_tool(name,{} if req is None else {'request':req});calls.append(dict(tool=name,request=req,result=v.model_dump(mode='json')))
    (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    if error:
     assert v.isError and error in '\n'.join(c.text for c in v.content if hasattr(c,'text')),(name,error,v);checks.append(error);return
    assert not v.isError,(name,v);return v.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.12.0'
   empty=await call('bom_check',dict(project_id=source_id));assert empty['counts']['missing']==32 and empty['verdict']=='incomplete'
   f=await call('project_fork',dict(project_id=source_id,expected_revision=prior['revision'],name='实物BOM：30个来源绑定、2个明确缺口',idempotency_key=uuid.uuid4().hex));pid=f['project_id'];rev=0
   st=await call('project_read',dict(project_id=pid));comps={c['ref']:c for c in st['components'].values()}
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   mapping=dict(U1='ESP32-WROOM-32E-N4',U2='AM2302',U3='R-78K3.3-1.0',U4='TXS0102DCTR',U5='TUSB320LAIRWBR',U6='SN74LVC1G14DBVR',U7='TPS2553DBVR',U8='TLV75533PDBVR',J1='USB4105-GF-A')
   codes={'10k':'10K0','100k':'100K','100':'100R','887k':'887K','30.1k':'30K1'}
   capcodes={'100nF':'C1608X7R1H104K080AA','1uF':'C1608X5R1C105K080AA','10uF':'C2012X5R1V106K125AC'}
   for ref,c in comps.items():
    if c['library_id']=='Device:R':mapping[ref]='CRCW0603'+codes[c['value']]+'FKEA'
    elif c['library_id']=='Device:C':mapping[ref]=capcodes[c['value']]
   assert len(mapping)==30
   resolved={}
   for mpn in sorted(set(mapping.values())):
    row=await call('parts_search',dict(query=mpn));assert row['total']==1,row;candidate=row['candidates'][0]
    if candidate['kind']=='capacitor':
     await call('part_bind',m(component_id=comps['C1']['id'],candidate_id=candidate['candidate_id']),'PART_NOT_RESOLVED')
    resolved[mpn]=await call('part_get',dict(candidate_id=candidate['candidate_id']))
   cap=resolved[capcodes['100nF']]
   await call('part_bind',m(component_id=comps['C1']['id'],candidate_id=cap['candidate_id']),'PART_BINDING_MISMATCH')
   cap=resolved[capcodes['10uF']]
   await call('part_bind',m(component_id=comps['C1']['id'],candidate_id=cap['candidate_id']),'PART_BINDING_MISMATCH')
   for ref,mpn in mapping.items():rev=(await call('part_bind',m(component_id=comps[ref]['id'],candidate_id=resolved[mpn]['candidate_id'])))['revision']
   b=await call('bom_check',dict(project_id=pid));assert b['counts']['identity_checked']==30 and b['counts']['missing']==2
   assert {row['reference'] for row in b['rows'] if row['identity_status']=='missing'}=={'J2','C9'}
   assert b['declared_rating_screen']=='incomplete';assert all(not row['physical_qualified'] for row in b['rows'])
   await call('component_parameters_set',m(component_id=comps['C1']['id'],value='100nF'),'PART_BOUND_VALUE_LOCKED')
   requirements=[]
   for c in comps.values():
    if c['library_id'] in ['Device:R','Device:C']:
     req=dict(component_id=c['id'],max_applied_voltage_v=5.5,max_tolerance_fraction=.01 if c['library_id']=='Device:R' else .1)
     req.update(dict(min_resistor_rated_power_w=.1) if c['library_id']=='Device:R' else dict(allowed_dielectrics=['X7R','X5R']));requirements.append(req)
   def rating_request(reqs,**kw):return m(requirements=reqs,origin='proposed_assumption',reason='Conservative declared 5.5V screening envelope, catalog tolerance and minimum nominal rating only; not solved circuit stress, measured voltage, effective capacitance or temperature.',**kw)
   await call('passive_ratings_set',rating_request([requirements[0],requirements[0]]),'DUPLICATE_RATING_REQUIREMENT')
   await call('passive_ratings_set',rating_request([{**requirements[0],'component_id':'0'*32}]),'UNKNOWN_COMPONENT')
   await call('passive_ratings_set',rating_request([{**requirements[0],'component_id':comps['U1']['id']}]),'RATING_ROLE_UNSUPPORTED')
   await call('passive_ratings_set',rating_request(requirements,approved=True),'extra_forbidden')
   req=rating_request(requirements);await call('passive_ratings_set',{**req,'origin':'verified_measurement'},'literal_error')
   wrong=copy.deepcopy(requirements);next(q for q in wrong if q['component_id']==comps['C1']['id'])['max_applied_voltage_v']=20.0
   rev=(await call('passive_ratings_set',rating_request(wrong)))['revision']
   bad=await call('bom_check',dict(project_id=pid));assert bad['declared_rating_screen']=='fail'
   assert (await call('spec_validate',dict(project_id=pid)))['verdict']=='fail'
   await call('schematic_build',m(),'SPEC_INVALID')
   wrong=copy.deepcopy(requirements);next(q for q in wrong if q['component_id']==comps['C1']['id'])['allowed_dielectrics']=['C0G']
   rev=(await call('passive_ratings_set',rating_request(wrong)))['revision'];assert (await call('bom_check',dict(project_id=pid)))['declared_rating_screen']=='fail'
   wrong=copy.deepcopy(requirements);next(q for q in wrong if q['component_id']==comps['R8']['id'])['max_tolerance_fraction']=.001
   rev=(await call('passive_ratings_set',rating_request(wrong)))['revision'];assert (await call('bom_check',dict(project_id=pid)))['declared_rating_screen']=='fail'
   req=rating_request(requirements);rev=(await call('passive_ratings_set',req))['revision'];assert (await call('passive_ratings_set',req))['revision']==rev
   await call('bom_check',dict(project_id=pid,revision=0),'REVISION_CONFLICT')
   good=await call('bom_check',dict(project_id=pid));assert good['declared_rating_screen']=='conditional' and good['counts']['passive_screened']==21
   assert good['verdict']=='incomplete' and not good['physical_qualified'] and not good['manufacturing_approved']
   assert len([f for f in good['findings'] if f['code']=='EFFECTIVE_CAPACITANCE_UNQUALIFIED'])==12
   draft=await call('bom_draft_export',dict(project_id=pid));assert len(draft['report']['rows'])==32
   assert hashlib.sha256(Path(draft['json_path']).read_bytes()).hexdigest()==draft['json_sha256']
   await call('bom_export',dict(project_id=pid),'BOM_PART_MISSING')
   assert (await call('engineering_check',dict(project_id=pid)))['verdict']!='pass'
   ready=await call('design_readiness',dict(project_id=pid));assert ready['status']=='blocked'
   assert (await call('spec_validate',dict(project_id=pid)))['verdict']=='pass'
   sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id'])
   assert (await call('schematic_verify',st))['verdict']=='pass';assert (await call('erc_run',st))['verdict']=='pass';await call('schematic_preview',st);await call('schematic_export',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
   assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass';assert (await call('pcb_layout_check',{**pt,'stage':'unrouted'}))['verdict']=='pass'
   await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt)
   retries=0
   while drc['verdict']=='fail' and retries<2:
    assert all(f['type'] in ['unconnected_items','via_dangling'] for f in drc['findings']),drc
    pcb=await call('pcb_route_retry_prepare',m(pcb_build_id=pt['pcb_build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id']);retries+=1
    assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass';assert (await call('pcb_layout_check',{**pt,'stage':'unrouted'}))['verdict']=='pass'
    await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt)
   assert drc['verdict']=='pass',drc
   assert (await call('pcb_layout_check',pt))['verdict']=='pass';copper=await call('pcb_copper_check',pt);assert copper['verdict']=='incomplete'
   preview=await call('pcb_preview',pt);await call('pcb_review_export',pt)
   audit=await call('design_audit',pt);assert audit['status']=='blocked'
   await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED')
   assert (data/source_id/'state.json').read_bytes()==before
 report=dict(passed=True,call_count=len(calls),tool_count=68,data_root=str(data),project_id=pid,revision=rev,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],retries=retries,bom=good,draft=draft,preview=preview,audit=audit,checks=checks,new_gerber_generated=False,real_human_approval=False,ordered=False)
 (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k not in ['bom','draft','preview','audit']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
