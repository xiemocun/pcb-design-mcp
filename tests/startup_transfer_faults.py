"""Independent power-balance examples and actual-mainboard missed-load regression."""
import copy,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import startup_transfer,power_startup
from pcb_mcp.service import Service
from pcb_mcp.models import StartupReport

def row(name,dc,cap,capacity,lo=5.,hi=5.):
 return dict(net_id=name,name=name,local_capacitor_current_reference_a=cap,declared_dc_baseline_a=dc,capacity_reference={'current_a':capacity},capacitor_inventory_complete=True,voltage_reference_v={'min_v':lo,'max_v':hi})
def edge(name,kind,vi,vo,eta=None):
 return dict(reference=name,kind=kind,input_net=vi,output_net=vo,efficiency_assumption=eta)

class Transfer(unittest.TestCase):
 def setUp(self):
  self.rows=[row('A',1.,.01,2.),row('B',.8,.02,.9),row('C',.7,.08,1.,3.,3.),row('D',.1,.03,1.)]
  self.edges=[edge('SW','load_switch','A','B'),edge('BUCK','buck','B','C',.8),edge('LDO','ldo','A','D')]
 def ev(self):return startup_transfer.evaluate(self.rows,self.edges,'A',True)
 def test_independent_power_balance_and_no_double_count(self):
  r=self.ev();self.assertEqual(r['verdict'],'conditional');self.assertAlmostEqual(r['input_charge_reference_a'],.01+.02+(.08*3/(5*.8))+.03)
  self.assertAlmostEqual(r['dc_plus_charge_input_reference_a'],1.12)
  a=next(x for x in r['rails'] if x['name']=='A');self.assertEqual(len(a['contributions']),4)
 def test_local_pass_but_upstream_overload(self):
  self.rows[1]['capacity_reference']['current_a']=.85
  self.assertLess(.8+.02,.85)
  r=self.ev();self.assertEqual(r['verdict'],'fail');self.assertIn('STARTUP_TRANSFER_CURRENT_EXCEEDED',{f['code'] for f in r['findings']})
 def test_order_independent(self):
  a=self.ev();self.rows.reverse();self.edges.reverse();b=self.ev();self.assertEqual(a['input_charge_reference_a'],b['input_charge_reference_a'])
 def test_missing_slew_incomplete_not_zero(self):
  self.rows[2]['local_capacitor_current_reference_a']=None;r=self.ev();self.assertEqual(r['verdict'],'incomplete');self.assertIsNone(r['input_charge_reference_a'])
 def test_bad_dc_budget_stops_transfer(self):
  self.assertEqual(startup_transfer.evaluate(self.rows,self.edges,'A',False)['verdict'],'incomplete')
 def test_unknown_inventory_incomplete(self):
  self.rows[2]['capacitor_inventory_complete']=False;self.assertEqual(self.ev()['verdict'],'incomplete')
 def test_cycle_rejected(self):
  self.edges=[edge('BC','load_switch','B','C'),edge('CB','load_switch','C','B'),edge('AD','ldo','A','D')]
  self.assertEqual(self.ev()['verdict'],'fail')
 def test_multi_driver_rejected(self):
  self.edges.append(edge('X','ldo','A','C'));self.assertEqual(self.ev()['verdict'],'fail')
 def test_orphan_rejected(self):
  self.edges.pop();self.assertEqual(self.ev()['verdict'],'fail')
 def test_unsupported_stage_rejected(self):
  self.edges[0]['kind']='unknown';self.assertEqual(self.ev()['verdict'],'fail')
 def test_bad_efficiency_or_voltage_rejected(self):
  for v in [None,0.,-1.,float('nan'),float('inf'),1.1]:
   with self.subTest(eta=v):
    self.edges[1]['efficiency_assumption']=v;self.assertEqual(self.ev()['verdict'],'fail')
  self.edges[1]['efficiency_assumption']=.8;self.rows[1]['voltage_reference_v']['min_v']=0.;self.assertEqual(self.ev()['verdict'],'fail')
 def test_duplicate_names_rejected(self):
  self.rows[2]['name']='B';self.assertEqual(self.ev()['verdict'],'fail')
 def test_zero_cap_valid_and_no_stage_single_source(self):
  r=startup_transfer.evaluate([row('A',.2,0.,1.)],[],'A',True);self.assertEqual(r['verdict'],'conditional');self.assertEqual(r['dc_plus_charge_input_reference_a'],.2)

class Mainboard(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.state=json.loads((R/'data/fb639744d29945359f393e331cfb34ab/state.json').read_text());cls.svc=Service.__new__(Service)
 def ev(self,slew):
  r=power_startup.evaluate(self.state,self.svc.bom_check_state(self.state),assumptions=[dict(net_id=n['id'],max_slew_v_per_ms=slew) for n in self.state['nets'].values() if n['kind']=='power'],origin='proposed_assumption',reason='Explicit mathematical sensitivity scenario, not measured')
  StartupReport(**r);return r
 def test_actual_mainboard_one_volt_per_ms(self):
  r=self.ev(1.);self.assertEqual(r['charge_transfer']['verdict'],'conditional');self.assertEqual(r['reference_slew_verdict'],'conditional');self.assertIsNone(r['actual_peak_input_current_upper_bound_a']);self.assertFalse(r['coverage']['full_startup_qualified'])
 def test_actual_mainboard_three_volt_per_ms_local_checks_miss_overload(self):
  r=self.ev(3.);self.assertTrue(all(x['current_margin_reference_a']>0 for x in r['rails']));self.assertEqual(r['reference_slew_verdict'],'fail')
  usb=next(x for x in r['charge_transfer']['rails'] if x['name']=='VUSB');self.assertLess(usb['current_margin_reference_a'],0)
  self.assertGreater(usb['downstream_charge_reference_a'],0);self.assertFalse(r['physical_qualified'])
if __name__=='__main__':unittest.main(verbosity=2)
