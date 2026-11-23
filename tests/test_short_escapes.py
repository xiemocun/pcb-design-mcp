import copy,unittest
from pcb_mcp import short_escapes as e
from tests.test_dc_power_tree import fixture
class ShortEscapeTests(unittest.TestCase):
 def setup_data(self):
  s,_,_=fixture();s['board_constraints']=dict(track_width_mm=.15);d=dict(component_id='U',pins=['1'],min_width_mm=.25,half_window_mm=1.,max_total_length_mm=2.,reason='Synthetic finite length pad window')
  s['pcb_copper_policy']=dict(rules=[dict(net_id='IN',min_width_mm=.8,design_current_a=2.7,pad_escapes=[d])]);return s
 def test_wrong_net_pin_rejected(self):
  s=self.setup_data();s['pcb_copper_policy']['rules'][0]['pad_escapes'][0]['pins']=['2']
  with self.assertRaisesRegex(ValueError,'NET_PIN'):e.definitions(s)
 def test_duplicate_windows_rejected(self):
  s=self.setup_data();s['pcb_copper_policy']['rules'][0]['pad_escapes']*=2
  with self.assertRaises(ValueError):e.definitions(s)
 def test_long_neck_does_not_pass(self):
  s=self.setup_data();d=e.definitions(s)[0];t=dict(uuid='t',width_mm=.25,length_mm=2.1)
  self.assertFalse(e.reference(d,[t],35,20)['passed'])
 def test_heat_reference_scales_with_square_of_current_and_length(self):
  s=self.setup_data();d=e.definitions(s)[0];t=dict(uuid='t',width_mm=.25,length_mm=1.)
  a=e.reference(d,[t],35,20);dd=dict(d,current_a=5.4);bb=e.reference(dd,[t],35,20)
  self.assertAlmostEqual(bb['rise_above_fixed_endpoints_reference_c'],4*a['rise_above_fixed_endpoints_reference_c']);self.assertFalse(a['physical_qualified']);self.assertFalse(a['assumptions']['endpoint_temperature_verified'])
 def test_window_measures_entire_copper_not_just_endpoint(self):
  s=self.setup_data();d=e.definitions(s)[0];a=dict(components={'U':dict(pads=[dict(number='1',net='IN',at_mm=[10,10])])});t=dict(kind='track',layer='F.Cu',width_mm=.25,bbox_mm=[9.9,9.9,11.01,10.1])
  self.assertFalse(e.allowed(d,t,a,'IN'));t['bbox_mm'][2]=10.9;self.assertTrue(e.allowed(d,t,a,'IN'))
 def test_one_dimensional_steady_state_reference_numeric(self):
  s=self.setup_data();d=e.definitions(s)[0];r=e.reference(d,[dict(uuid='t',width_mm=.25,length_mm=1.)],35,20)
  self.assertAlmostEqual(r['resistance_reference_ohm'],e.RHO_100C*.001/(.00025*.000035));self.assertLess(r['rise_above_fixed_endpoints_reference_c'],1.)
if __name__=='__main__':unittest.main()
