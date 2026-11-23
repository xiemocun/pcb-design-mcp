"""Exercise the actual selected host registration using a fresh MCP client."""
import asyncio,json,os,tomllib
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1]

async def main():
    cfg=tomllib.loads((Path.home()/'.codex/config.toml').read_text())['mcp_servers']['pcb-design-lab']
    assert cfg['command']==str(R/'.venv/bin/python') and cfg['args']==[str(R/'run_server.py')]
    assert cfg['env']['PCB_MCP_DATA_DIR']==str(R/'data')
    edit=json.loads((R/'acceptance-parts/report.json').read_text())
    proto=json.loads((R/'acceptance-power-module/report.json').read_text())
    calls=[]
    async with stdio_client(StdioServerParameters(command=cfg['command'],args=cfg['args'],env={**os.environ,**cfg['env']})) as (r,w):
      async with ClientSession(r,w) as session:
        await session.initialize();listed=await session.list_tools();assert len(listed.tools)==61
        (R/'tool-contract.json').write_text(json.dumps(listed.model_dump(mode='json'),ensure_ascii=False,indent=2))
        async def call(name,request=None,error=None):
            result=await session.call_tool(name,{} if request is None else {'request':request})
            calls.append(dict(tool=name,arguments=request,result=result.model_dump(mode='json')))
            if error:
                assert result.isError and error in '\n'.join(b.text for b in result.content if hasattr(b,'text')),(name,error,result)
                return
            assert not result.isError,(name,result)
            return result.structuredContent['data']
        caps=await call('system_capabilities');assert caps['contract_version']=='0.9.0'
        projects=await call('project_list');assert len(projects['projects'])==19
        original=await call('project_read',dict(project_id='923e1ddb9064427cbfcb3b95281191c3'))
        assert next(c['value'] for c in original['components'].values() if c['ref']=='R1')=='470'
        state=await call('project_read',dict(project_id=proto['project_id']))
        assert len(state['components'])==22 and len(state['nets'])==13
        def pin(ref,name):
            c=next(c for c in state['components'].values() if c['ref']==ref)
            p=next(p for p in c['pins'] if p['name']==name)
            return dict(component_id=c['id'],pin_id=p['pin_id'])
        traces={}
        for ref,name,net in [('U1','IO27','MCU_SENSOR_DATA'),('U2','SDA','SENSOR_DATA'),('U1','IO26','SENSOR_OE'),('U3','OUT','V3V3')]:
            trace=await call('schematic_pin_connections',dict(project_id=proto['project_id'],build_id=proto['schematic_build_id'],pin=pin(ref,name)))
            assert trace['net']['name']==net and trace['evidence']['verified']
            traces[net]=trace
        assert all(p['reference']!='U1' for p in traces['SENSOR_DATA']['members'])
        module=await call('module_constraints_check',dict(project_id=proto['project_id']))
        assert module['verdict']=='incomplete' and not module['manufacturing_approved']
        bounded=await call('module_constraints_check',dict(project_id=proto['interval_project_id']))
        assert bounded['verdict']=='pass' and bounded['whole_board_power_verdict']=='fail' and not bounded['manufacturing_approved']
        power=await call('power_integrity_check',dict(project_id=proto['interval_project_id']))
        assert power['verdict']=='fail'
        assert not any(f['code']=='REGULATOR_CURRENT_CAPACITY' for f in power['findings'])
        usb=await call('usb_c_power_check',dict(project_id=proto['project_id']))
        assert usb['verdict']=='fail' and not usb['manufacturing_approved']
        assert usb['ports'][0]['nominal_direct_capacitance_uf']>10.0
        pt=dict(project_id=proto['project_id'],pcb_build_id=proto['pcb_build_id'])
        sensor_audit=await call('design_audit',pt);assert sensor_audit['status']=='blocked'
        stages={s['check_id']:s['status'] for s in sensor_audit['stages']}
        for key in ['ACTUAL_NETLIST','ACTUAL_ERC','ACTUAL_PCB_NETS','ACTUAL_DRC','ACTUAL_RF_AND_DECOUPLING']:
            assert stages[key]=='pass',(key,stages)
        await call('manufacturing_review_prepare',pt,'POWER_INTEGRITY_REQUIRED')
        await call('bom_export',dict(project_id=proto['project_id']),'BOM_PART_MISSING')
        led=await call('project_read',dict(project_id=edit['project_id']))
        resistor=next(c for c in led['components'].values() if c['ref']=='R1')
        assert resistor['value']=='470' and led['part_bindings'][resistor['id']]['mpn']=='MRS25000C4700FCT00'
        lt=dict(project_id=edit['project_id'],pcb_build_id=edit['pcb_build_id'])
        led_audit=await call('design_audit',lt)
        assert led_audit['status']=='ready_for_human_review' and not led_audit['manufacturing_approved']
        bom=await call('bom_export',dict(project_id=edit['project_id']));assert len(bom['rows'])==3
        await call('pcb_preview',lt);await call('pcb_review_export',lt)
        review=await call('manufacturing_review_prepare',lt)
        rr=dict(project_id=edit['project_id'],review_id=review['review_id'])
        assert (await call('manufacturing_review_status',rr))['status']=='pending_human_signature'
        await call('fabrication_export',rr,'HUMAN_APPROVAL_REQUIRED')
        await call('fabrication_export',{**rr,'approved':True},'extra_forbidden')
        await call('manufacturing_review_status',dict(project_id='271d169a2e334222b6dedb7109d0d3b8',review_id='1626bf2009564c7bbf950d84cf3a7413'),'RULEPACK_CHANGED')
    report=dict(passed=True,call_count=len(calls),tool_count=61,contract_version='0.9.0',project_count=19,
                sensor_audit=sensor_audit,usb_power=usb,module_static=module,module_bounded=bounded,whole_board_power=power,traces=traces,
                led_audit=led_audit,led_bom=bom,led_review=review,real_human_approval=False,new_gerber_generated=False,ordered=False,
                original_470_preserved=True,old_receipt_cannot_authorize_new_implementation=True,
                connection='Fresh stdio MCP client using the actual registered local config; existing chat tool cache is not evidence of this connection.')
    (R/'acceptance-live.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (R/'work/live-transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ['sensor_audit','usb_power','module_static','module_bounded','whole_board_power','traces','led_audit','led_bom','led_review']},ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(main())
