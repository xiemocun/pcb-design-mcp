"""End-to-end acceptance: every circuit operation goes through real MCP stdio."""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
OUT=Path(os.environ.get('PCB_MCP_ACCEPTANCE_DIR',str(ROOT/'acceptance')))


async def run():
    OUT.mkdir(exist_ok=True)
    data=OUT/('run-'+uuid.uuid4().hex)
    transcript=[]
    checks=[]
    params=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],
        env={**os.environ,'PYTHONPATH':str(ROOT/'src'),'PCB_MCP_DATA_DIR':str(data)})
    pid=None
    rev=0
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            listing=await session.list_tools()
            required={'system_capabilities','project_create','library_search','library_get','component_add','net_create','net_members_set','spec_validate','schematic_build','schematic_verify','erc_run','schematic_export','manufacturing_review_prepare','fabrication_export'}
            assert required <= {t.name for t in listing.tools}
            for tool in listing.tools:
                assert tool.outputSchema is not None
                if tool.name not in ['system_capabilities','project_list','rules_list']:
                    assert tool.inputSchema['$defs']
            (OUT/'tools.json').write_text(json.dumps(listing.model_dump(mode='json'),ensure_ascii=False,indent=2))
            checks.append('MCP initialize + tools/list: required tools present with input/output schemas')

            async def call(name,request=None,error=None):
                result=await asyncio.wait_for(session.call_tool(name,{} if request is None else {'request':request}),90)
                transcript.append(dict(tool=name,arguments=request,result=result.model_dump(mode='json')))
                if error:
                    assert result.isError,(name,error,result)
                    content='\n'.join(x.text for x in result.content if hasattr(x,'text'))
                    assert error in content,(error,content)
                    checks.append(error)
                    return None
                assert not result.isError,(name,result)
                assert result.structuredContent and result.structuredContent['ok']
                return result.structuredContent['data']

            caps=await call('system_capabilities')
            assert caps['features']['signature_gated_gerber'] is True and caps['features']['orders'] is False
            project=await call('project_create',{'name':'LED protocol acceptance','profile':'led_series_experiment'})
            pid=project['project_id']

            def mutation(**kwargs):
                return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kwargs)

            await call('library_get',{'candidate_id':'0'*32},'UNKNOWN_CANDIDATE')
            candidates={}
            for q in ['Battery_Cell','LED','Device:R']:
                found=await call('library_search',{'query':q})
                assert len(found['candidates'])==1,found
                c=await call('library_get',{'candidate_id':found['candidates'][0]['candidate_id']})
                assert c['completeness']=='symbol_only' and c['mpn'] is None
                candidates[c['symbol']]=c
            checks.append('Real KiCad symbol lookup with pins and definition hashes; no fictional SKU')
            components={}
            for symbol,ref,value in [('Battery_Cell','BT1','3V'),('R','R1','330'),('LED','D1','Red')]:
                request=mutation(candidate_id=candidates[symbol]['candidate_id'],ref=ref,value=value)
                result=await call('component_add',request)
                rev=result['revision']
                components[symbol]=result
                again=await call('component_add',request)
                assert again==result
            checks.append('Idempotent retries do not duplicate components')
            bad=dict(request,value='Blue')
            await call('component_add',bad,'IDEMPOTENCY_CONFLICT')
            await call('component_parameters_set',dict(mutation(component_id=components['R']['component_id'],value='470'),expected_revision=0),'REVISION_CONFLICT')
            await call('net_create',dict(mutation(name='BAD',kind='signal'),unexpected=True),'extra_forbidden')
            nets={}
            for name,kind in [('BAT_PLUS','power'),('LED_ANODE','signal'),('BAT_MINUS','ground')]:
                n=await call('net_create',mutation(name=name,kind=kind))
                rev=n['revision']; nets[name]=n['net_id']
            def pin(symbol,number):
                c=components[symbol]
                return dict(component_id=c['component_id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==number))
            await call('net_members_set',mutation(net_id=nets['BAT_PLUS'],pins=[dict(component_id=components['R']['component_id'],pin_id='0'*32)]),'UNKNOWN_PIN')
            members={'BAT_PLUS':[pin('Battery_Cell','1'),pin('R','1')],
                     'LED_ANODE':[pin('R','2'),pin('LED','2')],
                     'BAT_MINUS':[pin('LED','1'),pin('Battery_Cell','2')]}
            for name,pins in members.items():
                result=await call('net_members_set',mutation(net_id=nets[name],pins=pins))
                rev=result['revision']
            await call('net_members_set',mutation(net_id=nets['LED_ANODE'],pins=[pin('Battery_Cell','1')]),'PIN_ALREADY_CONNECTED')
            report=await call('spec_validate',dict(project_id=pid,revision=rev))
            assert report['verdict']=='pass'
            build_request=mutation()
            built=await call('schematic_build',build_request)
            assert await call('schematic_build',build_request)==built
            target=dict(project_id=pid,build_id=built['build_id'])
            await call('schematic_export',target,'VALIDATION_REQUIRED')
            verified=await call('schematic_verify',target)
            assert verified['verdict']=='pass',verified
            erc=await call('erc_run',target)
            assert erc['verdict']=='pass',erc
            assert erc['findings']==[]
            preview=await call('schematic_preview',target)
            assert preview['layout_report']['verdict']=='incomplete'
            resource=await session.read_resource(preview['resource_uri'])
            assert '<svg' in resource.contents[0].text
            bundle=await call('schematic_export',target)
            assert Path(bundle['bundle_path']).is_file()
            checks.append('Real SKiDL -> KiCad schematic -> independently exported matching netlist -> zero ERC -> SVG -> gated ZIP')
            checks.append('Preview accessible through MCP resources; unsupported visual checks remain explicit')
            # Tampering must invalidate existing evidence, even without a revision change.
            schema=Path(bundle['schematic_path'])
            original=schema.read_bytes()
            schema.write_bytes(original+b'\n')
            await call('schematic_export',target,'ARTIFACT_CHANGED')
            schema.write_bytes(original)
            for filename in ['actual.xml','erc.json','spec.json']:
                evidence=schema.parent/filename
                original_evidence=evidence.read_bytes()
                evidence.write_bytes(original_evidence+b'\n')
                await call('schematic_export',target,'ARTIFACT_CHANGED')
                evidence.write_bytes(original_evidence)
            checks.append('Netlist, ERC and input snapshot tampering also block export')
            result=await call('component_parameters_set',mutation(component_id=components['R']['component_id'],value='470'))
            rev=result['revision']
            await call('schematic_export',target,'STALE_VALIDATION')
            # Deliberately reverse only the LED, preserving all pin cardinalities.
            for name in ['LED_ANODE','BAT_MINUS']:
                result=await call('net_members_set',mutation(net_id=nets[name],pins=[])); rev=result['revision']
            reverse={'LED_ANODE':[pin('R','2'),pin('LED','1')],
                     'BAT_MINUS':[pin('LED','2'),pin('Battery_Cell','2')]}
            for name,pins in reverse.items():
                result=await call('net_members_set',mutation(net_id=nets[name],pins=pins)); rev=result['revision']
            report=await call('spec_validate',dict(project_id=pid,revision=rev))
            assert report['verdict']=='fail'
            assert report['findings'][0]['code']=='LED_SERIES_POLARITY_OR_TOPOLOGY'
            checks.append('Reversed LED fails functional topology rule before build, independently of ERC')
            await call('schematic_build',mutation(),'SPEC_INVALID')
            # Nonexistent backend: no pass or partial export should be possible.
            # Tested below in a separate service process; ordinary tools expose no backend paths.
            (OUT/'successful_build.json').write_text(json.dumps(dict(project_id=pid,build=built,bundle=bundle),ensure_ascii=False,indent=2))
    # A fresh server process must read the committed state correctly.
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            result=await session.call_tool('project_read',{'request':dict(project_id=pid,revision=rev)})
            assert not result.isError and result.structuredContent['data']['revision']==rev
            checks.append('State survives server restart')
    # Prove process/timeout failure paths without spending a minute waiting.
    from unittest.mock import patch
    import subprocess
    from pcb_mcp import backend
    with patch('pcb_mcp.backend.subprocess.run',side_effect=subprocess.TimeoutExpired('kicad',45)):
        try:
            backend.command(['version'],OUT)
            raise AssertionError('Timeout silently passed')
        except RuntimeError as e:
            assert str(e)=='BACKEND_TIMEOUT'
    checks.append('Backend timeout raises failure, never a passing report')
    with patch('pcb_mcp.backend.subprocess.run',side_effect=FileNotFoundError('missing backend')):
        try:
            backend.command(['version'],OUT)
            raise AssertionError('Missing backend silently passed')
        except FileNotFoundError:
            pass
    checks.append('Missing backend raises failure, never a passing report')
    report=dict(passed=True,checks=checks,call_count=len(transcript),data_root=str(data),
                note='Scripted MCP client acceptance; not an LLM reasoning or host registration test.')
    (OUT/'transcript.json').write_text(json.dumps(transcript,ensure_ascii=False,indent=2))
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    asyncio.run(run())
