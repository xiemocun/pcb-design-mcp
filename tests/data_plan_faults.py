"""Data-plan structural faults and scoped power/interface regressions."""
import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'));W=R.parents[1]/'work/dual-logic-design'
from pcb_mcp_client.plan import Plan,resolve,check_pin_coverage
from pcb_mcp import power_integrity,module_contracts,parts
class PlanFaults(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.base=json.loads((W/'circuit-plan.json').read_text());cls.catalog=json.loads((W/'attempt-01/resolved-catalog.json').read_text());cls.components={ref:dict(component_id=('%032x'%i),pins=entry['symbol']['pins']) for i,(ref,entry) in enumerate(cls.catalog.items(),1)}
 def setUp(self):self.plan=copy.deepcopy(self.base)
 def test_real_pin_coverage(self):check_pin_coverage(Plan(**self.plan),self.components)
 def test_unknown_pin_rejected(self):
  self.plan['nets'][0]['pins'][0]['number']='99'
  with self.assertRaisesRegex(ValueError,'UNKNOWN_REAL_PIN'):check_pin_coverage(Plan(**self.plan),self.components)
 def test_missing_pin_rejected(self):
  self.plan['no_connects'].pop()
  with self.assertRaisesRegex(ValueError,'UNACCOUNTED_REAL_PIN'):check_pin_coverage(Plan(**self.plan),self.components)
 def test_connected_and_nc_rejected(self):
  self.plan['no_connects'][0]['pin']={'ref':'U1','number':'5'}
  with self.assertRaisesRegex(ValueError,'PIN_ASSIGNED_TWICE'):Plan(**self.plan)
 def test_duplicate_ref_rejected(self):
  self.plan['components'][1]['ref']='J1'
  with self.assertRaisesRegex(ValueError,'DUPLICATE_COMPONENT_REFERENCE'):Plan(**self.plan)
 def test_unknown_component_rejected(self):
  self.plan['nets'][0]['pins'][0]['ref']='J99'
  with self.assertRaisesRegex(ValueError,'UNKNOWN_COMPONENT_REFERENCE'):Plan(**self.plan)
 def test_no_production_or_exec_setting(self):
  for tool in ['fabrication_export','manufacturing_review_prepare','exec','project_read']:
   with self.subTest(tool=tool):
    self.plan['settings'][0]['tool']=tool
    with self.assertRaises(ValueError):Plan(**self.plan)
 def test_reserved_mutation_field_rejected(self):
  self.plan['settings'][0]['args']['project_id']='0'*32
  with self.assertRaisesRegex(ValueError,'RESERVED_PROJECT_FIELDS'):Plan(**self.plan)
 def test_reference_resolution_is_data_not_eval(self):
  self.assertEqual(resolve('$(touch /tmp/not-executed)',{},{}),'$(touch /tmp/not-executed)')
  self.assertEqual(resolve({'$component':'U1'},self.components,{}),self.components['U1']['component_id'])
  self.assertEqual(resolve({'$net':'SUPPLY'},self.components,{'SUPPLY':'a'*32}),'a'*32)
 def test_invalid_reference_operators(self):
  for value in [{'$eval':'pass'},{'$pin':['U1','5'],'literal':1},{'$pin':['U1']}]:
   with self.subTest(value=value):
    with self.assertRaises(ValueError):resolve(value,self.components,{})
 def test_wrong_reference_pin_fails(self):
  with self.assertRaisesRegex(ValueError,'PIN_NOT_FOUND'):resolve({'$pin':['U1','9']},self.components,{})
class PowerScope(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  run=json.loads((W/'attempt-02/run.json').read_text());cls.base=json.loads((R/'data'/run['project_id']/'state.json').read_text())
 def setUp(self):self.s=copy.deepcopy(self.base)
 def test_bound_standalone_logic_supported(self):self.assertTrue(power_integrity.supports_envelope(self.s))
 def test_no_binding_not_supported(self):self.s['part_bindings']={};self.assertFalse(power_integrity.supports_envelope(self.s))
 def test_forged_model_not_supported(self):
  for c in self.s['components'].values():
   if c['ref'].startswith('U'):self.s['part_bindings'][c['id']]['facts']['supply_max_v']=1000
  self.assertFalse(power_integrity.supports_envelope(self.s))
 def test_missing_source_not_supported(self):
  records=parts.catalog()
  for p in records:p['evidence_status']='missing_or_changed'
  with patch.object(parts,'catalog',return_value=records):self.assertFalse(power_integrity.supports_envelope(self.s))
 def test_legacy_profile_kept(self):
  old=json.loads((R/'data/fb639744d29945359f393e331cfb34ab/state.json').read_text());self.assertTrue(power_integrity.supports_envelope(old))
 def test_standalone_does_not_require_sensor(self):
  self.assertFalse(module_contracts.split_static_findings(self.s))
 def test_standalone_bypass_missing_still_error(self):
  for n in self.s['nets'].values():n['pins']=[p for p in n['pins'] if not self.s['components'][p['component_id']]['ref'].startswith('C')]
  self.assertIn('MODULE_BYPASS_MISSING',{f['code'] for f in module_contracts.split_static_findings(self.s)})
 def test_standalone_unbound_still_error(self):
  self.s['part_bindings']={};self.assertIn('MODULE_PART_REQUIRED',{f['code'] for f in module_contracts.split_static_findings(self.s)})
if __name__=='__main__':unittest.main(verbosity=2)
