"""An explicit capacitor model must affect the generated circuit and its actual current."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
from pcb_mcp import spice
from pcb_mcp.functional import Scenario, compile_spice


class BoundCapacitor(unittest.TestCase):
    def test_capacitor_model_precedes_nominal_value_and_keeps_initial_condition(self):
        def component(ref, symbol):
            return dict(id=ref, ref=ref, library_id=symbol, pins=[dict(number=n,pin_id=n) for n in ['1','2']])
        state = dict(id='capacitor_fixture',components={'J1':component('J1','Connector_Generic:Conn_01x02'),
                     'C1':component('C1','Device:C')}, nets={})
        for number, name in [('1','vin'),('2','gnd')]:
            state['nets'][name]=dict(id=name,pins=[dict(component_id=ref,pin_id=number) for ref in state['components']])
        records = {'C1':dict(kind='capacitor',facts={'capacitance_f':10e-6},definition_hash='c'),
                   'J1':dict(kind='connector',facts={},definition_hash='j')}
        pos, neg = dict(ref='J1',number='1'),dict(ref='J1',number='2')
        scenario = Scenario(id='ramp',engine='spice_transient',drives=[dict(positive=pos,negative=neg,dc_v=0.,
                            pwl=[[0.,0.],[.001,1.],[.002,1.]])],stop_s=.002,step_s=1e-5,
                            capacitor_initial_voltages=[dict(terminal=dict(ref='C1',number='1'),voltage_v=0.)])
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder/'test.lib').write_text('.subckt testcap a b\nCm a b 2u\n.ends testcap\n')
            model = dict(engine='spice_subcircuit',name='testcap',model_asset='test.lib',pins=['1','2'],
                         excluded_pins={},temperature_range_c=[25.,25.],conditions='Two microfarads for controlled current test.')
            for binding,expected in [({},.010),({'C1':(model,folder)},.002)]:
                with self.subTest(bound=bool(binding)):
                    deck, _, evidence, probes, _ = compile_spice(state,scenario,binding,records)
                    c1, = (row for row in evidence if row['reference']=='C1')
                    self.assertEqual(c1['initial_state']['expected_node_v'],0.)
                    self.assertEqual('model_id' in c1,bool(binding))
                    self.assertEqual('Xentry' in deck,bool(binding))
                    if Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file():
                        current = probes['C1'][0]['vector']
                        _, v = spice.run(deck,['time',current],folder/('bound' if binding else 'nominal'))
                        mask=(v['time']>.0001)&(v['time']<.0009)
                        np.testing.assert_allclose(v[current][mask],expected,rtol=1e-6)
            outside = scenario.model_copy(update={'temperature_c':0.})
            with self.assertRaisesRegex(ValueError,'TEMPERATURE_OUTSIDE_SCOPE'):
                compile_spice(state,outside,{'C1':(model,folder)},records)
