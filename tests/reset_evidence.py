"""Independent numerical RC check and isolated source/topology faults."""
import copy,json,math,sys,os
from pathlib import Path
from unittest.mock import patch
from pydantic import ValidationError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pcb_mcp import parts,reset_timing
from pcb_mcp.models import ResetCheck,ResetReport
from pcb_mcp.service import Service
R=Path(__file__).resolve().parents[1];a=json.loads((R/'acceptance-reset/report.json').read_text());s=json.loads((R/'data'/a['project_id']/'state.json').read_text());request=a['fast_request'];cat=parts.catalog();svc=Service.__new__(Service);checks=[]
def integrate(t,tau,rise,final,initial):
 # IndependentRK4 integrating dEN/dt=(Vin-EN)/tau; split at the ramp corner.
 v=initial;left=0.
 for right in sorted(set([min(t,rise),t])):
  if right<=left:continue
  h=(right-left)/4000
  def f(x,y):return (final if rise==0 else final*min(x/rise,1.) )/tau-y/tau
  for i in range(4000):
   x=left+i*h;k1=f(x,v);k2=f(x+h/2,v+h*k1/2);k3=f(x+h/2,v+h*k2/2);k4=f(x+h,v+h*k3);v+=h*(k1+2*k2+2*k3+k4)/6
  left=right
 return v
for tau,rise,t,initial in [(10.,0.,10.,0.),(8.91,.1,.15,0.),(11.11,100.,100.05,0.),(8.91,.001,10.,.5),(10.,4.,4.05,.2)]:
 exact=reset_timing.en_voltage(t,tau,rise,3.5475,initial);numerical=integrate(t,tau,rise,3.5475,initial);assert abs(exact-numerical)<2e-10,(tau,rise,exact,numerical)
checks.append('closed_form_matches_independent_RK4_over_step_fast_slow_and_precharged_cases')
assert abs(reset_timing.crossing_ms(.6,10.,0.,3.3,0.)+10*math.log(1-.6/3.3))<1e-12
assert abs(reset_timing.crossing_ms(.75*3.3,10.,0.,3.3,0.)-10*math.log(4))<1e-12;checks.append('low_and_high_threshold_step_times_match_closed_form_golden_values')
for rise in [1e-14,1e-10,1e-6]:assert abs(reset_timing.en_voltage(.15,8.91,rise,3.5475,0.)-reset_timing.en_voltage(.15,8.91,0.,3.5475,0.))<3e-7
checks.append('tiny_ramp_durations_stable_no_catastrophic_subtraction')
with patch.object(parts,'catalog',return_value=cat):
 def ev(state,scenario=None):
  r=reset_timing.evaluate(state,svc.bom_check_state(state),scenario=scenario or request['scenario'],origin=request['origin'],reason=request['reason']);ResetReport(**r);assert not r['physical_qualified'] and r['actual_reset_release_time_bounds_ms'] is None;return r
 def bad(state,code,scenario=None):
  r=ev(state,scenario);assert r['reference_timing_verdict']=='fail' and code in {f['code'] for f in r['findings']},(code,r);checks.append(code);return r
 assert ev(s)['reference_timing_verdict']=='conditional';byref={c['ref']:c for c in s['components'].values()}
 # A later75% crossing never proves the earlier reset-release ambiguity safe.
 borderline=dict(request['scenario'],supply_rise_ms=4.,supply_stable_at_ms=4.)
 r=bad(s,'RESET_LOW_HOLD_NOT_ESTABLISHED',borderline);assert all(x['first_to_final_supply_75pct_reference_ms']>r['calculation']['check_deadline_ms'] for x in r['corners']);checks.append('late_75percent_crossing_does_not_waive_early_low_level_loss')
 # Interior RC values and positive initial residuals stay below the conservative envelope.
 for initial in [0.,.2,.5]:
  scenario=dict(request['scenario'],initial_en_v=initial);r=ev(s,scenario);upper=r['calculation']['conservative_max_en_through_deadline_reference_v'];deadline=r['calculation']['check_deadline_ms']
  for i in range(31):
   tau=8.91+(11.11-8.91)*i/30
   for j in range(31):assert reset_timing.en_voltage(deadline*j/30,tau,.1,3.5475,initial)<=upper+1e-12
 checks.append('conservative_envelope_covers_interior_RC_and_initial_residual_samples')
 t=copy.deepcopy(s);c=byref['R4'];vdd=next(n for n in t['nets'].values() if n['name']=='V3V3');pin=next(p for p in vdd['pins'] if p['component_id']==c['id']);vdd['pins'].remove(pin);next(n for n in t['nets'].values() if n['name']=='VUSB')['pins'].append(pin);bad(t,'RESET_PULLUP_REQUIRED')
 t=copy.deepcopy(s);c=byref['C6']
 for n in t['nets'].values():
  if n['name']=='GND':n['pins']=[p for p in n['pins'] if p['component_id']!=c['id']]
 bad(t,'RESET_CAP_REQUIRED')
 t=copy.deepcopy(s);c=byref['U6'];pin=dict(component_id=c['id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['name']=='Y'))
 for n in t['nets'].values():n['pins']=[p for p in n['pins'] if p!=pin]
 next(n for n in t['nets'].values() if n['name']=='MCU_EN')['pins'].append(pin);bad(t,'RESET_ADDITIONAL_LOAD_UNSUPPORTED')
 t=copy.deepcopy(s);t['part_bindings'][byref['C6']['id']]['facts']['capacitance_f']=1e-12;bad(t,'RESET_PART_UNRESOLVED');checks.append('forged_capacitance_cannot_supply_a_false_delay')
 t=copy.deepcopy(s);t['part_bindings'].pop(byref['C6']['id']);r=ev(t);assert r['reference_timing_verdict']=='incomplete' and r['calculation'] is None;checks.append('unbound_RC_is_incomplete_not_zero_or_nominal_pass')
 rule,ok=reset_timing.reference_rules();assert ok
 with patch.object(reset_timing,'reference_rules',return_value=(rule,False)):bad(s,'RESET_REFERENCE_SOURCE_CHANGED')
 # Fresh catalog failure is simulated only in memory.
 stale=copy.deepcopy(cat);next(p for p in stale if p['mpn']=='C1608X5R1C105K080AA')['evidence_status']='missing_or_changed'
 with patch.object(parts,'catalog',return_value=stale):bad(s,'RESET_PART_UNRESOLVED')
 checks.append('source_failure_never_mutates_shared_cache')
 t=copy.deepcopy(s);t['power_envelope']['ambient_c']=85.;bad(t,'RESET_DC_REFERENCE_REQUIRED')
 for value in [float('nan'),float('inf'),-1.]:
  raw=copy.deepcopy(request);raw['scenario']['supply_rise_ms']=value
  try:ResetCheck(**raw)
  except ValidationError:pass
  else:raise AssertionError('nonfinite or negative ramp accepted')
 checks.append('nonfinite_and_negative_waveform_parameters_rejected')
result=dict(passed=True,count=len(checks),checks=checks,shared_evidence_cache_modified=False,project_states_modified=False,actual_geometry_retested=False)
Path(os.environ.get('PCB_MCP_RESET_EVIDENCE_OUT',str(R/'acceptance-reset-evidence.json'))).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
