"""Install source-bound declared DC reference through public MCP; never approval."""
import asyncio,json,base64,hashlib,os,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
root=Path('work/riding-intercom').resolve();s=json.loads((root/'data/89184e4c1e4740f88be798dfed22a98b/state.json').read_text());c=next(c for c in s['components'].values() if c['ref']=='U4');part=s['part_bindings'][c['id']]
assets=[];evidence=[]
for name,locator,meaning in [('bq24074','Electrical characteristics, KILIM/KISET, OUT/BAT current, power-path operation','Manufacturer current program ranges and source voltage/current ratings; no assumed load/efficiency originates here.'),('tps61023','Recommended operating conditions and electrical characteristics; application equations','Boost operating range and conditional valley-current specification; efficiency remains declared assumption.'),('tpsm82821','Recommended operating conditions, output current and adjustable output equation','Buck rated1A and divider formula; efficiency remains declared assumption.'),('tlv755p','Recommended operating conditions, electrical characteristics','LDO output current and accuracy; actual board thermal behavior is unmeasured.')]:
 path_source=root/'sources'/(name+'.pdf')
 if not path_source.exists():path_source=Path('evidence-cache')/(name+'.pdf')
 raw=path_source.read_bytes();url='https://www.ti.com/lit/ds/symlink/'+name+'.pdf';path=name+'.pdf'
 assets.append(dict(path=path,kind='document',source_url=url,sha256=hashlib.sha256(raw).hexdigest(),content_base64=base64.b64encode(raw).decode()))
 evidence.append(dict(asset=path,locator=locator,interpretation=meaning))
m=json.loads((root/'cost-down/dc-model-proposal.json').read_text())
request=dict(name='riding_intercom_cost_dc_reference',part_definition_hash=part['definition_hash'],engine='dc_power_tree',pins=[p['number'] for p in c['pins']],assets=assets,evidence=evidence,temperature_range_c=[0.,50.],conditions=m['conditions'],dc_power_tree=m)
async def main():
 server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(root/'data')})
 async with stdio_client(server) as (r,w),ClientSession(r,w) as session:
  await session.initialize();result=await session.call_tool('verification_model_import',{'request':request});data=result.model_dump(mode='json');(root/'cost-down/dc-model-import-result.json').write_text(json.dumps(data,indent=2));print(result.structuredContent if not result.isError else result)
if __name__=='__main__':asyncio.run(main())
