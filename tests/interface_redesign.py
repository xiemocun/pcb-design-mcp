"""Actual MCP edits and KiCad pipeline for the split signal interface; no fabrication."""
import asyncio,copy,hashlib,json,os,sys,time,uuid
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/interface-redesign';sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts
SOURCE='8dbd5d951e35438e9d26c4561b4a068c'
async def main():
 start=time.monotonic();calls=[];frozen=parts.rules_hash();out=W/('run-'+uuid.uuid4().hex);out.mkdir();(W/'latest-run.txt').write_text(str(out))
 env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')}
 async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env=env)) as (reader,writer):
  async with ClientSession(reader,writer) as session:
   await session.initialize()
   async def call(name,args=None,error=None):
    t=time.monotonic();print(name,'START',flush=True)
    result=await session.call_tool(name,{} if args is None else {'request':args},read_timeout_seconds=timedelta(seconds=240))
    calls.append(dict(tool=name,arguments=args,seconds=round(time.monotonic()-t,3),result=result.model_dump(mode='json')));(out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    if error:
     assert result.isError and error in '\n'.join(x.text for x in result.content if hasattr(x,'text')),(name,result);return
    assert not result.isError,(name,result)
    print(name,'OK',calls[-1]['seconds'],flush=True);return result.structuredContent['data']
   original=await call('project_read',dict(project_id=SOURCE))
   fork=await call('project_fork',dict(project_id=SOURCE,expected_revision=original['revision'],name='AM2302 独立接收与开漏发送接口',idempotency_key=uuid.uuid4().hex));pid=fork['project_id'];rev=0
   (out/'project.json').write_text(json.dumps(fork))
   c={x['ref']:x for x in original['components'].values()};obsolete={c['U4']['id'],c['R7']['id']}
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   async def mutate(name,**kw):
    nonlocal rev
    v=await call(name,m(**kw));rev=v['revision'];return v
   # Remove explicit references atomically through the existing typed tools.
   ratings=copy.deepcopy(original['passive_rating_requirements']);ratings['requirements']=[x for x in ratings['requirements'] if x['component_id'] not in obsolete]
   await mutate('passive_ratings_set',**ratings)
   budget=copy.deepcopy(original['power_budget_policy']);budget['loads']=[x for x in budget['loads'] if x['pin']['component_id'] not in obsolete]
   await mutate('power_budget_set',**budget)
   for n in original['nets'].values():
    pins=[p for p in n['pins'] if p['component_id'] not in obsolete and p['component_id']!=c['R6']['id'] and not (p['component_id']==c['C8']['id'] and next(x['number'] for x in c['C8']['pins'] if x['pin_id']==p['pin_id'])=='1')]
    if pins!=n['pins']:await mutate('net_members_set',net_id=n['id'],pins=pins)
   state=await call('project_read',dict(project_id=pid))
   for n in state['nets'].values():
    if not n['pins']:await mutate('net_remove',net_id=n['id'])
   for ref in ['U4','R7']:
    for item in original['pin_intents'].values():
     if item['pin']['component_id']==c[ref]['id']:await mutate('pin_intent_set',pin=item['pin'],intent='unspecified',reason='Remove obsolete interface component')
    await mutate('part_unbind',component_id=c[ref]['id']);await mutate('footprint_unbind',component_id=c[ref]['id']);await mutate('component_remove',component_id=c[ref]['id'])
   # R6 now releases the open-drain driver at reset. Select a real 10k part.
   await mutate('part_unbind',component_id=c['R6']['id']);await mutate('component_parameters_set',component_id=c['R6']['id'],value='10k')
   found=await call('parts_search',dict(query='CRCW060310K0FKEA'));part=await call('part_get',dict(candidate_id=found['candidates'][0]['candidate_id']));await mutate('part_bind',component_id=c['R6']['id'],candidate_id=part['candidate_id'])
   added={}
   for ref,mpn,symbol in [('U4','SN74LVC1G17DBVR','74LVC1G17'),('U9','SN74LVC1G07DBVR','74LVC1G07')]:
    found=await call('library_search',dict(query='74xGxx:'+symbol));assert found['total']==1
    sym=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']));comp=await mutate('component_add',candidate_id=sym['candidate_id'],ref=ref,value=symbol);added[ref]=comp
    found=await call('parts_search',dict(query=mpn));part=await call('part_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
    found=await call('footprint_search',dict(query=part['footprint_id']));fp=await call('footprint_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
    await mutate('footprint_bind',component_id=comp['component_id'],candidate_id=fp['candidate_id'],reason='Manufacturer DBV source pin table paired with actual SOT-23-5 footprint')
    await mutate('part_bind',component_id=comp['component_id'],candidate_id=part['candidate_id'])
    nc=dict(component_id=comp['component_id'],pin_id=next(p['pin_id'] for p in comp['pins'] if p['number']=='1'))
    await mutate('pin_intent_set',pin=nc,intent='no_connect',reason='Manufacturer DBV pin1 is NC')
   s=await call('project_read',dict(project_id=pid));nets={n['name']:n for n in s['nets'].values()}
   # Reuse the old IO26 net ID only as a transport detail; requirements define its new drive meaning.
   def pin(ref,number):
    item=added[ref] if ref in added else dict(component_id=c[ref]['id'],pins=c[ref]['pins'])
    return dict(component_id=item['component_id'],pin_id=next(p['pin_id'] for p in item['pins'] if p['number']==number))
   extras={'V3V3':[pin('U4','5'),pin('U9','5'),pin('C8','1'),pin('R6','1')],
           'GND':[pin('U4','3'),pin('U9','3')],
           'SENSOR_DATA':[pin('U4','2'),pin('U9','4')],
           'MCU_SENSOR_DATA':[pin('U4','4')],
           'SENSOR_OE':[pin('U9','2'),pin('R6','2')]}
   for name,pins in extras.items():await mutate('net_members_set',net_id=nets[name]['id'],pins=nets[name]['pins']+pins)
   # Resistor rating requirement follows the selected part; old 100k assumption is replaced.
   for req in ratings['requirements']:
    if req['component_id']==c['R6']['id']:req['max_tolerance_fraction']=.01
   await mutate('passive_ratings_set',**ratings)
   for ref in ['U4','U9']:
    budget['loads'].append(dict(pin=pin(ref,'5'),max_current_ma=1.,scope='supply_terminal_dc_including_io_drive'))
   await mutate('power_budget_set',**budget)
   requirements=[x for x in original['requirements'] if x['key'] not in ['level_translation','firmware_oe']]
   requirements +=[dict(key='sensor_interface',value='AM2302 bus -> SN74LVC1G17 -> IO27; IO26 -> SN74LVC1G07 open-drain -> bus; R6 10k release pullup. SENSOR_OE legacy net name now denotes drive signal.',origin='proposed_assumption'),dict(key='firmware_interface',value='GPIO26=1 releases bus,0 asserts LOW; GPIO27 receives; wait2500ms after power-up and between samples; output checksum-validated temperature/humidity JSON on UART0.',origin='proposed_assumption')]
   await mutate('requirements_set',items=requirements)
   spec=await call('spec_validate',dict(project_id=pid));assert spec['verdict']=='pass',spec
   module=await call('module_constraints_check',dict(project_id=pid));assert module['verdict']=='pass',module
   assert len(module['modules'])==1 and module['modules'][0]['kind']=='split_open_drain'
   budget_report=await call('power_budget_check',dict(project_id=pid));assert budget_report['budget_verdict']=='conditional',budget_report
   bom=await call('bom_check',dict(project_id=pid));assert bom['identity_verdict']=='pass' and bom['counts']['identity_checked']==32,bom
   state=await call('project_read',dict(project_id=pid))
   (out/'state.json').write_text(json.dumps(state,ensure_ascii=False,indent=2))
   sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id'])
   assert (await call('schematic_verify',st))['verdict']=='pass'
   erc=await call('erc_run',st);assert erc['verdict']=='pass',erc
   await call('schematic_preview',st);await call('schematic_export',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
   assert (await call('pcb_verify',dict(**pt,stage='unrouted')))['verdict']=='pass'
   assert (await call('pcb_layout_check',dict(**pt,stage='unrouted')))['verdict']=='pass'
   await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass'
   drc=await call('drc_run',pt)
   for retry in range(2):
    if drc['verdict']=='pass':break
    assert all(f['type'] in ['unconnected_items','via_dangling'] for f in drc['findings']),drc
    pcb=await call('pcb_route_retry_prepare',m(pcb_build_id=pt['pcb_build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
    assert (await call('pcb_verify',dict(**pt,stage='unrouted')))['verdict']=='pass'
    assert (await call('pcb_layout_check',dict(**pt,stage='unrouted')))['verdict']=='pass'
    await call('pcb_autoroute',pt);assert (await call('pcb_verify',pt))['verdict']=='pass'
    drc=await call('drc_run',pt)
   assert drc['verdict']=='pass',drc
   assert (await call('pcb_layout_check',pt))['verdict']=='pass'
   copper=await call('pcb_copper_check',pt);assert copper['geometry_verdict']=='pass' and not any(f['severity']=='error' for f in copper['findings']),copper
   preview=await call('pcb_preview',pt);bundle=await call('pcb_review_export',pt)
   await call('bom_export',dict(project_id=pid))
   assert parts.rules_hash()==frozen,'Source changed during acceptance'
   for name,sha in json.loads((W/'baseline/states.json').read_text()).items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==sha,name
   row=module['modules'][0];gpio=lambda name:int(row[name].replace('IO',''))
   header=f'/* Generated from verified MCP project {pid} revision {rev}. */\n#define DRIVE_GPIO GPIO_NUM_{gpio("drive_gpio")}\n#define READ_GPIO GPIO_NUM_{gpio("read_gpio")}\n'
   (R/'firmware/am2302/src/board_pins.h').write_text(header)
   (R/'firmware/am2302/hardware.json').write_text(json.dumps(dict(project_id=pid,revision=rev,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],read_gpio=gpio('read_gpio'),drive_gpio=gpio('drive_gpio'),drive_release_level=1,interface=row,source_evidence_hash=frozen,physical_verified=False),ensure_ascii=False,indent=2))
   result=dict(passed=True,project_id=pid,revision=rev,source_evidence_hash=frozen,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],interface=module,budget=budget_report,preview=preview,bundle=bundle,copper=copper,erc=erc,drc=drc,call_count=len(calls),elapsed_seconds=round(time.monotonic()-start,2),original_projects_unchanged=True,physical_verified=False,new_gerber_generated=False)
   (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(W/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ['interface','budget','preview','bundle','copper','erc','drc']},ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
