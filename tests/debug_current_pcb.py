import asyncio,json,os,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1]
async def main():
    log=json.loads((R/'acceptance-sensor-module/transcript.json').read_text());pc=next(x for x in reversed(log) if x['tool']=='pcb_build');req=pc['arguments'];pid=req['project_id']
    # Locate this isolated test data by its authoritative project state.
    data=next(p.parent for p in sorted((R/'acceptance-sensor-module').glob('run-*/'+pid)) if (p/'state.json').exists())
    params=StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(params) as (r,w):
      async with ClientSession(r,w) as s:
        await s.initialize()
        async def call(tool,args):
            result=await s.call_tool(tool,{'request':args})
            if result.isError:raise RuntimeError(str(result))
            d=result.structuredContent['data'];print(json.dumps(dict(tool=tool,result=d),ensure_ascii=False));return d
        fresh=await call('schematic_build',dict(project_id=pid,expected_revision=req['expected_revision'],idempotency_key=uuid.uuid4().hex))
        sch=dict(project_id=pid,build_id=fresh['build_id']);await call('schematic_verify',sch);await call('erc_run',sch)
        req['idempotency_key']=uuid.uuid4().hex;req['schematic_build_id']=fresh['build_id'];b=await call('pcb_build',req);pt=dict(project_id=pid,pcb_build_id=b['pcb_build_id'])
        await call('pcb_verify',{**pt,'stage':'unrouted'});await call('pcb_layout_check',{**pt,'stage':'unrouted'})
        await call('pcb_autoroute',{**pt,'max_passes':60});await call('pcb_verify',pt);await call('pcb_layout_check',pt);drc=await call('drc_run',pt)
        (R/'work/native-debug.json').write_text(json.dumps(dict(data_root=str(data),project_id=pid,pcb_build_id=b['pcb_build_id'],drc=drc),ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
