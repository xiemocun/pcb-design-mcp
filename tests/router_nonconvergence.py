"""Record actual three-attempt UART_TX nonconvergence and verify refusal gates.

A passing gate test is explicitly NOT a successful router repair.
"""
from pathlib import Path
import json,sys
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp.service import Service
from pcb_mcp.models import PCBRef
from pcb_mcp.common import DomainError
main=json.loads((R/'acceptance-bom/report.json').read_text());pid=main['project_id']
candidates=[]
for run in (R/'acceptance-connectivity-retry').glob('run-*'):
 state=json.loads((run/pid/'state.json').read_text());children=[b for b in state['pcb_builds'].values() if b.get('route_repair_parent') and 'drc' in b['checks']]
 if len(children)>=3 and all(b['checks'].get('drc',{}).get('verdict')=='fail' for b in children):candidates.append((run,state,children))
assert candidates,'Expected real three-attempt unresolved UART fixture'
run,s,children=candidates[0];service=Service(run);observations=[]
for b in children:
 pt=PCBRef(project_id=pid,pcb_build_id=b['id']);service.pcb_target(pt)
 report=json.loads((run/pid/'pcb_builds'/b['id']/'drc_report.json').read_text());assert report['verdict']=='fail' and report['details']['unconnected_count']>0
 assert all(f['type']=='unconnected_items' and any('UART_TX' in i.get('description','') for i in f['items']) for f in report['findings'])
 try:service.pcb_review_export(pt);raise AssertionError('Unresolved DRC exported as passing review')
 except DomainError as e:assert e.payload['code']=='PCB_VALIDATION_REQUIRED'
 observations.append(dict(pcb_build_id=b['id'],actual_drc=report,review_export_refused=True))
original=Service(R/'data');original.pcb_target(PCBRef(project_id=pid,pcb_build_id=main['pcb_build_id']))
result=dict(passed=True,test_purpose='fail_closed_gate_only_not_repair_success',router_converged=False,router_attempts=len(children),fixture_net='UART_TX',data_root=str(run),observations=observations,accepted_reference_unchanged=True,new_gerber_generated=False,manufacturing_approved=False,limitation='Freerouting seeded retry did not repair this long missing net in three bounded attempts; actual KiCad DRC remains failing. No guarantee of automatic repair.')
(R/'acceptance-router-nonconvergence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='observations'},indent=2))
