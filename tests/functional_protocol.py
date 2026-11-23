"""Actual MCP verification of the composed project, retaining its known failure."""
import argparse,asyncio,json,os,sys
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp.functional import FunctionalContract
from pcb_mcp.registry import canonical,sha
from pcb_mcp.editing import spec_hash


async def main(data,project,output):
    output.mkdir(parents=True,exist_ok=False);transcript=[]
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(data)})
    async with stdio_client(server) as (a,b),ClientSession(a,b) as session:
        await session.initialize()
        async def call(name,args,expect_error=False):
            reply=await session.call_tool(name,dict(request=args));transcript.append(dict(tool=name,request=args,response=reply.model_dump(mode='json')))
            (output/'transcript.json').write_text(json.dumps(transcript,indent=2)+'\n')
            assert bool(reply.isError)==expect_error,str(reply)
            return reply.structuredContent['data'] if not expect_error else None
        state=await call('project_read',dict(project_id=project))
        raw=dict(description='Keep the original unloaded 2.3 to 2.7 V divider requirement; compare full DC corners with nominal SPICE.',requirements=[dict(id='output',description='Unloaded output between 2.3 and 2.7 V across the declared input and resistor ranges.',positive=dict(ref='J2',number='1'),negative=dict(ref='J2',number='2'),quantity='voltage',minimum=2.3,maximum=2.7,scenarios=['corners','nominal'])],scenarios=[dict(id='corners',engine='linear_dc'),dict(id='nominal',engine='spice_op',drives=[dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),dc_v=5.)])])
        contract=FunctionalContract.model_validate(raw).model_dump(mode='json')
        args=dict(project_id=project,revision=state['revision'],expected_spec_sha256=spec_hash(state),contract=contract,contract_sha256=sha(canonical(contract)))
        await call('functional_check',dict(args,contract_sha256='0'*64),True)
        await call('functional_check',dict(args,expected_spec_sha256='0'*64),True)
        result=await call('functional_check',args)
        assert result['verdict']=='fail',result
        rows={r['scenario']:r for r in result['measurements']}
        assert rows['nominal']['passed'] and not rows['corners']['passed'],result
        assert abs(rows['nominal']['maximum']-2.5)<1e-9
        (output/'verification.json').write_text(json.dumps(dict(protocol_passed=True,known_functional_failure_preserved=True,result=result),indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--project',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    asyncio.run(main(a.data.resolve(),a.project,a.output.resolve()))
