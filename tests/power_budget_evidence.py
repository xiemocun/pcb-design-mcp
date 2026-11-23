"""Isolated arithmetic/source/graph failures. No shared evidence is changed."""
import copy,json,sys,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pcb_mcp import parts,power_budget,usb_gate_contract as gate
from pcb_mcp.service import Service
from pcb_mcp.models import PowerBudgetReport,RemoveComponent,RemoveNet
from pcb_mcp.common import DomainError
R=Path(__file__).resolve().parents[1]
main=json.loads((R/'acceptance-power-budget/report.json').read_text());s=json.loads((R/'data'/main['project_id']/'state.json').read_text());cat=parts.catalog();checks=[]
# Cache validated immutable records for pure in-memory faults; real protocol calls
# independently read the actual evidence on every check.
with patch.object(parts,'catalog',return_value=cat):
 svc=Service.__new__(Service)
 def ev(state):
  result=power_budget.evaluate(state,svc.bom_check_state(state));PowerBudgetReport(**result);return result
 def bad(state,code):
  result=ev(state);assert result['budget_verdict']=='fail' and code in {f['code'] for f in result['findings']},(code,result);checks.append(code);return result
 base=ev(s);assert base['budget_verdict']=='conditional'
 byref={c['ref']:c for c in s['components'].values()};load=s['power_budget_policy']['loads'][0]
 # Hand-check the source delta from adding100mA only on the3.3V main rail.
 # The same increment must reach the switch and source once, with buck loss.
 t=copy.deepcopy(s);t['power_budget_policy']['loads'][0]['max_current_ma']+=100
 changed=ev(t);buck=next(x for x in base['stages'] if x['kind']=='buck');rails={x['name']:x['supply_reference'] for x in base['rails']}
 expected_delta=.1*3.5475/(rails['VUSB']['min_v']*.8)
 assert abs((changed['declared_dc_reference_input_a']-base['declared_dc_reference_input_a'])-expected_delta)<1e-12;checks.append('downstream_load_propagates_once_including_conversion_loss')
 # Pure traversal order changes must not change the final total.
 t=copy.deepcopy(s);t['components']=dict(reversed(list(t['components'].items())))
 assert abs(ev(t)['declared_dc_reference_input_a']-base['declared_dc_reference_input_a'])<1e-12;checks.append('component_order_independent_budget')
 t=copy.deepcopy(s);t['part_bindings'][byref['R11']['id']]['facts']['tolerance_fraction']=.001
 assert gate.current_limit(t) is None;bad(t,'BUDGET_PART_UNRESOLVED');checks.append('forged_tolerance_not_silently_used')
 stale=copy.deepcopy(cat);next(p for p in stale if p['mpn']=='CRCW060330K1FKEA')['evidence_status']='missing_or_changed'
 with patch.object(parts,'catalog',return_value=stale):
  assert gate.current_limit(s) is None;bad(s,'BUDGET_PART_UNRESOLVED');checks.append('missing_source_fails_closed_without_cache_file_changes')
 t=copy.deepcopy(s);t['power_budget_policy']['stages'][0]['efficiency_lower_bound']=None;bad(t,'BUDGET_STAGE_STALE')
 t=copy.deepcopy(s);t['power_budget_policy']['stages'][0]['efficiency_lower_bound']=.1;bad(t,'BUDGET_STAGE_CAPACITY')
 # Reconnect buck IN to its own OUT in memory, removing its old membership.
 t=copy.deepcopy(s);c=byref['U3'];inp=next(p for p in c['pins'] if p['name']=='IN');outp=next(p for p in c['pins'] if p['name']=='OUT');pin=dict(component_id=c['id'],pin_id=inp['pin_id']);outpin=dict(component_id=c['id'],pin_id=outp['pin_id'])
 target=next(n for n in t['nets'].values() if outpin in n['pins'])
 for n in t['nets'].values():n['pins']=[p for p in n['pins'] if p!=pin]
 target['pins'].append(pin);bad(t,'BUDGET_RAIL_CYCLE')
 t=copy.deepcopy(s);unknown=copy.deepcopy(byref['U1']);unknown.update(id='f'*32,ref='Q1',library_id='Transistor_BJT:Q_NPN');t['components'][unknown['id']]=unknown;bad(t,'BUDGET_PROFILE_UNSUPPORTED');checks.append('unknown_active_part_rejected_regardless_of_reference_prefix')
 # Omitted parts/load domains stay incomplete, never a physical pass.
 t=copy.deepcopy(s);t['part_bindings'].pop(byref['U1']['id']);report=ev(t);assert report['budget_verdict']=='incomplete' and report['actual_input_current_upper_bound_a'] is None;checks.append('unbound_domain_cannot_complete_budget')
 # New reference guards are tested with all older references deliberately removed.
 with tempfile.TemporaryDirectory(prefix='pcb-budget-guards-') as folder:
  svc.root=Path(folder);t=copy.deepcopy(s);t['revision']=0;t['idempotency']={};t['nets']={};t['power_sources']={};t['constraints']={};t['pin_intents']={};t['footprint_bindings']={};t['part_bindings']={};t['passive_rating_requirements']={}
  svc.project_path(t['id']).mkdir();svc.save(t)
  try:svc.remove_component(RemoveComponent(project_id=t['id'],expected_revision=0,idempotency_key='guard-component',component_id=byref['U1']['id']))
  except DomainError as e:assert e.payload['code']=='ENTITY_IN_USE'
  else:raise AssertionError('budget reference removal was accepted')
  nid=s['power_budget_policy']['reserves'][0]['net_id'];t['nets']={nid:dict(id=nid,name='reserved',kind='power',pins=[])};svc.save(t)
  try:svc.remove_net(RemoveNet(project_id=t['id'],expected_revision=0,idempotency_key='guard-net',net_id=nid))
  except DomainError as e:assert e.payload['code']=='ENTITY_IN_USE'
  else:raise AssertionError('reserved power net removal was accepted')
  checks.extend(['component_budget_reference_guard','net_budget_reserve_guard'])
result=dict(passed=True,checks=checks,count=len(checks),shared_evidence_cache_modified=False,actual_geometry_retested=False)
(R/'acceptance-power-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
