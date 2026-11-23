"""Fault injection into an actual empty-project design; no production state edits."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/fresh-design-benchmark';sys.path.insert(0,str(R/'src'))
from pcb_mcp import timer_models,parts,led_branches,power_budget
from pcb_mcp.service import Service
class Faults(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.s=json.loads((Path((W/'latest-design.txt').read_text())/'state.json').read_text());cls.tmp=tempfile.TemporaryDirectory();cls.service=Service(cls.tmp.name)
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):self.s=copy.deepcopy(self.__class__.s)
 def c(self,ref):return next(c for c in self.s['components'].values() if c['ref']==ref)
 def pin(self,ref,num):
  c=self.c(ref);return dict(component_id=c['id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
 def move(self,ref,num,nname):
  pin=self.pin(ref,num)
  for n in self.s['nets'].values():n['pins']=[p for p in n['pins'] if p!=pin]
  next(n for n in self.s['nets'].values() if n['name']==nname)['pins'].append(pin)
 def codes(self):return {f['code'] for f in timer_models.analyze(self.s)['findings']}
 def test_nominal_period_independent_formula(self):
  r=timer_models.analyze(self.s);self.assertFalse(r['findings']);row=r['timers'][0];self.assertAlmostEqual(row['nominal_frequency_hz'],1.4426950408889634/(210000*10e-6),12);self.assertAlmostEqual(row['nominal_high_seconds']/row['nominal_low_seconds'],1.1,12)
 def test_reset_at_ground_cannot_pass(self):self.move('U1','4','GND');self.assertIn('TIMER_ASTABLE_CONNECTION',self.codes())
 def test_separate_trigger_threshold_cannot_pass(self):self.move('U1','6','CONTROL');self.assertIn('TIMER_ASTABLE_CONNECTION',self.codes())
 def test_no_charge_path_cannot_pass(self):self.move('R1','1','GND');self.assertIn('TIMER_ASTABLE_PASSIVES',self.codes())
 def test_missing_supply_bypass_cannot_pass(self):self.move('C3','1','CONTROL');self.assertIn('TIMER_ASTABLE_PASSIVES',self.codes())
 def test_actual_bound_led_chain(self):
  r=led_branches.evaluate(self.service,self.s);self.assertEqual(r['verdict'],'pass');self.assertLess(r['calculations']['led_branches'][0]['dc_current_upper_reference_a'],.008)
 def test_single_low_resistor_rejected_even_with_valid_bindings(self):
  # Bypass R4 physically, retaining valid manufacturer bindings on all parts.
  self.move('R3','2','LED_A');self.move('R4','1','GND');self.move('R4','2','GND');r=led_branches.evaluate(self.service,self.s);self.assertEqual(r['verdict'],'fail');self.assertIn('LED_BRANCH_CURRENT_MARGIN',{f['code'] for f in r['findings']})
 def test_unknown_sink_rejected(self):
  del self.s['part_bindings'][self.c('U1')['id']];r=led_branches.evaluate(self.service,self.s);self.assertEqual(r['verdict'],'fail');self.assertIn('LED_SINK_MODEL_REQUIRED',{f['code'] for f in r['findings']})
 def test_branch_at_led_anode_rejected(self):self.move('C2','1','LED_A');self.assertEqual(led_branches.evaluate(self.service,self.s)['verdict'],'fail')
 def test_valid_color_binding_does_not_allow_wrong_color(self):
  c=self.c('D1');p=self.s['part_bindings'][c['id']];self.assertTrue(parts.binding_valid(self.s,c,p));c['value']='Blue';self.assertFalse(parts.binding_valid(self.s,c,p))
 def test_unsupported_temperature_cannot_pass(self):
  self.s['power_envelope']['ambient_c']=85.;r=led_branches.evaluate(self.service,self.s);self.assertEqual(r['verdict'],'fail');self.assertIn('LED_BRANCH_TEMPERATURE_UNSUPPORTED',{f['code'] for f in r['findings']})
 def test_no_conversion_stage_still_checks_source_capacity(self):
  self.s['power_envelope']['available_current_ma']=1;r=power_budget.evaluate(self.s,self.service.bom_check_state(self.s));self.assertEqual(r['budget_verdict'],'fail');self.assertIn('BUDGET_SOURCE_CAPACITY',{f['code'] for f in r['findings']})
if __name__=='__main__':unittest.main(verbosity=2)
