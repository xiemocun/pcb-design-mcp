"""In-memory source/topology faults and known capacitor arithmetic. No shared files changed."""
import copy,json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pcb_mcp import parts,power_startup
from pcb_mcp.service import Service
from pcb_mcp.models import StartupReport,StartupCheck
from pydantic import ValidationError
R=Path(__file__).resolve().parents[1];fixture=json.loads((R/'acceptance-startup/report.json').read_text());s=json.loads((R/'data'/fixture['project_id']/'state.json').read_text());request=fixture['scenario_request'];cat=parts.catalog();checks=[];svc=Service.__new__(Service)
with patch.object(parts,'catalog',return_value=cat):
 def ev(state):
  r=power_startup.evaluate(state,svc.bom_check_state(state),assumptions=request['slew_assumptions'],origin=request['origin'],reason=request['reason']);StartupReport(**r);assert r['actual_peak_input_current_upper_bound_a'] is None and r['actual_startup_time_upper_bound_ms'] is None and not r['physical_qualified'];return r
 def bad(state,code):
  r=ev(state);assert r['reference_slew_verdict']=='fail' and code in {f['code'] for f in r['findings']},(code,r);checks.append(code)
 base=ev(s);assert base['reference_slew_verdict']=='conditional';rows={r['name']:r for r in base['rails']}
 assert abs(rows['VUSB']['nominal_tolerance_capacitance_uf']-(27*1.2+1*1.1+.1*1.1+.1*1.1))<1e-10;checks.append('parallel_caps_each_counted_once_with_selected_tolerances')
 assert abs(rows['VUSB']['local_capacitor_current_reference_a']-.03372)<1e-12;checks.append('one_volt_per_millisecond_yields33point72mA_local_charge')
 assert abs(rows['VBUS_RAW']['local_capacitor_current_reference_a']-.0011)<1e-12 and abs(rows['VUSB']['capacitor_charge_reference_c']-177.03e-6)<1e-12;checks.append('ampere_millisecond_and_coulomb_units_correct')
 assert next(c for c in base['capacitors'] if c['reference']=='C6')['scope']=='signal_or_unsupported_connection';checks.append('reset_cap_is_explicitly_excluded_not_silently_counted_as_power')
 byref={c['ref']:c for c in s['components'].values()}
 t=copy.deepcopy(s);p=t['part_bindings'][byref['C9']['id']];p['facts']['capacitance_f']=1e-9;bad(t,'STARTUP_CAP_RATING_UNKNOWN');checks.append('forged_small_capacitance_cannot_reduce_reference_current')
 t=copy.deepcopy(s);t['part_bindings'].pop(byref['C9']['id']);r=ev(t);assert r['reference_slew_verdict']=='incomplete' and not next(x for x in r['rails'] if x['name']=='VUSB')['capacitor_inventory_complete'];checks.append('unbound_cap_never_becomes_zero_or_conditional_pass')
 # Swap the polarity in the structured specification only.
 t=copy.deepcopy(s);cap=byref['C9'];a,b=[dict(component_id=cap['id'],pin_id=p['pin_id']) for p in cap['pins']]
 for n in t['nets'].values():n['pins']=[b if p==a else a if p==b else p for p in n['pins']]
 bad(t,'STARTUP_CAP_REVERSED')
 # Ground terminal replaced by a different supply is not a parallel-to-ground cap.
 t=copy.deepcopy(s);cap=byref['C5'];pin=dict(component_id=cap['id'],pin_id=cap['pins'][1]['pin_id'])
 for n in t['nets'].values():n['pins']=[p for p in n['pins'] if p!=pin]
 next(n for n in t['nets'].values() if n['name']=='V3V3')['pins'].append(pin);bad(t,'STARTUP_CAP_TOPOLOGY_UNSUPPORTED')
 t=copy.deepcopy(s);cap=byref['C6'];pin=dict(component_id=cap['id'],pin_id=cap['pins'][0]['pin_id'])
 for n in t['nets'].values():n['pins']=[p for p in n['pins'] if p!=pin]
 bad(t,'STARTUP_CAP_UNCONNECTED')
 rule,ok=power_startup.reference_rules();assert ok
 with patch.object(power_startup,'reference_rules',return_value=(rule,False)):bad(s,'STARTUP_SOURCE_CHANGED')
 checks.append('formula_or_datasheet_source_loss_in_memory_fails_closed')
 # The source/table never produces a typical-to-peak conversion.
 assert all(r['max_slew_assumption_v_per_ms'] is not None for r in base['rails'])
 empty=power_startup.evaluate(s,svc.bom_check_state(s));assert empty['reference_slew_verdict']=='incomplete' and all(r['local_capacitor_current_reference_a'] is None for r in empty['rails']);checks.append('typical_or_maximum_rise_time_not_turned_into_peak_slew_bound')
 # State order cannot change capacitor inventory totals.
 t=copy.deepcopy(s);t['components']=dict(reversed(list(t['components'].items())));other=ev(t);otherrows={r['name']:r for r in other['rails']}
 assert all(abs(row['local_capacitor_current_reference_a']-otherrows[name]['local_capacitor_current_reference_a'])<1e-12 for name,row in rows.items());checks.append('component_order_independent_local_charge')
 for value in [float('nan'),float('inf'),-1.]:
  badrequest=copy.deepcopy(request);badrequest['slew_assumptions'][0]['max_slew_v_per_ms']=value
  try:StartupCheck(**badrequest)
  except ValidationError:pass
  else:raise AssertionError('nonfinite or negative slew accepted')
 checks.append('nonfinite_and_negative_slew_rejected')
result=dict(passed=True,count=len(checks),checks=checks,shared_evidence_cache_modified=False,project_states_modified=False,actual_geometry_retested=False)
(R/'acceptance-startup-evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
