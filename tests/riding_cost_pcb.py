"""Actual schematic and PCB backend attempt; stops at failed checks.
No signature, Gerber, manufacture approval, or order is performed here.
"""
import asyncio,json,os,sys,uuid
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from riding_cost_common import ROOT
PID='89184e4c1e4740f88be798dfed22a98b'

async def main():
    number=1
    while (ROOT/f'cost-down/pcb-{number:02d}').exists():number+=1
    out=ROOT/f'cost-down/pcb-{number:02d}';out.mkdir()
    server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(ROOT/'data')})
    async with stdio_client(server) as (r,w),ClientSession(r,w) as s:
        await s.initialize()
        async def call(tool,args):
            reply=await s.call_tool(tool,{'request':args},read_timeout_seconds=timedelta(seconds=300))
            with (out/'transcript.jsonl').open('a') as log:log.write(json.dumps(dict(tool=tool,request=args,response=reply.model_dump(mode='json')),ensure_ascii=False)+'\n')
            if reply.isError:raise RuntimeError(str(reply))
            result=reply.structuredContent['data'];name=tool+('_'+args['stage'] if 'stage' in args else '')
            print(name,result.get('verdict','complete'),flush=True)
            (out/(name+'.json')).write_text(json.dumps(result,indent=2,ensure_ascii=False))
            return result
        state=await call('project_read',dict(project_id=PID));rev=state['revision'];nets={n['name']:n['id'] for n in state['nets'].values()}
        async def mutate(tool,**args):
            nonlocal rev
            result=await call(tool,dict(project_id=PID,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**args));rev=result['revision'];return result
        geometry=json.loads((ROOT/'cost-down/repaired-copper-proposal.json').read_text())
        keep=[dict(x_min_mm=24.9,y_min_mm=42.8,x_max_mm=26.1,y_max_mm=47.2,no_tracks=True,no_vias=True,no_fills=True,reason='Manufacturer74438357010 page1: no vias/traces under inductor; center gap between electrical lands')]
        await mutate('board_constraints_set',width_mm=90.,height_mm=65.,edge_margin_mm=5.,clearance_mm=.15,track_width_mm=.15,min_drill_mm=.2,placement_profile='explicit',placements=geometry['placements'],ground_plane_net_id=nets['GND'],ground_plane_reason='Physical connected two-layer ground polygons and local stitching; independent native return geometry verification',copper_keepouts=keep,local_routes=geometry['local_routes'],local_vias=geometry['local_vias'])
        policy=json.loads((ROOT/'hardware-circuit/copper-policy-proposal.json').read_text())
        await mutate('pcb_net_rules_set',**policy)
        sch=await mutate('schematic_build');st=dict(project_id=PID,build_id=sch['build_id'])
        for tool in ['schematic_verify','erc_run']:
            result=await call(tool,st)
            if result['verdict']!='pass':raise RuntimeError('Failed '+tool)
        await call('schematic_preview',st);await call('schematic_export',st)
        pre=await call('pcb_preflight',dict(project_id=PID))
        if pre['verdict']!='pass':raise RuntimeError('Failed PCB preflight')
        pcb=await mutate('pcb_build',schematic_build_id=sch['build_id']);pt=dict(project_id=PID,pcb_build_id=pcb['pcb_build_id'])
        for tool in ['pcb_verify','pcb_layout_check']:
            result=await call(tool,dict(**pt,stage='unrouted'))
            if result['verdict']!='pass':raise RuntimeError('Failed '+tool)
        await call('pcb_autoroute',dict(**pt,max_passes=30))
        for tool in ['pcb_verify','drc_run','pcb_layout_check','pcb_copper_check','pcb_preview']:
            result=await call(tool,dict(**pt,stage='routed') if tool in ['pcb_verify','pcb_layout_check'] else pt)
        await call('bom_draft_export',dict(project_id=PID))

if __name__=='__main__':asyncio.run(main())
