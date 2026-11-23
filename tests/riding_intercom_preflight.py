"""Record this user's new requirements and probe current real MCP capabilities."""
import asyncio,hashlib,json,os,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    root=Path('work/riding-intercom').resolve();out=root/'preflight-01';out.mkdir(exist_ok=False)
    source=json.loads((root/'requirements.json').read_text());transcript=[]
    frozen=json.loads(Path('work/generalization/freeze-01.json').read_text())
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen.items())
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(root/'data')})
    async with stdio_client(server) as (a,b),ClientSession(a,b) as session:
        await session.initialize()
        async def call(name,args=None):
            reply=await session.call_tool(name,{} if args is None else dict(request=args));transcript.append(dict(tool=name,request=args,response=reply.model_dump(mode='json')))
            (out/'transcript.json').write_text(json.dumps(transcript,indent=2,ensure_ascii=False)+'\n');assert not reply.isError,str(reply)
            return reply.structuredContent['data']
        caps=await call('system_capabilities');listing=await session.list_tools()
        created=await call('project_create',dict(name=source['name'],profile='schematic_experiment'))
        items=[dict(key=k,value=v,origin='user_statement',source_message_ref='2026-11-07 user request and three clarification replies') for k,v in source['user_requirements'].items()]
        recorded=await call('requirements_set',dict(project_id=created['project_id'],expected_revision=created['revision'],idempotency_key=uuid.uuid4().hex,items=items))
        candidates={}
        for q in ('ESP32-S3-WROOM-1U','ES7210','ES8311'):
            candidates[q]=dict(parts=await call('parts_search',dict(query=q)),symbols=await call('library_search',dict(query=q)))
        result=dict(project_id=created['project_id'],revision=recorded['revision'],contract_version=caps['contract_version'],tool_count=len(listing.tools),requirements_recorded=True,candidates=candidates,core_freeze_unchanged=True,functional_qualification=False,pcb_generated=False)
        (out/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
        print(json.dumps(dict(project_id=result['project_id'],revision=result['revision'],version=result['contract_version'],catalog_candidates={k:len(v['parts']['candidates']) for k,v in candidates.items()},symbol_candidates={k:len(v['symbols']['candidates']) for k,v in candidates.items()}),ensure_ascii=False))

if __name__=='__main__':asyncio.run(main())
