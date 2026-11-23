"""Fault injection and independent DC corner equation on the actual revised spec."""
import copy,itertools,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/regulator-redesign';sys.path.insert(0,str(R/'src'))
from pcb_mcp import regulator_models,power_integrity,parts,backend,footprints,usb_gate_contract,power_budget,regulator_layout
class Regulator(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.s=json.loads((Path((W/'latest-run.txt').read_text())/'state.json').read_text())
 def setUp(self):self.s=copy.deepcopy(self.__class__.s)
 def c(self,ref):return next(c for c in self.s['components'].values() if c['ref']==ref)
 def move(self,ref,pin,nname):
  c=self.c(ref);p=next(p for p in c['pins'] if p['name']==pin or p['number']==pin);obj=dict(component_id=c['id'],pin_id=p['pin_id'])
  for n in self.s['nets'].values():n['pins']=[q for q in n['pins'] if q!=obj]
  next(n for n in self.s['nets'].values() if n['name']==nname)['pins'].append(obj)
 def codes(self):return {f['code'] for f in regulator_models.analyze(self.s)['findings'] if f['severity']=='error'}
 def test_interval_contains_independent_kcl_corners(self):
  a=regulator_models.analyze(self.s);self.assertFalse(self.codes());row=a['regulators'][0];f=self.s['part_bindings'][self.c('U3')['id']]['facts'];top=self.s['part_bindings'][self.c('R12')['id']]['facts'];bot=self.s['part_bindings'][self.c('R13')['id']]['facts']
  def bounds(q):
   drift=q['temperature_coefficient_ppm_per_k']*1e-6*(q['temperature_range_c'][1]-q['temperature_range_c'][0]);return [q['value_ohm']*(1-q['tolerance_fraction'])*(1-drift),q['value_ohm']*(1+q['tolerance_fraction'])*(1+drift)]
  for vfb,rt,rb,leak in itertools.product([f['feedback_min_v'],f['feedback_max_v']],bounds(top),bounds(bot),[-f['feedback_leakage_max_a'],f['feedback_leakage_max_a']]):
   out=(vfb*(1/rt+1/rb)+leak)*rt;self.assertGreaterEqual(out+1e-12,row['output_interval_v']['min_v']);self.assertLessEqual(out-1e-12,row['output_interval_v']['max_v'])
  self.assertGreater(row['output_interval_v']['min_v'],3.2);self.assertLess(row['output_interval_v']['max_v'],3.6)
 def test_en_at_ground_rejected(self):self.move('U3','EN','GND');self.assertIn('PWM_REGULATOR_CONNECTION',self.codes())
 def test_fb_shorted_to_output_rejected(self):self.move('U3','FB','V3V3');self.assertIn('PWM_REGULATOR_CONNECTION',self.codes())
 def test_missing_bottom_resistor_rejected(self):self.move('R13','2','VUSB');self.assertIn('PWM_REGULATOR_FEEDBACK',self.codes())
 def test_missing_feedforward_rejected(self):self.move('C14','1','GND');self.assertIn('PWM_REGULATOR_FEEDBACK',self.codes())
 def test_missing_input_ceramic_rejected(self):self.move('C1','1','GND');self.assertIn('PWM_REGULATOR_CAPACITANCE',self.codes())
 def test_unknown_feedback_load_rejected(self):self.move('C9','1','REG_FB');self.assertIn('PWM_REGULATOR_FB_LOAD',self.codes())
 def test_display_value_change_cannot_fake_feedback(self):self.c('R12')['value']='100k';self.assertIn('PWM_REGULATOR_PASSIVE_SOURCE',self.codes())
 def test_source_missing_no_valid_rail_created(self):
  del self.s['part_bindings'][self.c('U3')['id']];self.assertIn('PWM_REGULATOR_PART_REQUIRED',self.codes());r=power_integrity.evaluate(self.s);nid=next(n['id'] for n in self.s['nets'].values() if n['name']=='V3V3');self.assertNotIn(nid,r['rails'])
 def test_usb_interlock_still_rejects_bypass(self):self.move('U3','IN','VBUS_RAW');self.move('U3','EN','VBUS_RAW');self.assertTrue(any(f['code']=='USB_GATE_POWER_PATH' for f in usb_gate_contract.static_findings(self.s)))
 def test_effective_capacitance_remains_unknown(self):
  r=regulator_models.analyze(self.s);self.assertTrue(any(f['code']=='PWM_CAPACITANCE_EFFECTIVE_UNVERIFIED' for f in r['findings']));self.assertFalse(r['regulators'][0]['physical_qualified']);self.assertIsNone(r['regulators'][0]['capacitors']['OUT']['effective_lower_f'])
 def test_buck_cannot_regulate_above_actual_input(self):
  self.s['power_envelope']['min_v']=3.4;r=power_integrity.evaluate(self.s);self.assertTrue(any(f['code']=='BUCK_DROPOUT_MARGIN' and f['severity']=='error' for f in r['findings']))
 def test_power_budget_does_not_hide_known_regulator_errors(self):
  self.move('C1','1','GND');r=power_budget.evaluate(self.s,dict(findings=[]));self.assertEqual(r['budget_verdict'],'fail');self.assertTrue(any(f['code']=='PWM_REGULATOR_CAPACITANCE' for f in r['findings']))
 def test_actual_local_front_paths_and_broken_contact(self):
  report=json.loads((W/'report.json').read_text());folder=R/'data'/report['project_id']/'pcb_builds'/report['pcb_build_id'];a=json.loads((folder/'copper_geometry.json').read_text());r=regulator_layout.evaluate(self.s,a,'routed');self.assertFalse(r['findings']);self.assertEqual(len(r['measurements']),2)
  for row in r['measurements']:
   self.assertEqual(row[row['role']+'_front_track_reference']['ideal_via_count'],0);self.assertEqual(row['GND_front_track_reference']['ideal_via_count'],0)
  for n in a['direct_contacts']['nodes'].values():
   if n.get('reference')=='U3':n['contacts']=[]
  self.assertTrue(regulator_layout.evaluate(self.s,a,'routed')['findings'])
 def test_native_pin_groups_and_manufacturer_land_dimensions(self):
  c=self.c('U3');self.assertEqual({n for p in c['pins'] for n in p['physical_numbers']},{str(i) for i in range(1,11)})
  fp=self.s['footprint_bindings'][c['id']];actual=footprints.load(fp['footprint_id'],Path(fp['source']));self.assertEqual(len(actual['pads']),10)
  for pad in actual['pads']:
   self.assertEqual(list(map(float,pad['size'])),[.75,.35]);self.assertIn(float(pad['at'][0]),[-.625,.625])
if __name__=='__main__':unittest.main(verbosity=2)
