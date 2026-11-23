import asyncio,json
from riding_cost_common import run,OUT
async def main(c):
 s=await c.state();pcb=json.loads((OUT/'pcb-01/pcb_build.json').read_text())['pcb_build_id']
 for tool,args in [('design_audit',dict(project_id=c.pid,revision=c.rev,pcb_build_id=pcb)),('design_readiness',dict(project_id=c.pid,revision=c.rev))]:
  r=await c.call(tool,**args);(OUT/'pcb-01'/(tool+'.json')).write_text(json.dumps(r,indent=2));print(tool,r.get('status'),r.get('blockers'),flush=True)
if __name__=='__main__':asyncio.run(run(main,'audit.jsonl'))
