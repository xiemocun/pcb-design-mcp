"""Exercise source identity and actual divider connectivity, not physical regulation."""
import copy,json,unittest
from pathlib import Path
from unittest.mock import patch
from pcb_mcp.feedback_voltage import evaluate

class FeedbackFaults(unittest.TestCase):
    def setUp(self):
        self.s=json.loads(Path('work/riding-intercom/hardware-circuit/state.json').read_text())
        u=next(c for c in self.s['components'].values() if c['ref']=='U5')
        part=self.s['part_bindings'][u['id']]
        self.model=dict(engine='feedback_voltage',part_definition_hash=part['definition_hash'],conditions='nominal only',feedback_voltage=dict(input_pin='3',output_pin='6',feedback_pin='1',ground_pin='4',reference_nominal_v=.595,output_min_v=2.2,output_max_v=5.5))
        self.catalog=copy.deepcopy(list(self.s['part_bindings'].values()))
    def run_model(self,models=None):
        with patch('pcb_mcp.parts.catalog',return_value=self.catalog),patch('pcb_mcp.verification_models.packages',return_value=models or {'m':(self.model,None)}):return evaluate(self.s)
    def test_nominal_setting(self):
        result=self.run_model();self.assertFalse(result['findings']);self.assertAlmostEqual(result['rows'][0]['nominal_v'],4.9504)
    def test_feedback_short(self):
        c=next(c for c in self.s['components'].values() if c['ref']=='U5');p=next(p for p in c['pins'] if p['number']=='1')
        for n in self.s['nets'].values():n['pins']=[x for x in n['pins'] if x!={'component_id':c['id'],'pin_id':p['pin_id']}]
        result=self.run_model();self.assertEqual(result['findings'][0]['code'],'FEEDBACK_MODEL_CONNECTION')
    def test_unknown_feedback_load(self):
        c=next(c for c in self.s['components'].values() if c['ref']=='U1');n=next(n for n in self.s['nets'].values() if n['name']=='BOOST_FB');n['pins'].append(dict(component_id=c['id'],pin_id=c['pins'][0]['pin_id']))
        self.assertEqual(self.run_model()['findings'][0]['code'],'FEEDBACK_LOAD_UNMODELED')
    def test_stale_resistor_binding(self):
        c=next(c for c in self.s['components'].values() if c['ref']=='R4');self.s['part_bindings'][c['id']]['facts']['value_ohm']=999.
        self.assertEqual(self.run_model()['findings'][0]['code'],'FEEDBACK_DIVIDER_SOURCE')
    def test_ambiguous_model(self):
        self.assertEqual(self.run_model({'a':(self.model,None),'b':(self.model,None)})['findings'][0]['code'],'FEEDBACK_MODEL_AMBIGUOUS')
    def test_outside_source_range(self):
        self.model['feedback_voltage']['reference_nominal_v']=1.
        self.assertEqual(self.run_model()['findings'][0]['code'],'FEEDBACK_SETTING_RANGE')

if __name__=='__main__':unittest.main()
