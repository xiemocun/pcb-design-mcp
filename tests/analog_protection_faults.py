import copy,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/analog-input-protection';sys.path.insert(0,str(R/'src'))
from pcb_mcp import analog_protection,analog_models,usb_power,parts,backend
from pcb_mcp.generic_placement import bounded_positions

class Protection(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.base=json.loads((Path((W/'latest-run.txt').read_text())/'state.json').read_text())
 def setUp(self):self.s=copy.deepcopy(self.base)
 def c(self,ref):return next(c for c in self.s['components'].values() if c['ref']==ref)
 def move(self,ref,num,name):
  c=self.c(ref);p=dict(component_id=c['id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
  for n in self.s['nets'].values():n['pins']=[q for q in n['pins'] if q!=p]
  if name:next(n for n in self.s['nets'].values() if n['name']==name)['pins'].append(p)
 def report(self):return analog_protection.analyze(self.s,self.c('U1'))
 def bad(self,code):
  q=self.report();self.assertIn(code,{f['code'] for f in q['findings']});self.assertFalse(q['power_off_topology_supported']);self.assertTrue(analog_models.analyze(self.s)['findings'])
 def test_protected_topology_not_full_function(self):
  q=self.report();self.assertTrue(q['power_off_topology_supported']);self.assertFalse(q['findings'])
  self.assertEqual(q['path'],['J2','U2.3','U2.2','R4','U1.1'])
  self.assertLess(q['nonnegative_dc_series_current_reference_a'],.00055)
  self.assertIsNone(q['guaranteed_total_tracking_error_v']);self.assertFalse(q['brownout_and_hotplug_qualified']);self.assertFalse(q['whole_board_usb_backfeed_qualified'])
  self.assertEqual(analog_models.analyze(self.s)['amplifiers'][0]['input_range_v'],[.5,4.])
 def test_enable_low(self):self.move('U2','1','GND');self.bad('ANALOG_PROTECTION_ENABLE')
 def test_enable_floating(self):self.move('U2','1',None);self.bad('ANALOG_PROTECTION_ENABLE')
 def test_ground_missing(self):self.move('U2','5',None);self.bad('ANALOG_PROTECTION_SUPPLY')
 def test_negative_supply_not_ground(self):self.move('U2','4','CC1');self.bad('ANALOG_PROTECTION_SUPPLY')
 def test_supply_wrong_rail(self):self.move('U2','13','CC1');self.bad('ANALOG_PROTECTION_SUPPLY')
 def test_nc_connected(self):self.move('U2','12','GND');self.bad('ANALOG_PROTECTION_NC')
 def test_unused_channel_not_ignored(self):self.move('U2','14','ANALOG_IN');self.bad('ANALOG_PROTECTION_UNUSED_CHANNEL')
 def test_extra_shunt_load_rejected(self):self.move('C4','1','ANALOG_PROTECTED');self.bad('ANALOG_PROTECTION_BYPASS')
 def test_missing_switch_identity(self):del self.s['part_bindings'][self.c('U2')['id']];self.bad('ANALOG_PROTECTION_PATH_REQUIRED')
 def test_resistor_alone_cannot_masquerade_as_isolation(self):
  self.move('R4','1','ANALOG_IN');self.bad('ANALOG_PROTECTION_PATH_REQUIRED')
  q=usb_power.evaluate(self.s);self.assertIn('USB_INPUT_PROTECTION_INVALID',{f['code'] for f in q['findings'] if f['severity']=='error'})
 def test_restoring_direct_input_restores_original_error(self):
  self.move('U1','1','ANALOG_IN');q=usb_power.evaluate(self.s)
  self.assertIn('USB_EXTERNAL_INPUT_BACKFEED_PATH',{f['code'] for f in q['findings'] if f['severity']=='error'})
 def test_real_bound_low_resistor_fails_current_limit(self):
  old=json.loads((R/'data/a3074bbb123e45faa365985ab499686c/state.json').read_text());source=next(c for c in old['components'].values() if c['ref']=='R3');target=self.c('R4');target['value']='470'
  for field in ['part_bindings','footprint_bindings']:self.s[field][target['id']]=copy.deepcopy(old[field][source['id']])
  self.bad('ANALOG_PROTECTION_LIMITER')
 def test_high_input_does_not_get_5v_reference(self):
  next(r for r in self.s['requirements'] if r['key']=='analog_input_max_v')['value']=5.
  self.bad('ANALOG_PROTECTION_REFERENCE_SCOPE')
 def test_temperature_not_silently_extrapolated(self):self.s['power_envelope']['ambient_c']=85.;self.bad('ANALOG_PROTECTION_REFERENCE_SCOPE')
 def test_power_off_reference_not_entire_range_upper_bound(self):
  q=self.report()['static_off_mode'];self.assertEqual(q['leakage_reference_max_a'],150e-9);self.assertIn('VS1V/16V',q['leakage_test_condition']);self.assertIsNone(q['actual_leakage_upper_bound_over_entire_input_range_a'])
 def test_source_table_single_supply_not_5ohm_headline(self):
  p=self.s['part_bindings'][self.c('U2')['id']];self.assertEqual(p['facts']['on_resistance_reference_max_ohm_at4p5v'],14.7)
  actual=next(c for c in backend.catalog(['PCB_MCP']) if c['symbol']=='ADG4612BRUZ')
  self.assertEqual({p['number']:p['name'] for p in actual['pins']},p['facts']['pin_mapping'])
 def test_usb_remaining_errors_not_suppressed(self):
  q=usb_power.evaluate(self.s);errors={f['code'] for f in q['findings'] if f['severity']=='error'}
  self.assertEqual(errors,{'USB_PSD_ATTACH_LOAD_UNPROVEN','USB_PSD_CURRENT_BOUND_UNPROVEN','USB_PSD_EFFECTIVE_CAPACITANCE_UNPROVEN'})
  self.assertEqual(len(q['ports'][0]['default_power']['protected_analog_inputs']),1)
 def test_native_size_packing_keeps_original_outline(self):
  sizes={'J1':(10.69,8.99),'U2':(6.95,5.7),**{f'C{i}':(3.01,1.51) for i in range(12)}}
  positions=bounded_positions(sizes,70.,50.,5.);rects=[]
  for ref,(x,y) in positions.items():
   # Remove binary arithmetic noise (~1e-15mm), far below KiCad1nm coordinates.
   w,h=sizes[ref];rect=tuple(round(v,9) for v in (x-w/2-2,y-h/2-2,x+w/2+2,y+h/2+2))
   self.assertTrue(rect[0]>=5 and rect[1]>=5 and rect[2]<=65 and rect[3]<=45)
   self.assertFalse(any(rect[0]<z[2] and rect[2]>z[0] and rect[1]<z[3] and rect[3]>z[1] for z in rects));rects.append(rect)
 def test_native_size_packing_rejects_impossible_outline(self):
  with self.assertRaisesRegex(ValueError,'PLACEMENT_CAPACITY_EXCEEDED'):bounded_positions({'U1':(100.,100.)},70.,50.,5.)
if __name__=='__main__':unittest.main(verbosity=2)
