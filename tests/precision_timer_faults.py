"""Actual source-bound board and fault injection; no CAD or saved-state mutation."""
import copy,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import precision_timer as pt,timer_models,electrical
W=R.parents[1]/'work/pulse-precision-redesign'

class PrecisionTimer(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base=json.loads((W/'attempt-01/state.json').read_text())
 def setUp(self):self.s=copy.deepcopy(self.base)
 def c(self,ref):return next(c for c in self.s['components'].values() if c['ref']==ref)
 def binding(self,ref):return self.s['part_bindings'][self.c(ref)['id']]
 def codes(self):return {f['code'] for f in timer_models.analyze(self.s)['findings']}
 def args(self):return [copy.deepcopy(self.binding(r)['facts']) for r in ['U1','R1','R2','R3','R4']]+[[4.75,5.25]]
 def req(self,key,value):next(r for r in self.s['requirements'] if r['key']==key)['value']=value
 def test_actual_model_and_limits(self):
  r=timer_models.analyze(self.s);self.assertFalse(r['findings']);t=r['timers'][0]
  self.assertTrue(40<t['frequency_reference_interval_hz'][0]<t['frequency_reference_interval_hz'][1]<60)
  self.assertTrue(.6<t['high_duty_reference_interval'][0]<t['high_duty_reference_interval'][1]<.75)
  self.assertFalse(t['full_function_qualified']);self.assertEqual(electrical.evaluate(self.s)['verdict'],'incomplete')
 def test_wrong_part_number_cannot_inherit_evidence(self):
  self.binding('U1')['mpn']='LTC6992IS6-2#TRMPBF';self.assertIn('PRECISION_TIMER_SOURCE',self.codes())
 def test_wrong_resistor_value(self):
  self.c('R1')['value']='220k';self.assertIn('PRECISION_TIMER_PASSIVE_SOURCE',self.codes())
 def test_source_fact_tampering(self):
  self.binding('R1')['facts']['tolerance_fraction']=0;self.assertIn('PRECISION_TIMER_PASSIVE_SOURCE',self.codes())
 def test_bypass_removed(self):
  del self.s['components'][self.c('C1')['id']];self.assertIn('PRECISION_TIMER_RESISTOR_NETWORK',self.codes())
 def test_mod_external_load(self):
  n=next(n for n in self.s['nets'].values() if n['name']=='MOD');n['pins'].append({'component_id':self.c('J2')['id'],'pin_id':'test-extra-load'})
  self.assertIn('PRECISION_TIMER_EXTRA_LOAD',self.codes())
 def test_supply_outside_range(self):
  self.s['power_envelope']['max_v']=5.6;self.assertIn('PRECISION_TIMER_SUPPLY',self.codes())
 def test_missing_pin(self):
  cid=self.c('U1')['id'];pin=next(p['pin_id'] for p in self.c('U1')['pins'] if p['number']=='3')
  for n in self.s['nets'].values():n['pins']=[p for p in n['pins'] if (p['component_id'],p['pin_id'])!=(cid,pin)]
  self.assertIn('PRECISION_TIMER_CONNECTION',self.codes())
 def test_nominal_inside_but_interval_outside(self):
  self.req('pulse_frequency_min_hz',49);self.req('pulse_frequency_max_hz',51)
  self.assertIn('PRECISION_TIMER_INTERVAL_MISMATCH',self.codes());self.assertNotIn('TIMER_NOMINAL_REQUIREMENT_MISMATCH',self.codes())
 def test_divider_midpoint_is_not_a_valid_code(self):
  a=self.args();a[4]=copy.deepcopy(a[3])
  with self.assertRaisesRegex(ValueError,'DIVCODE_MARGIN'):pt.intervals(*a)
 def test_divider_swap_inverts_duty(self):
  a=self.args();normal=pt.intervals(*a);a[3],a[4]=a[4],a[3];inverse=pt.intervals(*a)
  self.assertEqual(inverse['divcode'],8);self.assertEqual(normal['ndiv'],inverse['ndiv'])
  self.assertAlmostEqual(normal['nominal_high_duty']+inverse['nominal_high_duty'],1)
 def test_setting_network_outside_supported_range(self):
  a=self.args();a[1]['value_ohm']=1000;a[2]['value_ohm']=1000
  with self.assertRaisesRegex(ValueError,'RSET_RANGE'):pt.intervals(*a)
 def test_ref_names_do_not_define_topology(self):
  for c in self.s['components'].values():c['ref']='renamed_'+c['ref']
  self.assertFalse(pt.analyze(self.s)['findings'])
if __name__=='__main__':unittest.main(verbosity=2)
