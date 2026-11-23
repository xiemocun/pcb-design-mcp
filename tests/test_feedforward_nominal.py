"""Actual source-bound capacitance topology accepted only for DC divider setting."""
import json,copy,unittest
from pathlib import Path
from unittest.mock import patch
from pcb_mcp import feedback_voltage
class Feedforward(unittest.TestCase):
 def setUp(self):
  self.s=json.loads(Path('work/riding-intercom/data/e4569a2267ec4b0c8dbfe22e4bb60820/state.json').read_text());self.catalog=copy.deepcopy(list(self.s['part_bindings'].values()));c=next(c for c in self.s['components'].values() if c['ref']=='U5');self.model=dict(engine='feedback_voltage',part_definition_hash=self.s['part_bindings'][c['id']]['definition_hash'],conditions='nominal equation test',feedback_voltage=dict(input_pin='3',output_pin='6',feedback_pin='1',ground_pin='4',reference_nominal_v=.595,output_min_v=2.2,output_max_v=5.5))
 def run_model(self):
  with patch('pcb_mcp.parts.catalog',return_value=self.catalog),patch('pcb_mcp.verification_models.packages',return_value={'m':(self.model,None)}):return feedback_voltage.evaluate(self.s)
 def test_real_feedforward_dc_open(self):
  r=self.run_model();self.assertFalse(r['findings']);self.assertEqual({c['reference'] for c in r['rows'][0]['feedforward_capacitors']},{'C51','C52'});self.assertAlmostEqual(r['rows'][0]['nominal_v'],4.9504)
 def test_stale_capacitor_rejected(self):
  c=next(c for c in self.s['components'].values() if c['ref']=='C51');self.s['part_bindings'][c['id']]['facts']['capacitance_f']=999.
  self.assertIn('FEEDBACK_LOAD_UNMODELED',{f['code'] for f in self.run_model()['findings']})
 def test_wrong_endpoint_rejected(self):
  c=next(c for c in self.s['components'].values() if c['ref']=='C51');v=next(n for n in self.s['nets'].values() if n['name']=='V5');g=next(n for n in self.s['nets'].values() if n['name']=='GND');p=next(p for p in v['pins'] if p['component_id']==c['id']);v['pins'].remove(p);g['pins'].append(p)
  self.assertIn('FEEDBACK_LOAD_UNMODELED',{f['code'] for f in self.run_model()['findings']})
if __name__=='__main__':unittest.main()
