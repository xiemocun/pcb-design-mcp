"""Real MCP fail-closed power, pin-source, readiness and artifact tests."""
import asyncio,json,os,sys,uuid,shutil
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]

async def run():
    original=json.loads((ROOT/'acceptance-esp32-reference/report.json').read_text())
    out=ROOT/'acceptance-design-gates';out.mkdir(exist_ok=True)
    data=out/('run-'+uuid.uuid4().hex);shutil.copytree(original['data_root'],data)
    calls=[];checks=[]
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(params) as (r,w):
      async with ClientSession(r,w) as session:
        await session.initialize();tools=await session.list_tools();assert len(tools.tools)==68
        (ROOT/'tool-contract.json').write_text(json.dumps(tools.model_dump(mode='json'),ensure_ascii=False,indent=2))
        async def call(name,req=None,error=None):
            result=await session.call_tool(name,{} if req is None else {'request':req})
            calls.append(dict(tool=name,arguments=req,result=result.model_dump(mode='json')))
            (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
            if error:
                assert result.isError,(name,result);assert error in '\n'.join(x.text for x in result.content if hasattr(x,'text')),result
                checks.append(error);return
            assert not result.isError,(name,result);return result.structuredContent['data']
        pid=original['project_id'];s=await call('project_read',dict(project_id=pid));rev=s['revision']
        def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
        source=next(iter(s['power_sources'].values()))
        envelope=dict(positive_pin=source['boundary_pin'],ground_net_id=source['ground_net_id'],nominal_v=5.0,min_v=4.75,max_v=5.25,available_current_ma=500.0,ambient_c=25.0,reason='Deliberate declared supply envelope for isolated failure tests',origin='proposed_assumption')
        for mpn,kind in [('ESP32-WROOM-32E-N4','mcu_module'),('AM2302','sensor'),('TLV75533PDBVR','regulator')]:
            candidates=await call('parts_search',dict(query=mpn,kind=kind));assert candidates['total']==1
            part=await call('part_get',dict(candidate_id=candidates['candidates'][0]['candidate_id']));assert part['evidence_status']=='available'
        await call('power_envelope_set',m(**{**envelope,'min_v':5.5}),'INVALID_SUPPLY_RANGE')
        await call('power_envelope_set',m(**{**envelope,'approved':True}),'extra_forbidden')
        invalidpin={**envelope['positive_pin'],'pin_id':'0'*32}
        await call('power_envelope_set',m(**{**envelope,'positive_pin':invalidpin}),'INVALID_POWER_BOUNDARY')
        rev=(await call('power_envelope_set',m(**envelope)))['revision']
        power=await call('power_integrity_check',dict(project_id=pid));codes={f['code'] for f in power['findings']}
        assert power['verdict']=='fail' and {'REGULATOR_CURRENT_CAPACITY','SOURCE_CURRENT_BUDGET','SUPPLY_ENVELOPE_OUT_OF_RANGE','REFERENCE_THERMAL_BUDGET_EXCEEDED','USB_CURRENT_CAPABILITY_UNVERIFIED'}<=codes,power
        invalid=await call('spec_validate',dict(project_id=pid));assert invalid['verdict']=='fail' and 'SUPPLY_ENVELOPE_OUT_OF_RANGE' in {f['code'] for f in invalid['findings']}
        await call('schematic_build',m(),'SPEC_INVALID')
        await call('pcb_build',m(schematic_build_id=original['pcb'].get('schematic_build_id','0'*32)),'PCB_SPEC_INVALID')
        readiness=await call('design_readiness',dict(project_id=pid));assert readiness['status']=='blocked' and not readiness['manufacturing_approved']
        await call('project_read',dict(project_id=pid,revision=rev-1),'REVISION_CONFLICT')
        # A changed upstream declaration cannot retain the former interval.
        rev=(await call('power_source_set',m(source_id=source['id'],net_id=source['net_id'],ground_net_id=source['ground_net_id'],boundary_pin=source['boundary_pin'],nominal_v=9.0,reason='Deliberate stale-envelope and unsupported PD test')))['revision']
        assert any(f['code']=='SUPPLY_ENVELOPE_BOUNDARY_STALE' for f in (await call('power_integrity_check',dict(project_id=pid)))['findings'])
        # Old library discrepancy is a hard source conflict; corrected NC cannot be wired.
        auxiliary=await call('project_create',dict(name='Isolated pin-source conflict test'));pid=auxiliary['project_id'];rev=auxiliary['revision']
        await call('power_envelope_set',m(**envelope),'POWER_PROFILE_UNSUPPORTED')
        found=await call('library_search',dict(query='Sensor:AM2302'));legacy=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
        rev=(await call('component_add',m(candidate_id=legacy['candidate_id'],ref='U1',value=legacy['symbol'])))['revision']
        invalid=await call('spec_validate',dict(project_id=pid));assert any(f['code']=='SYMBOL_SOURCE_PIN_CONFLICT' for f in invalid['findings'])
        await call('schematic_build',m(),'SPEC_INVALID')
        found=await call('library_search',dict(query='PCB_MCP:AM2302_SIP4'));corrected=await call('library_get',dict(candidate_id=found['candidates'][0]['candidate_id']))
        c=await call('component_add',m(candidate_id=corrected['candidate_id'],ref='U2',value=corrected['symbol']));rev=c['revision']
        nc=next(p for p in c['pins'] if p['number']=='3');assert nc['electrical_type']=='no_connect'
        n=await call('net_create',m(name='INVALID_NC_GROUND',kind='ground'));rev=n['revision']
        await call('net_members_set',m(net_id=n['net_id'],pins=[dict(component_id=c['component_id'],pin_id=nc['pin_id'])]),'PIN_FUNCTION_NO_CONNECT')
        checks+=['Source-grounded MPN families and corrected sensor pins','No rating/approved override; invalid envelopes and boundaries fail','Supply interval/LDO minimum capacity/reference thermal/USB evidence fail before actual schematic/PCB generation','Readiness remains blocked; stale context and revisions fail']
    report=dict(passed=True,call_count=len(calls),checks=checks,data_root=str(data),no_gerber_generated=not any(data.rglob('*.gbr')),real_human_approval=False)
    assert report['no_gerber_generated'];(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(run())
