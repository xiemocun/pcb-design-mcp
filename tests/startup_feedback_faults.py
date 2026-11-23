"""Source-modelled feedforward cap is distinct from a malformed power cap."""
import copy,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import power_startup
from pcb_mcp.service import Service
class Startup(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base=json.loads((R/'data/fb639744d29945359f393e331cfb34ab/state.json').read_text());cls.svc=Service.__new__(Service)
 def setUp(self):self.s=copy.deepcopy(self.base)
 def c(self,ref):return next(c for c in self.s['components'].values() if c['ref']==ref)
 def move(self,ref,num,name):
  c=self.c(ref);p=dict(component_id=c['id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
  for n in self.s['nets'].values():n['pins']=[q for q in n['pins'] if q!=p]
  next(n for n in self.s['nets'].values() if n['name']==name)['pins'].append(p)
 def ev(self,slew=1.):
  r=power_startup.evaluate(self.s,self.svc.bom_check_state(self.s),assumptions=[dict(net_id=n['id'],max_slew_v_per_ms=slew) for n in self.s['nets'].values() if n['kind']=='power'],origin='proposed_assumption',reason='Unit sensitivity scenario, not an actual ramp measurement')
  self.assertFalse(r['physical_qualified']);self.assertFalse(r['coverage']['full_startup_qualified']);self.assertIsNone(r['actual_peak_input_current_upper_bound_a']);return r
 def bad(self,code):
  r=self.ev();self.assertEqual(r['reference_slew_verdict'],'fail');self.assertIn(code,{f['code'] for f in r['findings']})
 def test_real_feedback_recognised_but_charge_not_claimed(self):
  r=self.ev();self.assertEqual(r['reference_slew_verdict'],'conditional');c=next(x for x in r['capacitors'] if x['reference']=='C14');self.assertEqual(c['scope'],'source_modelled_feedback_capacitor');self.assertFalse(c['coupled_charge_included']);self.assertEqual(r['verdict'],'incomplete');self.assertTrue(all(x['effective_capacitance_upper_bound_uf'] is None for x in r['rails']))
 def test_miswired_feedback_not_exempted(self):self.move('C14','2','MCU_EN');self.bad('STARTUP_CAP_TOPOLOGY_UNSUPPORTED')
 def test_missing_feedback_source_not_exempted(self):del self.s['part_bindings'][self.c('C14')['id']];self.bad('STARTUP_CAP_TOPOLOGY_UNSUPPORTED')
 def test_fake_feedback_source_not_exempted(self):self.s['part_bindings'][self.c('C14')['id']]['facts']['capacitance_f']=1e-15;self.bad('STARTUP_CAP_TOPOLOGY_UNSUPPORTED')
 def test_unrelated_power_signal_cap_still_error(self):self.move('C5','2','MCU_EN');self.bad('STARTUP_CAP_TOPOLOGY_UNSUPPORTED')
 def test_unrealistic_slew_still_fails(self):
  r=self.ev(100.);self.assertEqual(r['reference_slew_verdict'],'fail');self.assertIn('STARTUP_REFERENCE_CURRENT_EXCEEDED',{f['code'] for f in r['findings']})
if __name__=='__main__':unittest.main(verbosity=2)
