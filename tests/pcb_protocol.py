"""Actual MCP physical pipeline and failure gates. No mock routing or DRC."""
import asyncio,json,os,sys,uuid,shutil
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]

async def run():
    out=ROOT/'acceptance-pcb';out.mkdir(exist_ok=True)
    data=out/('run-'+uuid.uuid4().hex);calls=[];checks=[]
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(params) as (r,w):
      async with ClientSession(r,w) as s:
        await s.initialize();tools=await s.list_tools();assert len(tools.tools)==62
        (out/'tools.json').write_text(json.dumps(tools.model_dump(mode='json'),ensure_ascii=False,indent=2))
        async def call(name,args=None,error=None):
            response=await asyncio.wait_for(s.call_tool(name,{} if args is None else {'request':args}),145)
            calls.append(dict(tool=name,arguments=args,result=response.model_dump(mode='json')))
            (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
            if error:
                assert response.isError,(name,response)
                assert error in '\n'.join(c.text for c in response.content if hasattr(c,'text')),response
                checks.append(error);return None
            assert not response.isError,(name,response)
            return response.structuredContent['data']
        p=await call('project_create',{'name':'470 ohm LED PCB physical experiment'})
        pid=p['project_id'];rev=p['revision']
        def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
        comps={}
        for symbol,ref,value in [('Connector_Generic:Conn_01x02','J1','Conn_01x02'),('Device:R','R1','470'),('Device:LED','D1','LED')]:
            found=await call('library_search',{'query':symbol});c=await call('library_get',{'candidate_id':found['candidates'][0]['candidate_id']})
            comp=await call('component_add',m(candidate_id=c['candidate_id'],ref=ref,value=value));rev=comp['revision'];comps[ref]=comp
        def pin(ref,num):return dict(component_id=comps[ref]['component_id'],pin_id=next(x['pin_id'] for x in comps[ref]['pins'] if x['number']==num))
        for name,kind,members in [('VCC','power',[pin('J1','1'),pin('R1','1')]),('LED_A','signal',[pin('R1','2'),pin('D1','2')]),('GND','ground',[pin('D1','1'),pin('J1','2')])]:
            n=await call('net_create',m(name=name,kind=kind));rev=n['revision']
            rev=(await call('net_members_set',m(net_id=n['net_id'],pins=members)))['revision']
        pre=await call('pcb_preflight',{'project_id':pid});assert pre['verdict']=='fail'
        await call('footprint_get',{'candidate_id':'0'*32},'UNKNOWN_FOOTPRINT')
        fps={}
        for ref,ident in [('R1','Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal'),('D1','LED_THT:LED_D3.0mm'),('J1','Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical')]:
            found=await call('footprint_search',{'query':ident});assert found['total']==1,found
            fp=found['candidates'][0]
            await call('footprint_bind',m(component_id=comps[ref]['component_id'],candidate_id=fp['candidate_id'],reason='Geometry experiment from local library; physical MPN not qualified'),'FOOTPRINT_NOT_RESOLVED')
            fps[ref]=await call('footprint_get',{'candidate_id':fp['candidate_id']})
        await call('footprint_bind',m(component_id=comps['R1']['component_id'],candidate_id=fps['D1']['candidate_id'],reason='Deliberate wrong role test'),'FOOTPRINT_ROLE_MISMATCH')
        wrong=await call('footprint_search',{'query':'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical'})
        wrong=await call('footprint_get',{'candidate_id':wrong['candidates'][0]['candidate_id']})
        await call('footprint_bind',m(component_id=comps['J1']['component_id'],candidate_id=wrong['candidate_id'],reason='Deliberate pad count mismatch'),'PAD_PIN_MISMATCH')
        for ref,fp in fps.items():rev=(await call('footprint_bind',m(component_id=comps[ref]['component_id'],candidate_id=fp['candidate_id'],reason='Geometry experiment from local library; physical MPN not qualified')))['revision']
        rev=(await call('board_constraints_set',m()))['revision']
        pre=await call('pcb_preflight',{'project_id':pid});assert pre['verdict']=='pass',pre
        check=await call('spec_validate',{'project_id':pid});assert check['verdict']=='pass',check
        built=await call('schematic_build',m());sch=dict(project_id=pid,build_id=built['build_id'])
        assert (await call('schematic_verify',sch))['verdict']=='pass'
        assert (await call('erc_run',sch))['verdict']=='pass'
        await call('schematic_preview',sch);export=await call('schematic_export',sch)
        req=m(schematic_build_id=built['build_id']);pcb=await call('pcb_build',req);assert await call('pcb_build',req)==pcb
        target=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
        await call('pcb_autoroute',target,'PCB_VERIFICATION_REQUIRED')
        await call('pcb_review_export',target,'PCB_VALIDATION_REQUIRED')
        await call('drc_run',target,'ROUTING_REQUIRED')
        check=await call('pcb_verify',{**target,'stage':'unrouted'});assert check['verdict']=='pass',check
        checks.append('Actual un-routed board matches full footprint IDs, values, all pads and nets, exact outline')
        folder=data/pid/'pcb_builds'/pcb['pcb_build_id']
        # Independent DRC of unrouted board must detect the missing copper.
        import subprocess
        cli='/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
        drc=subprocess.run([cli,'pcb','drc','--format','json','--all-track-errors','--severity-all','--exit-code-violations','-o','negative_unrouted_drc.json','unrouted.kicad_pcb'],cwd=folder,capture_output=True,text=True,timeout=45)
        raw=json.loads((folder/'negative_unrouted_drc.json').read_text());assert drc.returncode==5 and raw['unconnected_items'],raw
        checks.append('Independent KiCad DRC rejects unrouted board with unconnected pads')
        (out/'pending.json').write_text(json.dumps(dict(data=str(data),project_id=pid,revision=rev,pcb_build_id=pcb['pcb_build_id'],schematic_build_id=built['build_id']),indent=2))
        await call('pcb_autoroute',target)
        assert (await call('pcb_verify',target))['verdict']=='pass'
        drc=await call('drc_run',target);assert drc['verdict']=='pass',drc
        preview=await call('pcb_preview',target)
        exported=await call('pcb_review_export',target)
        resource=await s.read_resource(preview['resource_uri']);assert '<svg' in resource.contents[0].text
        snapshot=ROOT/'successful-pcb-data';shutil.copytree(data,snapshot,dirs_exist_ok=True)
        checks.append('Local Java Freerouting SES imported by KiCad; actual DRC zero violations and zero unconnected')
        demo=ROOT/'demo-pcb';demo.mkdir(exist_ok=True)
        for name in ['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','drc.json','drc_report.json','manifest.json','pcb_review_bundle.zip','input.dsn','output.ses']:
            shutil.copy2(folder/name,demo/name)
        shutil.copy2(preview['preview_path'],demo/'preview.svg')
        shutil.copy2(export['schematic_path'],demo/'schematic.kicad_sch')
        for name in ['routed.kicad_dru','routed.kicad_pcb','drc_report.json']:
            path=folder/name;original=path.read_bytes();path.write_bytes(original+b'\n')
            await call('pcb_review_export',target,'ARTIFACT_CHANGED');path.write_bytes(original)
        await call('pcb_autoroute',target,'ROUTE_ALREADY_EXISTS')
        rev=(await call('component_parameters_set',m(component_id=comps['R1']['component_id'],value='510')))['revision']
        await call('pcb_review_export',target,'STALE_VALIDATION')
        # Restore requested 470 ohm value, then rebuild required; old checks stay stale.
        rev=(await call('component_parameters_set',m(component_id=comps['R1']['component_id'],value='470')))['revision']
        report=dict(passed=True,call_count=len(calls),checks=checks,project_id=pid,build=pcb,review_bundle=exported['bundle_path'],limitations='Geometry and connectivity experiment; MPN fit, function and manufacturing approval unsupported')
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(run())
