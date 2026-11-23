"""Development fixture for real MCP module composition into existing CAD runner.

This is an explicitly declared, unloaded divider; independent function checking
is a later stage, not inferred from port declarations or successful CAD.
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def pin(ref,n):return dict(ref=ref,number=n)
def voltage(lo,hi):return dict(minimum=lo,maximum=hi,unit='V')


async def main(output):
    output.mkdir(parents=True,exist_ok=False)
    os.environ['PCB_MCP_DATA_DIR']=str(output/'data')
    config=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env=dict(os.environ))
    async with stdio_client(config) as (reader,writer),ClientSession(reader,writer) as session:
        await session.initialize()
        transcript=[]
        async def call(tool,request):
            reply=await session.call_tool(tool,dict(request=request))
            transcript.append(dict(tool=tool,arguments=request,response=reply.model_dump(mode='json')))
            write(output/'composition-transcript.json',transcript)
            assert not reply.isError,str(reply)
            return reply.structuredContent['data']
        async def component(mpn,ref,value=None):
            item=(await call('parts_search',dict(query=mpn)))['candidates'][0]
            item=await call('part_get',dict(candidate_id=item['candidate_id']))
            return dict(ref=ref,mpn=item['mpn'],part_definition_hash=item['definition_hash'],value=value)
        header=await component('61300211121','J1')
        resistor=await component('CRCW0603100KFKEA','R1','100k')
        r2=dict(resistor,ref='R2')
        ground=lambda pins:dict(name='ground',kind='reference',direction='passive',pins=pins,basis='Common return specified by the development fixture')
        source=dict(id='source',function='Declared external DC supply boundary',components=[header],ports=[
            dict(name='supply',kind='power',direction='output',pins=[pin('J1','1')],reference='ground',drive_voltage=voltage(4.75,5.25),available_current=dict(value=20.,unit='mA'),basis='Explicit external source assumption for development'),ground([pin('J1','2')])])
        divider=dict(id='divider',function='Passive resistive division, unloaded output',components=[resistor,r2],nets=[
            dict(name='V5',kind='power',pins=[pin('R1','1')]),dict(name='VOUT',kind='signal',pins=[pin('R1','2'),pin('R2','1')]),dict(name='GND',kind='ground',pins=[pin('R2','2')])],ports=[
            dict(name='input',kind='power',direction='input',pins=[pin('R1','1')],reference='ground',accept_voltage=voltage(4.75,5.25),required_current=dict(value=1.,unit='mA'),basis='Declared conservative module input budget, pending independent calculation'),
            dict(name='output',kind='signal',direction='output',pins=[pin('R1','2')],reference='ground',drive_voltage=voltage(2.3,2.7),available_current=dict(value=0.,unit='A'),basis='Proposed voltage interval to independently verify; unloaded output only'),ground([pin('R2','2')])])
        load=dict(id='measurement',function='Unloaded output measurement boundary',components=[header],ports=[
            dict(name='input',kind='signal',direction='input',pins=[pin('J1','1')],reference='ground',accept_voltage=voltage(2.3,2.7),required_current=dict(value=0.,unit='A'),basis='Ideal infinite impedance measurement boundary for this fixture'),ground([pin('J1','2')])])
        proposal=dict(name='Composed external source resistive divider and output boundary',
            intent=dict(description='Provide an unloaded 2.3 to 2.7 volt output from an external 4.75 to 5.25 volt source using a passive divider. This development case claims static operation only.',
                requirements=[dict(id='output_voltage',description='Static unloaded output stays between 2.3 and 2.7 volts.',metric='voltage',target=['divider','output'],allowed=voltage(2.3,2.7),conditions=dict(input_voltage=voltage(4.75,5.25)))]),
            modules=[source,divider,load],connections=[dict(ports=[['source','supply'],['divider','input']]),dict(ports=[['divider','output'],['measurement','input']]),dict(ports=[['source','ground'],['divider','ground'],['measurement','ground']])],
            settings=[dict(tool='board_constraints_set',args=dict(width_mm=60.,height_mm=40.,clearance_mm=.15,track_width_mm=.2)),
                      dict(tool='power_source_set',args=dict(net_id={'$port_net':['source','supply']},ground_net_id={'$port_net':['source','ground']},boundary_pin={'$port_pin':['source','supply']},nominal_v=5.,reason='Declared external 5V nominal source; range remains in frozen formal requirements.')),
                      dict(tool='power_envelope_set',args=dict(positive_pin={'$port_pin':['source','supply']},ground_net_id={'$port_net':['source','ground']},nominal_v=5.,min_v=4.75,max_v=5.25,available_current_ma=20.,ambient_c=25.,origin='proposed_assumption',reason='Explicit source range from the frozen development requirement.'))])
        write(output/'proposal.json',proposal)
        bad=copy.deepcopy(proposal)
        bad['modules'][0]['ports'][0]['drive_voltage']=voltage(12.,12.)
        rejected=await call('design_compose',bad)
        assert rejected['verdict']=='fail' and rejected['plan'] is None
        result=await call('design_compose',proposal)
        assert result['verdict']=='pass' and result['real_parts_and_pin_coverage_verified'],result
        assert not result['functional_requirements_verified']
        write(output/'composition.json',result)
        write(output/'plan.json',result['plan'])
        (output/'requirement.json').write_text(result['requirement_json'])
    from pcb_mcp_client.run_plan import execute
    run=await execute(output/'plan.json',output/'requirement.json',output/'cad')
    assert run['status']=='cad_checks_passed',run
    write(output/'verification.json',dict(passed=True,composed_modules=3,source_selected_components=4,incorrect_source_rejected=True,
        cad=run,physical_verified=False,function_qualified=False,source_condition='Unloaded static divider development fixture, not a held-out generalization case'))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(main(args.output.resolve()))
