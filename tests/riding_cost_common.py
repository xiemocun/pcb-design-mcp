import asyncio,json,os,sys,uuid
from pathlib import Path
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path('work/riding-intercom').resolve();OUT=ROOT/'cost-down';DATA=ROOT/'data';PARENT='e4569a2267ec4b0c8dbfe22e4bb60820'
class Client:
 def __init__(self,session,log):self.session=session;self.log=log;self.pid=None;self.rev=None
 async def call(self,tool,**args):
  reply=await self.session.call_tool(tool,{'request':args},read_timeout_seconds=timedelta(seconds=300))
  with self.log.open('a') as f:f.write(json.dumps(dict(tool=tool,request=args,response=reply.model_dump(mode='json')),ensure_ascii=False)+'\n')
  if reply.isError:raise RuntimeError(str(reply)[:2400])
  return reply.structuredContent['data']
 async def state(self):
  s=await self.call('project_read',project_id=self.pid);self.rev=s['revision'];return s
 async def mutate(self,tool,**args):
  r=await self.call(tool,project_id=self.pid,expected_revision=self.rev,idempotency_key=uuid.uuid4().hex,**args);self.rev=r['revision'];return r
async def run(fn,log):
 params=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(DATA)})
 async with stdio_client(params) as (r,w),ClientSession(r,w) as s:
  await s.initialize();c=Client(s,OUT/log)
  if (OUT/'context.json').exists():c.pid=json.loads((OUT/'context.json').read_text())['project_id'];await c.state()
  return await fn(c)
async def fork(c):
 if c.pid:print('Existing cost-down project',c.pid);return
 r=await c.call('project_fork',project_id=PARENT,expected_revision=626,idempotency_key='costdown_20261107_v1',name='Riding intercom cost-down without reduced functions');(OUT/'context.json').write_text(json.dumps(r,indent=2));print(r)
if __name__=='__main__':asyncio.run(run(fork,'fork.jsonl'))
