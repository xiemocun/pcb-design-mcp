"""Declared function mismatch must stop before CAD, without changing the runner."""
import copy,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import timer_models,electrical
W=R.parents[1]/'work/pulse-plan-reuse'
class Requirements(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base=json.loads((W/'attempt-01/state.json').read_text())
 def setUp(self):self.s=copy.deepcopy(self.base)
 def set_(self,key,value):
  for r in self.s['requirements']:
   if r['key']==key:r['value']=value;return
  self.s['requirements'].append(dict(key=key,value=value,origin='proposed_assumption'))
 def codes(self):return {x['code'] for x in timer_models.analyze(self.s)['findings']}
 def test_actual_nominal_matches_but_not_qualified(self):
  r=timer_models.analyze(self.s);self.assertFalse(r['findings']);self.assertEqual(r['requirements']['status'],'nominal_reference_pass');self.assertFalse(r['requirements']['full_function_qualified'])
  self.assertAlmostEqual(r['timers'][0]['nominal_frequency_hz'],48.0898346963)
  self.assertEqual(electrical.evaluate(self.s)['verdict'],'incomplete')
 def test_actual_negative_record_caught(self):
  s=json.loads((W/'negative-before/state.json').read_text());r=electrical.evaluate(s)
  self.assertEqual(r['verdict'],'fail');self.assertIn('TIMER_NOMINAL_REQUIREMENT_MISMATCH',{f['code'] for f in r['findings']})
 def test_duty_mismatch(self):
  self.set_('pulse_duty_min',.8);self.set_('pulse_duty_max',.9);self.assertIn('TIMER_NOMINAL_REQUIREMENT_MISMATCH',self.codes())
 def test_missing_bound(self):
  self.s['requirements']=[r for r in self.s['requirements'] if r['key']!='pulse_frequency_min_hz'];self.assertIn('TIMER_REQUIREMENT_INVALID',self.codes())
 def test_invalid_numbers(self):
  for v in [True,'40Hz',float('nan'),float('inf'),-1.,0.,100.]:
   with self.subTest(v=v):
    self.set_('pulse_frequency_min_hz',v);self.assertIn('TIMER_REQUIREMENT_INVALID',self.codes())
 def test_percent_not_fraction(self):
  self.set_('pulse_duty_min',60.);self.set_('pulse_duty_max',75.);self.assertIn('TIMER_REQUIREMENT_INVALID',self.codes())
 def test_old_timer_without_declaration_still_unqualified(self):
  self.s['requirements']=[];r=timer_models.analyze(self.s);self.assertFalse(r['findings']);self.assertEqual(r['requirements']['status'],'not_declared');self.assertFalse(r['requirements']['full_function_qualified'])
 def test_two_timers_ambiguous(self):
  u=copy.deepcopy(next(c for c in self.s['components'].values() if c['ref']=='U1'));u['id']='f'*32;u['ref']='U2';self.s['components'][u['id']]=u
  self.assertIn('TIMER_REQUIREMENT_TARGET_UNRESOLVED',self.codes())
 def test_missing_timer(self):
  self.s['components']={k:c for k,c in self.s['components'].items() if c['ref']!='U1'};self.assertIn('TIMER_REQUIREMENT_TARGET_UNRESOLVED',self.codes())
 def test_feedback_topology_fault_kept(self):
  u=next(c for c in self.s['components'].values() if c['ref']=='U1');p=next(p['pin_id'] for p in u['pins'] if p['number']=='6')
  for n in self.s['nets'].values():n['pins']=[e for e in n['pins'] if not(e['component_id']==u['id'] and e['pin_id']==p)]
  self.assertIn('TIMER_ASTABLE_CONNECTION',self.codes())
if __name__=='__main__':unittest.main(verbosity=2)
