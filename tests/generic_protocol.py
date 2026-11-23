"""Actual MCP integration for a multi-component circuit and editing/constraint failures."""
import asyncio,json,os,sys,uuid,shutil
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT=Path(__file__).resolve().parents[1]


async def run():
    out=ROOT/'acceptance-generic'; out.mkdir(exist_ok=True)
    data=out/('run-'+uuid.uuid4().hex); calls=[]; checks=[]
    params=StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_server.py')],
            env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(params) as (r,w):
        async with ClientSession(r,w) as session:
            await session.initialize()
            tools=await session.list_tools()
            (out/'tools.json').write_text(json.dumps(tools.model_dump(mode='json'),ensure_ascii=False,indent=2))
            assert len(tools.tools)==68
            async def call(name,args=None,error=None):
                response=await asyncio.wait_for(session.call_tool(name,{} if args is None else {'request':args}),90)
                calls.append(dict(tool=name,arguments=args,result=response.model_dump(mode='json')))
                if error:
                    assert response.isError,(name,response)
                    assert error in '\n'.join(c.text for c in response.content if hasattr(c,'text')),response
                    checks.append(error); return None
                assert not response.isError,(name,response)
                return response.structuredContent['data']
            p=await call('project_create',{'name':'RC ladder with connector and explicit NC'})
            pid=p['project_id']; rev=p['revision']
            def m(**kwargs):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kwargs)
            requirements=await call('requirements_set',m(items=[dict(key='function',value='RC passive test with connector',origin='user_statement'),dict(key='power_voltage',value='3V',origin='proposed_assumption')]))
            rev=requirements['revision']; assert requirements['verification']=='recorded_not_independently_verified'
            async def resolve(query):
                search=await call('library_search',{'query':query})
                assert search['total']==1,search
                return await call('library_get',{'candidate_id':search['candidates'][0]['candidate_id']})
            candidates={name:await resolve(name) for name in ['Device:Battery_Cell','Device:R','Device:C','Connector_Generic:Conn_01x03']}
            unsupported=await resolve('Device:D_Dual_CommonAnode_AKK_Split')
            assert not unsupported['supported']
            await call('component_add',m(candidate_id=unsupported['candidate_id'],ref='U1',value=unsupported['symbol']),'UNSUPPORTED_SYMBOL')
            paged=await call('library_search',{'query':'USB','library':'Connector','limit':2})
            assert len(paged['candidates'])==2 and paged['next_offset']==2
            nextpage=await call('library_search',{'query':'USB','library':'Connector','limit':2,'offset':2})
            assert not ({c['candidate_id'] for c in paged['candidates']}&{c['candidate_id'] for c in nextpage['candidates']})
            checks.append('Real multi-library search, pagination and unsupported symbol refusal')
            components={}
            for symbol,ref,value in [('Device:Battery_Cell','BT1','3V'),('Device:R','R1','1000'),('Device:R','R2','2200'),('Device:C','C1','100nF'),('Connector_Generic:Conn_01x03','J1','Conn_01x03')]:
                c=await call('component_add',m(candidate_id=candidates[symbol]['candidate_id'],ref=ref,value=value))
                rev=c['revision']; components[ref]=c
            def pin(ref,num):
                c=components[ref]
                return dict(component_id=c['component_id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
            rev=(await call('component_parameters_set',m(component_id=components['R1']['component_id'],value='1k')))['revision']
            await call('component_parameters_set',m(component_id=components['C1']['component_id'],value='banana'),'INVALID_PARAMETER')
            nets={}
            for name,kind in [('VCC','power'),('MID','signal'),('GND','ground')]:
                n=await call('net_create',m(name=name,kind=kind)); rev=n['revision']; nets[name]=n['net_id']
            mapping={'VCC':[pin('BT1','1'),pin('R1','1'),pin('J1','1')],
                     'MID':[pin('R1','2'),pin('R2','1'),pin('C1','1')],
                     'GND':[pin('BT1','2'),pin('R2','2'),pin('C1','2'),pin('J1','2')]}
            for name,pins in mapping.items():rev=(await call('net_members_set',m(net_id=nets[name],pins=pins)))['revision']
            check=await call('spec_validate',{'project_id':pid,'revision':rev})
            assert check['verdict']=='fail' and any(f['code']=='PIN_INTENT_UNSPECIFIED' for f in check['findings'])
            await call('pin_intent_set',m(pin=pin('J1','3'),intent='no_connect'),'REASON_REQUIRED')
            rev=(await call('pin_intent_set',m(pin=pin('J1','3'),intent='no_connect',reason='Reserved connector pin intentionally unused in this experiment')))['revision']
            await call('net_members_set',m(net_id=nets['MID'],pins=mapping['MID']+[pin('J1','3')]),'PIN_MARKED_NO_CONNECT')
            await call('pin_intent_set',m(pin=pin('J1','1'),intent='no_connect',reason='Incorrect attempt'),'PIN_ALREADY_CONNECTED')
            await call('component_remove',m(component_id=components['R1']['component_id']),'ENTITY_IN_USE')
            await call('net_remove',m(net_id=nets['VCC']),'ENTITY_IN_USE')
            checks.append('Every pin must connect or have a reasoned NC; destructive edits cannot drop references')
            con=await call('constraint_set',m(constraint={'kind':'must_connect','pins':[pin('R1','1'),pin('R2','1')]}));rev=con['revision']
            invalid=await call('spec_validate',{'project_id':pid,'revision':rev})
            assert any(f['code']=='CONSTRAINT_UNSATISFIED' for f in invalid['findings'])
            await call('schematic_build',m(),'SPEC_INVALID')
            rev=(await call('constraint_remove',m(constraint_id=con['entity_id'])))['revision']
            for kind,pins in [('must_connect',[pin('BT1','1'),pin('J1','1')]),('must_separate',[pin('BT1','1'),pin('BT1','2')])]:
                rev=(await call('constraint_set',m(constraint={'kind':kind,'pins':pins})))['revision']
            # Editing an isolated extra component/net must actually work, not just fail safely.
            spare=await call('component_add',m(candidate_id=candidates['Device:R']['candidate_id'],ref='R3',value='10k'));rev=spare['revision']
            rev=(await call('component_remove',m(component_id=spare['component_id'])))['revision']
            spare=await call('net_create',m(name='TEMP',kind='signal'));rev=spare['revision']
            rev=(await call('net_remove',m(net_id=spare['net_id'])))['revision']
            checks.append('Multiple instances, safe removal, typed constraints and failure-repair loop')
            valid=await call('spec_validate',{'project_id':pid,'revision':rev});assert valid['verdict']=='pass',valid
            built=await call('schematic_build',m(layout_profile='labeled_grid'))
            target=dict(project_id=pid,build_id=built['build_id'])
            verified=await call('schematic_verify',target);assert verified['verdict']=='pass' and verified['no_connect_match'],verified
            erc=await call('erc_run',target);assert erc['verdict']=='pass',erc
            preview=await call('schematic_preview',target)
            assert preview['layout_report']['verdict']=='incomplete'
            exported=await call('schematic_export',target)
            state=await call('project_read',{'project_id':pid})
            assert state['revision']==rev and len(state['components'])==5
            listing=await call('project_list');assert any(p['project_id']==pid for p in listing['projects'])
            checks.append('Real 5-component/3-net RC schematic, correct numeric values, explicit NC, independent netlist match and zero ERC')
            demo=ROOT/'demo-generic';demo.mkdir(exist_ok=True)
            folder=Path(exported['schematic_path']).parent
            for name in ['led.kicad_sch','review_bundle.zip','erc.json','actual.xml','manifest.json']:
                shutil.copy2(folder/name,demo/name)
            shutil.copy2(folder/'preview/led.svg',demo/'preview.svg')
            (out/'successful_build.json').write_text(json.dumps(dict(project_id=pid,revision=rev,build=built,export=exported),ensure_ascii=False,indent=2))
    report=dict(passed=True,call_count=len(calls),checks=checks,limitations='Single-unit experimental schematics. No functional ratings or manufacturing assurance.')
    (out/'transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(run())
