"""Known wrong connections and boundary claims on the actual empty-project spec."""
import copy,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/analog-buffer-benchmark';sys.path.insert(0,str(R/'src'))
from pcb_mcp import analog_models,electrical,power_paths,power_integrity,prototype_policy
from pcb_mcp.service import Service
class Analog(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base=json.loads((Path((W/'latest-design.txt').read_text())/'state.json').read_text())
 def setUp(self):self.s=copy.deepcopy(self.base)
 def c(self,ref):return next(c for c in self.s['components'].values() if c['ref']==ref)
 def move(self,ref,num,name):
  c=self.c(ref);p=dict(component_id=c['id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
  for n in self.s['nets'].values():n['pins']=[q for q in n['pins'] if q!=p]
  if name:next(n for n in self.s['nets'].values() if n['name']==name)['pins'].append(p)
 def req(self,k,v):next(x for x in self.s['requirements'] if x['key']==k)['value']=v
 def bad(self,code):
  a=analog_models.analyze(self.s);self.assertIn(code,{f['code'] for f in a['findings']});self.assertEqual(electrical.evaluate(self.s)['verdict'],'fail')
 def test_nominal_is_ideal_not_guaranteed_accuracy(self):
  self.assertEqual(electrical.evaluate(self.s)['verdict'],'pass')
  a=analog_models.analyze(self.s);self.assertFalse(a['findings']);q=a['amplifiers'][0];self.assertEqual(q['dc_reference_cases'],[dict(input_v=x,ideal_output_v=x) for x in [.5,2.25,4.]]);self.assertIsNone(q['guaranteed_total_tracking_error_v']);self.assertFalse(q['dynamic_or_stability_qualified']);self.assertFalse(q['physical_verified'])
 def test_positive_feedback_rejected(self):self.move('U1','1','ANALOG_OUT');self.move('U1','3','ANALOG_IN');self.bad('ANALOG_NEGATIVE_FEEDBACK_REQUIRED')
 def test_missing_feedback_rejected(self):self.move('U1','3',None);self.bad('ANALOG_NEGATIVE_FEEDBACK_REQUIRED')
 def test_negative_supply_disconnected_rejected(self):self.move('U1','2',None);self.bad('ANALOG_NEGATIVE_FEEDBACK_REQUIRED')
 def test_negative_supply_on_signal_rejected(self):self.move('U1','2','CC1');self.bad('ANALOG_SINGLE_SUPPLY_REQUIRED')
 def test_actual_source_identity_required(self):del self.s['part_bindings'][self.c('U1')['id']];self.bad('ANALOG_SOURCE_REQUIRED')
 def test_port_polarity_reversed_rejected(self):self.move('J2','1','GND');self.move('J2','2','ANALOG_IN');self.bad('ANALOG_PORT_MAPPING')
 def test_no_bypass_rejected(self):self.move('C1','1','CC1');self.move('C2','1','CC2');self.move('C3','1','CC2');self.bad('ANALOG_BYPASS_REQUIRED')
 def test_extra_output_load_rejected(self):self.move('C2','1','ANALOG_OUT');self.bad('ANALOG_OUTPUT_LOAD_UNSUPPORTED')
 def test_extra_input_load_rejected(self):self.move('C2','1','ANALOG_IN');self.bad('ANALOG_INPUT_LOAD_UNSUPPORTED')
 def test_input_outside_rail_rejected(self):self.req('analog_input_max_v',5.);self.bad('ANALOG_RAIL_HEADROOM')
 def test_supply_above_recommended_rejected(self):self.s['power_envelope']['max_v']=6.;self.bad('ANALOG_SUPPLY_RANGE')
 def test_missing_or_nonfinite_boundaries_rejected(self):
  for x in [None,True,'1000',float('nan'),float('inf')]:
   self.req('analog_source_max_ohm',x);self.bad('ANALOG_BOUNDARY_REQUIRED')
 def test_heavy_and_capacitive_loads_rejected(self):
  self.req('analog_external_load_min_ohm',1000.);self.bad('ANALOG_BOUNDARY_OUT_OF_SCOPE');self.req('analog_external_load_min_ohm',100000.);self.req('analog_external_cap_max_pf',101.);self.bad('ANALOG_BOUNDARY_OUT_OF_SCOPE')
 def test_usb_errors_block_prototype_review_without_downgrade(self):
  r=prototype_policy.assessment(Service.__new__(Service),self.s);self.assertEqual(r['verdict'],'fail');self.assertIn('usb_power',r['reports']);codes={f['code'] for f in r['findings'] if f['severity']=='error'};self.assertTrue({'USB_EXTERNAL_INPUT_BACKFEED_PATH','USB_PSD_CURRENT_BOUND_UNPROVEN','USB_PSD_ATTACH_LOAD_UNPROVEN','USB_PSD_EFFECTIVE_CAPACITANCE_UNPROVEN'}<=codes)
 def test_controlled_mainboard_remains_review_eligible_not_physical(self):
  s=json.loads((R/'data/fb639744d29945359f393e331cfb34ab/state.json').read_text());r=prototype_policy.assessment(Service.__new__(Service),s);self.assertEqual(r['verdict'],'eligible_for_prototype_review');self.assertFalse(r['physical_qualified']);self.assertFalse(any(f['severity']=='error' for f in r['reports']['usb_power']['findings']))
 def test_valid_different_input_not_fixed_to_benchmark_numbers(self):
  self.req('analog_input_min_v',1.);self.req('analog_input_max_v',3.);a=analog_models.analyze(self.s);self.assertFalse(a['findings']);self.assertEqual(a['amplifiers'][0]['input_range_v'],[1.,3.])
if __name__=='__main__':unittest.main(verbosity=2)
