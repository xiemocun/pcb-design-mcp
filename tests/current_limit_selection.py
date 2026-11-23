"""Real catalog/state regression; scenario checks are not physical measurements."""
import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts,usb_gate_contract as gate,power_budget,power_startup,placement_rules
from pcb_mcp.service import Service

class Selection(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.base=json.loads((R/'data/fb639744d29945359f393e331cfb34ab/state.json').read_text())
  cls.catalog=parts.catalog();cls.svc=Service.__new__(Service)
 def setUp(self):
  self.s=copy.deepcopy(self.base);self.r=next(c for c in self.s['components'].values() if c['ref']=='R11')
 def replace(self):
  self.r['value']='24.9k';self.s['part_bindings'][self.r['id']]=copy.deepcopy(next(p for p in self.catalog if p['mpn']=='CRCW060324K9FKEA'))
 def startup(self,slew):
  return power_startup.evaluate(self.s,self.svc.bom_check_state(self.s),assumptions=[dict(net_id=n['id'],max_slew_v_per_ms=slew) for n in self.s['nets'].values() if n['kind']=='power'])
 def test_original_failed_scenario_stays_failed(self):
  self.assertEqual(self.startup(3)['reference_slew_verdict'],'fail')
 def test_selected_value_fixes_same_declared_scenario(self):
  self.replace();r=self.startup(3);self.assertEqual(r['reference_slew_verdict'],'conditional')
  self.assertFalse(r['coverage']['full_startup_qualified']);self.assertIsNone(r['actual_peak_input_current_upper_bound_a'])
  self.assertEqual(gate.evaluate(self.s)['verdict'],'pass');limit=gate.current_limit(self.s)
  self.assertAlmostEqual(limit['min_a'],.9527702722662588);self.assertAlmostEqual(limit['max_a'],1.1298623971327388)
  self.assertIn(self.r['id'],placement_rules.reference_plan(self.s))
 def test_higher_slew_still_fails(self):
  self.replace();self.assertEqual(self.startup(5)['reference_slew_verdict'],'fail')
 def test_nondefault_unbound_is_not_assumed_30100(self):
  self.replace();del self.s['part_bindings'][self.r['id']];self.assertIsNone(gate.current_limit(self.s));self.assertEqual(gate.evaluate(self.s)['verdict'],'fail')
 def test_legacy_default_explicit_assumption(self):
  del self.s['part_bindings'][self.r['id']];self.assertFalse(gate.current_limit(self.s)['bound_catalog_tolerance_checked'])
 def test_extra_driver_on_ilim_rejected(self):
  self.replace();node=next(n for n in self.s['nets'].values() if any(p['component_id']==self.r['id'] for p in n['pins']) and n['kind']!='ground')
  extra=next(c for c in self.s['components'].values() if c['ref']=='U1');node['pins'].append(dict(component_id=extra['id'],pin_id=extra['pins'][0]['pin_id']))
  self.assertIsNone(gate.current_limit(self.s));self.assertEqual(gate.evaluate(self.s)['verdict'],'fail')
 def test_part_value_mismatch_rejected(self):
  self.replace();self.r['value']='30.1k';self.assertIsNone(gate.current_limit(self.s))
 def test_missing_switch_evidence_rejected(self):
  self.replace();cat=copy.deepcopy(self.catalog);next(p for p in cat if p['symbol_id']==gate.SW)['evidence_status']='missing_or_changed'
  with patch.object(parts,'catalog',return_value=cat):self.assertIsNone(gate.current_limit(self.s))
 def test_normal_load_fits_but_limit_exceeds_source(self):
  self.replace();self.s['power_envelope']['available_current_ma']=1000
  r=power_budget.evaluate(self.s,self.svc.bom_check_state(self.s))
  self.assertIn('BUDGET_PROGRAMMED_LIMIT_SOURCE_CAPACITY',{f['code'] for f in r['findings']})
  self.assertNotIn('BUDGET_SOURCE_CAPACITY',{f['code'] for f in r['findings']})
 def test_real_bound_10k_outside_programming_range(self):
  self.r['value']='10k';self.s['part_bindings'][self.r['id']]=copy.deepcopy(next(p for p in self.catalog if p['mpn']=='CRCW060310K0FKEA'))
  self.assertIsNone(gate.current_limit(self.s));self.assertEqual(gate.evaluate(self.s)['verdict'],'fail')
 def test_valid_100k_is_not_enough_for_board(self):
  self.r['value']='100k';self.s['part_bindings'][self.r['id']]=copy.deepcopy(next(p for p in self.catalog if p['mpn']=='CRCW0603100KFKEA'))
  self.assertEqual(gate.evaluate(self.s)['verdict'],'pass')
  report=power_budget.evaluate(self.s,self.svc.bom_check_state(self.s))
  self.assertEqual(report['budget_verdict'],'fail');self.assertIn('BUDGET_STAGE_CAPACITY',{f['code'] for f in report['findings']})

if __name__=='__main__':unittest.main(verbosity=2)
