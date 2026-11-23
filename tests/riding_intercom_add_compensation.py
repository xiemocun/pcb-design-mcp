"""Add actual source-resolved two120p feedforward capacitors through public MCP."""
import asyncio,json,os,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from riding_intercom_circuit import ROOT,PID
async def main():
 server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(ROOT/'data')})
 async with stdio_client(server) as (r,w),ClientSession(r,w) as session:
  await session.initialize()
  async def call(name,args):
   result=await session.call_tool(name,{'request':args});rec=result.model_dump(mode='json')
   with (ROOT/'hardware-circuit/compensation-mcp.jsonl').open('a') as f:f.write(json.dumps(dict(tool=name,request=args,response=rec))+'\n')
   if result.isError:raise RuntimeError(str(result))
   return result.structuredContent['data']
  s=await call('project_read',dict(project_id=PID));rev=s['revision']
  async def mutate(name,**args):
   nonlocal rev
   result=await call(name,dict(project_id=PID,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**args));rev=result['revision'];return result
  hits=(await call('parts_search',dict(query='06035A121JAT2A')))['candidates'];part=await call('part_get',dict(candidate_id=next(p['candidate_id'] for p in hits if p['mpn']=='06035A121JAT2A')))
  sym=next(p for p in (await call('library_search',dict(query=part['symbol_id'])))['candidates'] if p['library_id']==part['symbol_id']);sym=await call('library_get',dict(candidate_id=sym['candidate_id']))
  fp=next(p for p in (await call('footprint_search',dict(query=part['footprint_id'],library=part['footprint_id'].split(':')[0])))['candidates'] if p['footprint_id']==part['footprint_id']);fp=await call('footprint_get',dict(candidate_id=fp['candidate_id']))
  additions=[]
  for ref in ['C51','C52']:
   c=next((c for c in s['components'].values() if c['ref']==ref),None)
   if c is None:
    result=await mutate('component_add',candidate_id=sym['candidate_id'],ref=ref,value='120p');c=dict(id=result['component_id'],pins=result['pins'])
   if c['id'] not in s['footprint_bindings']:await mutate('footprint_bind',component_id=c['id'],candidate_id=fp['candidate_id'],reason='Same manufacturer C0G120p0603 source as existing C10; two parallel for240p boost feedforward')
   if c['id'] not in s['part_bindings']:await mutate('part_bind',component_id=c['id'],candidate_id=part['candidate_id'])
   additions.append(c)
  for name,num in [('V5','1'),('BOOST_FB','2')]:
   net=next(n for n in s['nets'].values() if n['name']==name);members=net['pins'][:]
   for c in additions:
    pin=next(p for p in c['pins'] if num in p.get('physical_numbers',[p['number']]));member=dict(component_id=c['id'],pin_id=pin['pin_id'])
    if member not in members:members.append(member)
   await mutate('net_members_set',net_id=net['id'],pins=members)
  print('Added source-bound feedforward capacitors, revision',rev,flush=True)
if __name__=='__main__':asyncio.run(main())
