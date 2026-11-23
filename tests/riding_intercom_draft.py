"""Continue the existing MCP project with source-bound ICs and interface nets.
This is a persistent electrical draft, intentionally not a completed schematic.
"""
import asyncio,json,os,sys,uuid
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    root=Path('work/riding-intercom').resolve()
    number=1
    while (root/f'electrical-draft-{number:02d}').exists(): number+=1
    out=root/f'electrical-draft-{number:02d}';out.mkdir(exist_ok=False)
    pid=json.loads((root/'preflight-01/result.json').read_text())['project_id']
    transcript=[]
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(root/'data')})
    async with stdio_client(server) as (r,w),ClientSession(r,w) as s:
        await s.initialize()
        async def call(tool,args):
            reply=await s.call_tool(tool,{'request':args})
            transcript.append(dict(tool=tool,request=args,response=reply.model_dump(mode='json')))
            (out/'transcript.json').write_text(json.dumps(transcript,indent=2,ensure_ascii=False)+'\n')
            assert not reply.isError,str(reply)
            return reply.structuredContent['data']
        state=await call('project_read',dict(project_id=pid));rev=state['revision']
        async def mutate(tool,**args):
            nonlocal rev
            result=await call(tool,dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**args));rev=result['revision'];return result
        source=json.loads((root/'requirements.json').read_text())
        req=[dict(key=k,value=v,origin='user_statement',source_message_ref='2026-11-07 riding intercom requirements and replies') for k,v in source['user_requirements'].items()]
        req += [dict(key='prototype_authorized',value=True,origin='user_statement',source_message_ref=source['prototype_authorization']['source']),dict(key='size_antenna_adjustment',value=True,origin='user_statement',source_message_ref=source['prototype_authorization']['source'])]
        await mutate('requirements_set',items=req)
        components={}
        for ref,mpn in [('U1','ESP32-S3-WROOM-1U-N16R8'),('U2','ES7210'),('U3','ES8311')]:
            part=next(p for p in (await call('parts_search',dict(query=mpn)))['candidates'] if p['mpn']==mpn)
            part=await call('part_get',dict(candidate_id=part['candidate_id']))
            sym=(await call('library_search',dict(query=part['symbol_id'])))['candidates'][0]
            fp=(await call('footprint_search',dict(query=part['footprint_id'])))['candidates'][0]
            sym=await call('library_get',dict(candidate_id=sym['candidate_id']))
            fp=await call('footprint_get',dict(candidate_id=fp['candidate_id']))
            c=await mutate('component_add',candidate_id=sym['candidate_id'],ref=ref,value=mpn);components[ref]=c
            await mutate('footprint_bind',component_id=c['component_id'],candidate_id=fp['candidate_id'],reason='Manufacturer pin/package source and checked matching KiCad geometry, prototype draft')
            await mutate('part_bind',component_id=c['component_id'],candidate_id=part['candidate_id'])
        def pin(ref,n):
            c=components[ref];return dict(component_id=c['component_id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==str(n)))
        # PA and microphone bias/reference passives are not silently omitted:
        # this checkpoint deliberately leaves the electrical draft incomplete.
        nets=[('I2C_SDA',[('U1',10),('U2',3),('U3',19)]),('I2C_SCL',[('U1',11),('U2',4),('U3',1)]),
              ('I2S_MCLK',[('U1',9),('U2',5),('U3',2)]),('I2S_BCLK',[('U1',17),('U2',9),('U3',6)]),
              ('I2S_LRCK',[('U1',26),('U2',10),('U3',8)]),('I2S_ADC_DATA',[('U1',18),('U2',11)]),
              ('I2S_DAC_DATA',[('U1',12),('U3',9)])]
        for name,pins in nets:
            net=await mutate('net_create',name=name,kind='signal')
            await mutate('net_members_set',net_id=net['net_id'],pins=[pin(*p) for p in pins])
        state=await call('project_read',dict(project_id=pid))
        (out/'state.json').write_text(json.dumps(state,indent=2,ensure_ascii=False)+'\n')
        (out/'result.json').write_text(json.dumps(dict(project_id=pid,revision=rev,components=list(components),interface_nets=len(nets),schematic_complete=False,pcb_generated=False,physical_verified=False,remaining=['power and charging','microphones/bias/coupling','DAC reference network','amplifier/speaker','decoupling and strapping','layout/routing and electrical review']),indent=2)+'\n')
        print('Saved source-bound electrical draft',pid,'rev',rev)

if __name__=='__main__':asyncio.run(main())
