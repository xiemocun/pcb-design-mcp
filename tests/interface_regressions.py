"""Faults in actual split-interface specifications; no claim of hardware testing."""
import copy,json,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import module_contracts as m
class Interface(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  w=R.parents[1]/'work/interface-redesign';run=Path((w/'latest-run.txt').read_text());cls.s=json.loads((run/'state.json').read_text())
 def test_actual_signal_margins(self):
  r=m.evaluate(self.s);self.assertEqual(r['verdict'],'pass');row=r['modules'][0]
  self.assertEqual((row['read_gpio'],row['drive_gpio']),('IO27','IO26'))
  self.assertTrue(all(x>0 for x in row['voltage_reference']['margins_v'].values()));self.assertFalse(row['physical_qualified'])
 def test_gpio_selection_is_not_hardcoded(self):
  s=copy.deepcopy(self.s);mc=next(c for c in s['components'].values() if c['library_id']==m.ESP)
  for old,new in [('IO26','IO32'),('IO27','IO33')]:
   a=next(p for p in mc['pins'] if p['name']==old);b=next(p for p in mc['pins'] if p['name']==new)
   for n in s['nets'].values():
    for p in n['pins']:
     if p==dict(component_id=mc['id'],pin_id=a['pin_id']):p['pin_id']=b['pin_id']
  r=m.evaluate(s);self.assertEqual(r['verdict'],'pass',r);self.assertEqual((r['modules'][0]['read_gpio'],r['modules'][0]['drive_gpio']),('IO33','IO32'))
 def test_missing_release_pullup_fails(self):
  s=copy.deepcopy(self.s);c=next(c for c in s['components'].values() if c['ref']=='R6')
  for n in s['nets'].values():n['pins']=[p for p in n['pins'] if p['component_id']!=c['id']]
  r=m.evaluate(s);self.assertEqual(r['verdict'],'fail');self.assertIn('MODULE_RELEASE_PULLUP_REQUIRED',{f['code'] for f in r['findings']})
 def test_overvoltage_fails(self):
  s=copy.deepcopy(self.s);s['power_envelope']['max_v']=6.0;r=m.evaluate(s)
  self.assertEqual(r['verdict'],'fail');self.assertIn('MODULE_BUFFER_RANGE',{f['code'] for f in r['findings']})
 def test_missing_bus_pullup_fails(self):
  s=copy.deepcopy(self.s);c=next(c for c in s['components'].values() if c['ref']=='R3')
  for n in s['nets'].values():n['pins']=[p for p in n['pins'] if p['component_id']!=c['id']]
  r=m.evaluate(s);self.assertEqual(r['verdict'],'fail');self.assertIn('MODULE_BUS_PULLUP_REQUIRED',{f['code'] for f in r['findings']})
 def test_unbound_logic_fails(self):
  s=copy.deepcopy(self.s);c=next(c for c in s['components'].values() if c['ref']=='U9');s['part_bindings'].pop(c['id']);r=m.evaluate(s)
  self.assertEqual(r['verdict'],'fail');self.assertIn('MODULE_PART_REQUIRED',{f['code'] for f in r['findings']})
 def test_bypassed_receiver_fails(self):
  s=copy.deepcopy(self.s);mc=next(c for c in s['components'].values() if c['library_id']==m.ESP);pin=next(p for p in mc['pins'] if p['name']=='IO27');p=dict(component_id=mc['id'],pin_id=pin['pin_id'])
  for n in s['nets'].values():n['pins']=[x for x in n['pins'] if x!=p]
  next(n for n in s['nets'].values() if n['name']=='SENSOR_DATA')['pins'].append(p)
  self.assertEqual(m.evaluate(s)['verdict'],'fail')
 def test_legacy_gap_remains_an_error(self):
  s=json.loads((R/'data/8dbd5d951e35438e9d26c4561b4a068c/state.json').read_text());r=m.evaluate(s)
  self.assertEqual(r['verdict'],'fail');self.assertIn('MODULE_LOGIC_MARGIN_UNPROVEN',{f['code'] for f in r['findings']})
if __name__=='__main__':unittest.main(verbosity=2)
