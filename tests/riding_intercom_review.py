"""Prepare fixed current CAD review. Never signs or exports Gerbers."""
import asyncio,json,os,sys
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from riding_intercom_circuit import ROOT,PID
async def main():
 out=ROOT/'hardware-pcb-10';pcb=json.loads((out/'pcb_build.json').read_text())['pcb_build_id'];args=dict(project_id=PID,pcb_build_id=pcb)
 server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(ROOT/'data')})
 async with stdio_client(server) as (r,w),ClientSession(r,w) as session:
  await session.initialize()
  for tool,request in [('pcb_review_export',args),('manufacturing_review_prepare',dict(**args,purpose='prototype_files_only',qualification_declaration='First engineering prototype PCBA for two-person offline loudspeaker intercom. Actual PCB pad/net identity, ERC, native DRC, local layout and ground-plane geometry checked; source-bound declared DC and finite pad-neck references retained. No physical RF range, wind noise, full-duplex echo, power startup, temperature, ground-plane current distribution or effective capacitance validation. User explicitly deferred physical tests until boards exist. Requires filled/capped via-in-pad, exact BOM/CPL and independent human authorization of this fixed file export only; no order.',validity_hours=72))]:
   reply=await session.call_tool(tool,{'request':request},read_timeout_seconds=timedelta(seconds=300));record=reply.model_dump(mode='json');(out/(tool+'.json')).write_text(json.dumps(record,indent=2,ensure_ascii=False))
   if reply.isError:raise RuntimeError(str(reply)[:1600])
   d=reply.structuredContent['data'];print(tool,d.get('status'),d.get('review_path',d.get('bundle_path')),flush=True)
if __name__=='__main__':asyncio.run(main())
