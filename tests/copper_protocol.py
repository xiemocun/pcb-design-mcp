"""Real stdio MCP: reject thin power copper, rebuild with per-net rules."""
import asyncio,json,os,shutil,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];OLD=R.parent/'pcb-design-mcp-v10'

async def main():
 out=R/'acceptance-copper';out.mkdir(exist_ok=True);data=out/('run-'+uuid.uuid4().hex);shutil.copytree(R/'data',data)
 prior=json.loads((OLD/'acceptance-usb-interlock/report.json').read_text());calls=[];checks=[]
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})) as (r,w):
  async with ClientSession(r,w) as session:
   await session.initialize();contract=await session.list_tools();assert len(contract.tools)==68
   (R/'tool-contract.json').write_text(json.dumps(contract.model_dump(mode='json'),ensure_ascii=False,indent=2))
   async def call(name,args=None,error=None):
    result=await session.call_tool(name,{} if args is None else {'request':args});calls.append(dict(tool=name,arguments=args,result=result.model_dump(mode='json')))
    (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    if error:
     assert result.isError and error in '\n'.join(c.text for c in result.content if hasattr(c,'text')),(name,error,result);checks.append(error);return
    assert not result.isError,(name,result);return result.structuredContent['data']
   assert (await call('system_capabilities'))['contract_version']=='0.12.0'
   source=await call('project_read',dict(project_id=prior['project_id']));nets={n['name']:n for n in source['nets'].values()}
   f=await call('project_fork',dict(project_id=source['id'],expected_revision=source['revision'],name='供电细线反例：声明规则下的参考筛选',idempotency_key=uuid.uuid4().hex));pid=f['project_id'];rev=0
   currents={'VBUS_RAW':1.05,'VUSB':.95,'V3V3':1.0,'GND':1.2,'VCC_AUX':.05}
   rules=[dict(net_id=n['id'],min_width_mm=.15,design_current_a=currents[n['name']],reference_drop_budget_v=.1) for n in nets.values() if n['kind'] in ['power','ground']]
   def request(rules):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,rules=rules,copper_thickness_um=35.0,temperature_rise_c=20.0,origin='proposed_assumption',reason='Declared geometry/calculator screen only. Thickness and all currents are unverified design assumptions; no physical or manufacturing approval.')
   await call('pcb_net_rules_set',request([{**rules[0],'net_id':'0'*32}]),'UNKNOWN_NET')
   signal=next(n for n in nets.values() if n['kind']=='signal')
   await call('pcb_net_rules_set',request([{**rules[0],'net_id':signal['id']}]),'COPPER_RULE_INVALID')
   await call('pcb_net_rules_set',request([rules[0],rules[0]]),'DUPLICATE_NET_RULE')
   await call('pcb_net_rules_set',{**request(rules),'approved':True},'extra_forbidden')
   await call('pcb_net_rules_set',{**request(rules),'origin':'verified_measurement'},'literal_error')
   rev=(await call('pcb_net_rules_set',request(rules)))['revision']
   async def build():
    assert (await call('spec_validate',dict(project_id=pid)))['verdict']=='pass'
    sch=await call('schematic_build',dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex));st=dict(project_id=pid,build_id=sch['build_id'])
    assert (await call('schematic_verify',st))['verdict']=='pass';assert (await call('erc_run',st))['verdict']=='pass';await call('schematic_preview',st);se=await call('schematic_export',st)
    pcb=await call('pcb_build',dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
    assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass';assert (await call('pcb_layout_check',{**pt,'stage':'unrouted'}))['verdict']=='pass'
    await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass'
    drc=await call('drc_run',pt)
    if drc['verdict']=='fail':
     assert all(f['type'] in ['unconnected_items','via_dangling'] for f in drc['findings']),drc
     previous=dict(pt);retry=dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,pcb_build_id=pt['pcb_build_id'])
     repaired=await call('pcb_route_retry_prepare',retry);assert (await call('pcb_route_retry_prepare',retry))==repaired
     pcb=repaired;pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
     assert (await call('pcb_verify',{**pt,'stage':'unrouted'}))['verdict']=='pass';assert (await call('pcb_layout_check',{**pt,'stage':'unrouted'}))['verdict']=='pass'
     await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass';drc=await call('drc_run',pt);checks.append('Fresh native DRC feedback -> immutable seeded retry -> real reroute and recheck')
    assert drc['verdict']=='pass',drc
    assert (await call('pcb_layout_check',pt))['verdict']=='pass'
    preview=await call('pcb_preview',pt);export=await call('pcb_review_export',pt)
    report=await call('pcb_copper_check',pt)
    return dict(project_id=pid,revision=rev,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],copper=report,preview=preview,export=export,schematic_export=se)
   thin=await build();assert thin['copper']['verdict']=='fail'
   assert any(f['code']=='COPPER_CURRENT_REFERENCE_LIMIT' for f in thin['copper']['findings'])
   assert thin['copper']['geometry_verdict']=='pass' # width=.15 is explicit, reference current is inadequate
   f=await call('project_fork',dict(project_id=pid,expected_revision=rev,name='独立电源网络线宽：0.30mm 参考改进',idempotency_key=uuid.uuid4().hex));pid=f['project_id'];rev=0
   rules=[{**r,'min_width_mm':.15 if r['net_id']==nets['VCC_AUX']['id'] else .3,**(dict(escape_profile='tusb320_gpio_ground_v1',escape_design_current_a=.05) if r['net_id']==nets['GND']['id'] else {})} for r in rules]
   rev=(await call('pcb_net_rules_set',request(rules)))['revision']
   await call('pcb_copper_check',dict(project_id=pid,pcb_build_id=thin['pcb_build_id']),'UNKNOWN_PCB_BUILD')
   wide=await build();c=wide['copper'];assert c['verdict']=='incomplete' and c['geometry_verdict']=='pass',c
   assert not any(f['code']=='COPPER_CURRENT_REFERENCE_LIMIT' for f in c['findings'])
   assert all(row['track_reference']['current_reference_satisfied'] for row in c['nets'])
   assert not c['thermal_qualified'] and not c['full_path_drop_qualified'] and not c['manufacturing_approved']
   await call('pcb_copper_check',{**dict(project_id=pid,pcb_build_id=wide['pcb_build_id']),'approved':True},'extra_forbidden')
   pt=dict(project_id=pid,pcb_build_id=wide['pcb_build_id']);gate=await call('usb_c_gate_scenarios',dict(project_id=pid));assert gate['verdict']=='pass'
   audit=await call('design_audit',pt);assert audit['status']=='blocked';assert next(s for s in audit['stages'] if s['check_id']=='ACTUAL_COPPER')['status']=='incomplete'
   await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED');await call('bom_export',dict(project_id=pid),'BOM_PART_MISSING')
   final=await call('project_read',dict(project_id=pid));assert len(final['components'])==32 and len(final['nets'])==19
 for case in [thin,wide]:
  demo=R/('demo-copper-'+('thin' if case is thin else 'wide'));demo.mkdir(exist_ok=True)
  pcb=data/case['project_id']/'pcb_builds'/case['pcb_build_id'];sch=data/case['project_id']/'builds'/case['schematic_build_id']
  for folder,names in [(pcb,['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','fp-lib-table','input.dsn','dsn_rules.json','copper_report.json','copper_geometry.json','drc_report.json','layout_routed.json','pcb_review_bundle.zip']),(sch,['led.kicad_sch','actual.xml','erc.json','verify.json','spec.json','review_bundle.zip'])]:
   for name in names:shutil.copy2(folder/name,demo/name)
  shutil.copy2(case['preview']['preview_path'],demo/'pcb.svg')
 for key,name in [('json_path','audit.json'),('report_path','检查报告.md')]:shutil.copy2(audit[key],R/'demo-copper-wide'/name)
 result=dict(passed=True,call_count=len(calls),tool_count=68,data_root=str(data),project_id=wide['project_id'],revision=wide['revision'],schematic_build_id=wide['schematic_build_id'],pcb_build_id=wide['pcb_build_id'],thin=thin,wide=wide,audit=audit,checks=checks,new_gerber_generated=False,real_human_approval=False,ordered=False)
 (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['thin','wide','audit']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
