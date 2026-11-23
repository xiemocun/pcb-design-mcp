"""Resume current hardware draft, import source equation, run actual CAD gates."""
import asyncio,base64,hashlib,json,os,sys,uuid
from datetime import timedelta
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from riding_intercom_circuit import ROOT,PID

async def main():
    number=1
    while (ROOT/f'hardware-cad-{number:02d}').exists():number+=1
    out=ROOT/f'hardware-cad-{number:02d}';out.mkdir()
    parameters=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(ROOT/'data')})
    async with stdio_client(parameters) as (r,w),ClientSession(r,w) as s:
        await s.initialize()
        async def call(tool,args):
            reply=await s.call_tool(tool,{'request':args},read_timeout_seconds=timedelta(seconds=300))
            with (out/'transcript.jsonl').open('a') as log:log.write(json.dumps(dict(tool=tool,request={'retained_in':'model.json'} if tool=='verification_model_import' else args,response=reply.model_dump(mode='json')),ensure_ascii=False)+'\n')
            if reply.isError:raise RuntimeError(str(reply))
            return reply.structuredContent['data']
        state=await call('project_read',dict(project_id=PID));rev=state['revision']
        async def mutate(tool,**args):
            nonlocal rev
            reply=await call(tool,dict(project_id=PID,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**args));rev=reply['revision'];return reply
        u5=next(c for c in state['components'].values() if c['ref']=='U5');part=state['part_bindings'][u5['id']]
        data=(ROOT/'sources/tps61023.pdf').read_bytes()
        model=dict(name='TPS61023_PWM_nominal_feedback',engine='feedback_voltage',part_definition_hash=part['definition_hash'],pins=['1','2','3','4','5','6'],excluded_pins={},temperature_range_c=[25.,25.],supply_range_v=[1.8,5.5],conditions='PWM enabled, nominal feedback setting only, VREFtyp595mV. PFM typical601mV differs; no PFM maximum, input power delivery, switch/inductor, dynamic current, thermal, startup or regulation guarantee inferred. Full power-chain review remains required.',feedback_voltage=dict(input_pin='3',output_pin='6',feedback_pin='1',ground_pin='4',reference_nominal_v=.595,output_min_v=2.2,output_max_v=5.5),assets=[dict(path='documents/tps61023.pdf',kind='document',source_url='https://www.ti.com/lit/ds/symlink/tps61023.pdf',sha256=hashlib.sha256(data).hexdigest(),content_base64=base64.b64encode(data).decode())],evidence=[dict(asset='documents/tps61023.pdf',locator='Section5 pin table; page5 electrical characteristics VREF PWMtyp595mV; section8.2.2.1 equation4',interpretation='Apply only nominal resistor-divider setting; separately retain all unmodeled power delivery and tolerance checks.')])
        (out/'model.json').write_text(json.dumps(model,indent=2));await call('verification_model_import',model)
        validation=await call('spec_validate',dict(project_id=PID));(out/'validation.json').write_text(json.dumps(validation,indent=2,ensure_ascii=False))
        if validation['verdict']!='pass':raise RuntimeError('Specification remains blocked; see validation.json')
        sch=await mutate('schematic_build');target=dict(project_id=PID,build_id=sch['build_id'])
        (out/'schematic.json').write_text(json.dumps(sch,indent=2))
        for tool in ['schematic_verify','erc_run','schematic_preview']:
            result=await call(tool,target);(out/(tool+'.json')).write_text(json.dumps(result,indent=2,ensure_ascii=False));print(tool,result.get('verdict','produced'),flush=True)
        bom=await call('bom_draft_export',dict(project_id=PID));(out/'bom.json').write_text(json.dumps(bom,indent=2,ensure_ascii=False))
        print('Current schematic build',sch['build_id'],flush=True)

if __name__=='__main__':asyncio.run(main())
