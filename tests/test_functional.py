"""Frozen contract faults plus independent native SPICE numerical checks."""
import copy
from pathlib import Path
import tempfile
import unittest
import numpy as np
from pcb_mcp.functional import Drive, FunctionalContract, Scenario, matching_dc_bias
from pcb_mcp import spice


def contract():
    return dict(description='An explicit static divider output requirement',requirements=[dict(id='out',description='Output must remain between specified voltage limits.',positive=dict(ref='J2',number='1'),negative=dict(ref='J2',number='2'),quantity='voltage',minimum=2.3,maximum=2.7,scenarios=['dc'])],scenarios=[dict(id='dc',engine='linear_dc')])


class ContractTests(unittest.TestCase):
    def test_coverage_cannot_omit_a_requirement(self):
        raw=contract();raw['requirements'][0]['scenarios']=['missing']
        with self.assertRaisesRegex(ValueError,'SCENARIO_MISSING'):FunctionalContract.model_validate(raw)

    def test_known_limits_cannot_reverse(self):
        raw=contract();raw['requirements'][0]['minimum']=3.
        with self.assertRaisesRegex(ValueError,'RANGE_REVERSED'):FunctionalContract.model_validate(raw)

    def test_wrong_engine_cannot_measure_logic(self):
        raw=contract();raw['requirements'][0]['quantity']='logic'
        with self.assertRaisesRegex(ValueError,'ENGINE_MISMATCH'):FunctionalContract.model_validate(raw)

    def test_missing_transient_window_cannot_pass(self):
        raw=contract();raw['requirements'][0]['time_window_s']=[0.,1.]
        with self.assertRaisesRegex(ValueError,'WINDOW_UNCOVERED'):FunctionalContract.model_validate(raw)

    def test_model_control_and_external_files_rejected(self):
        for text in ('.control\nshell touch /tmp/bad\n.endc','.include /tmp/file','.osdi /tmp/model','A1 1 0 custom','.model m nmos\n; shell echo bad','B1 1 0 v=file(/tmp/a)'):
            with self.subTest(text=text),self.assertRaises(ValueError):spice.model_lines(text)

    def test_numeric_model_and_subcircuit_are_data(self):
        self.assertEqual(len(spice.model_lines('.subckt example 1 2\nR1 1 2 1000\n.ends example')),3)

    def test_voltage_fixture_impedance_is_optional_and_part_of_the_bias_identity(self):
        base=dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),dc_v=5.)
        self.assertNotIn('series_resistance_ohm',Drive(**base).model_dump())
        self.assertNotIn('series_connection',Drive(**base).model_dump())
        ac=Scenario(id='ac',engine='spice_ac',frequency_hz=[100,1000],
                    drives=[Drive(**base,ac_v=.1,series_resistance_ohm=1000.)])
        matching=Scenario(id='op',engine='spice_op',drives=[Drive(**base,series_resistance_ohm=1000.)])
        ideal=Scenario(id='ideal',engine='spice_op',drives=[Drive(**base)])
        self.assertTrue(matching_dc_bias(ac,matching))
        self.assertFalse(matching_dc_bias(ac,ideal))

    def test_series_connection_requires_explicit_resistances_and_full_control(self):
        base=dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),dc_v=5.)
        control=dict(off_resistance_ohm=1e12,control_pwl=[[0.,1.],[.1,0.]])
        with self.assertRaisesRegex(ValueError,'SERIES_CONNECTION_REQUIRES_ON_RESISTANCE'):
            Drive(**base,series_connection=control)
        with self.assertRaisesRegex(ValueError,'SERIES_CONNECTION_RESISTANCE_ORDER_INVALID'):
            Drive(**base,series_resistance_ohm=1.,series_connection=dict(control,off_resistance_ohm=.5))
        with self.assertRaisesRegex(ValueError,'SERIES_CONNECTION_REQUIRES_ORDERED_BINARY_CONTROL_PWL'):
            Drive(**base,series_resistance_ohm=1.,series_connection=dict(control,control_pwl=[[0.,1.],[.1,.1]]))
        drive=Drive(**base,series_resistance_ohm=1.,series_connection=control)
        for scenario in [dict(engine='spice_op'),dict(engine='spice_transient',stop_s=.2,step_s=.001)]:
            with self.subTest(scenario=scenario),self.assertRaisesRegex(ValueError,'SERIES_CONNECTION_REQUIRES_COMPLETE_TRANSIENT_CONTROL'):
                Scenario(id='break_input',drives=[drive],**scenario)
        connected=Scenario(id='break_input',engine='spice_transient',stop_s=.1,step_s=.001,drives=[drive])
        self.assertEqual(connected.drives[0],drive)

    def test_operating_point_hint_is_optional_and_transient_only(self):
        hint = {'terminal': {'ref': 'U1', 'number': '4'}, 'guess_v': .39}
        with self.assertRaisesRegex(ValueError, 'OPERATING_POINT_HINT_REQUIRES_TRANSIENT'):
            Scenario(id='bias', engine='spice_op', operating_point_hints=[hint])
        transient = dict(id='startup', engine='spice_transient', stop_s=.1, step_s=.001)
        self.assertNotIn('operating_point_hints', Scenario(**transient).model_dump())
        actual = Scenario(**transient, operating_point_hints=[hint])
        self.assertEqual(actual.model_dump()['operating_point_hints'], [hint])


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native ngspice unavailable')
class NativeSpiceTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory()
    def tearDown(self):self.temp.cleanup()
    def run_deck(self,deck,vectors):return spice.run(deck,vectors,Path(self.temp.name)/'run')[1]

    def test_dc_bridge_against_independent_equations(self):
        result=self.run_deck('Bridge\nV1 p 0 10\nR1 p a 1k\nR2 a 0 1k\nR3 p b 2k\nR4 b 0 2k\nR5 a b 330\n.op\n.end\n',['v(a)','v(b)','i(v1)'])
        self.assertAlmostEqual(result['v(a)'][0],5.);self.assertAlmostEqual(result['v(b)'][0],5.);self.assertAlmostEqual(result['i(v1)'][0],-.0075)

    def test_transient_against_rc_closed_form(self):
        waves=self.run_deck('RC step\nV1 p 0 PULSE(0 1 1u 1n 1n 10m 20m)\nR1 p o 1k\nC1 o 0 1u\n.tran 1u 5m 0 1u\n.end\n',['time','v(o)'])
        measured=np.interp(.001001,waves['time'],waves['v(o)'])
        self.assertAlmostEqual(measured,1-np.exp(-1),delta=2e-5)

    def test_complex_ac_against_rc_transfer(self):
        waves=self.run_deck('RC AC\nV1 p 0 AC 1\nR1 p o 1k\nC1 o 0 1u\n.ac lin 3 100 200\n.end\n',['frequency','v(o)'])
        expected=1/(1+2j*np.pi*waves['frequency']*.001)
        np.testing.assert_allclose(waves['v(o)'],expected,rtol=1e-8)


if __name__=='__main__':unittest.main()
