"""Read-only actual MCP regression of saved board roles; no fake export proof."""
import asyncio,hashlib,json,os,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/usb-default-power-review'
sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts,__version__
PROJECTS=dict(analog='8f5b7aa0dac846ac961ebcfee740b6cc',original_timer='a3074bbb123e45faa365985ab499686c',mainboard='fb639744d29945359f393e331cfb34ab')
async def main():
    calls=[];reports={};frozen=parts.rules_hash()
    before={pid:hashlib.sha256((R/'data'/pid/'state.json').read_bytes()).hexdigest() for pid in PROJECTS.values()}
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(R/'data')})) as (a,b):
        async with ClientSession(a,b) as session:
            await session.initialize();listing=await session.list_tools()
            baseline=json.loads((W/'baseline/tool-contract.json').read_text())
            old={x['name'] for x in baseline['tools']};new={x.name for x in listing.tools}
            assert old==new,(old-new,new-old)
            (W/'tool-contract.json').write_text(listing.model_dump_json(indent=2))
            (W/'contract-report.json').write_text(json.dumps(dict(tool_names_unchanged=True,added=sorted(new-old),removed=sorted(old-new)),indent=2))
            async def call(tool,args=None):
                q=await session.call_tool(tool,{} if args is None else dict(request=args));calls.append(dict(tool=tool,arguments=args,result=q.model_dump(mode='json')))
                (W/'protocol-transcript.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2));assert not q.isError,q
                return q.structuredContent['data']
            caps=await call('system_capabilities');assert caps['contract_version']==__version__
            (W/'capabilities.json').write_text(json.dumps(caps,ensure_ascii=False,indent=2))
            for name,pid in PROJECTS.items():
                s=await call('project_read',dict(project_id=pid));usb=await call('usb_c_power_check',dict(project_id=pid))
                assert usb['evidence_hash']==frozen
                codes={f['code'] for f in usb['findings'] if f['severity']=='error'}
                if name=='mainboard': assert not codes and usb['verdict']=='incomplete'
                else:
                    assert usb['ports'][0]['default_power']['role']=='power_only_sink_candidate'
                    assert 'USB_DEFAULT_MODE_UNQUALIFIED' not in codes
                    assert ('USB_EXTERNAL_INPUT_BACKFEED_PATH' if name=='analog' else 'USB_PSD_DIRECT_CAP_MINIMUM') in codes
                reports[name]=dict(project_id=pid,revision=s['revision'],usb=usb,cad_rerun_in_this_readonly_test=False)
            assert before=={pid:hashlib.sha256((R/'data'/pid/'state.json').read_bytes()).hexdigest() for pid in before}
            assert parts.rules_hash()==frozen
    result=dict(source_evidence_hash=frozen,contract_version=__version__,real_mcp_readonly_calls=True,original_states_unchanged=True,reports=reports,new_gerber_generated=False)
    (W/'protocol-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(dict(version=__version__,tool_names_unchanged=True,original_states_unchanged=True,roles_checked=list(reports)),ensure_ascii=False))
if __name__=='__main__':asyncio.run(main())
