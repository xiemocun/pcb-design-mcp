"""Thermal stimuli must alter the actual resistor, not an electrical input."""
from pathlib import Path
import tempfile,unittest
import numpy as np
from pcb_mcp.temperature_resistance import TemperatureResistance,TemperatureProfile,emit
from pcb_mcp.functional import Scenario,compile_spice
from pcb_mcp import spice


def model():return dict(engine='temperature_resistance',pins=['1','2'],temperature_range_c=[0.,60.],
    temperature_resistance=dict(pin_a='1',pin_b='2',nominal_points=[[0.,20000.],[30.,10000.],[60.,5000.]]))


class ThermalData(unittest.TestCase):
    def test_positive_finite_ordered_curve_and_real_pin_roles(self):
        for points in ([[0.,1.],[0.,2.]],[[0.,0.],[60.,2.]],[[0.,1.],[60.,float('inf')]]):
            with self.subTest(points=points),self.assertRaises(ValueError):
                TemperatureResistance(pin_a='1',pin_b='2',nominal_points=points)
        data=TemperatureResistance.model_validate(model()['temperature_resistance'])
        with self.assertRaisesRegex(ValueError,'PIN_ROLES'):data.validate_scope(['1','3'],[0.,60.])
        with self.assertRaisesRegex(ValueError,'OUTSIDE_SCOPE'):data.at(61.)

    def test_body_temperature_scope_does_not_expand_model_scope(self):
        m=model();m['temperature_range_c']=[10.,50.]
        for t in (0.,60.):
            with self.assertRaisesRegex(ValueError,'OUTSIDE_SCOPE'):emit(m,1,['p','n'],t,None)
        with self.assertRaisesRegex(ValueError,'OUTSIDE_SCOPE'):
            emit(m,1,['p','n'],25.,TemperatureProfile(points=[[0.,25.],[1.,60.]]))

    def test_time_order_coverage_and_engine_cannot_be_silently_ignored(self):
        for points in ([[1.,25.],[2.,30.]],[[0.,25.],[0.,30.]],[[0.,25.],[-1.,30.]]):
            with self.assertRaises(ValueError):TemperatureProfile(points=points)
        with self.assertRaisesRegex(ValueError,'REQUIRES_TRANSIENT'):
            Scenario(id='bad',engine='spice_op',body_temperature_profiles={'NT1':dict(points=[[0.,25.],[1.,30.]])})
        with self.assertRaisesRegex(ValueError,'COVERAGE'):
            Scenario(id='bad',engine='spice_transient',stop_s=2.,step_s=.01,
                body_temperature_profiles={'NT1':dict(points=[[0.,25.],[1.,30.]])})

    def test_unbound_or_wrong_component_profile_cannot_be_ignored(self):
        scenario=Scenario(id='bad',engine='spice_transient',stop_s=1.,step_s=.01,
            drives=[dict(positive=dict(ref='J1',number='1'),negative=dict(ref='J1',number='2'),dc_v=5.)],
            body_temperature_profiles={'NT1':dict(points=[[0.,25.],[1.,30.]])})
        s=dict(id='test',components={'J1':dict(id='J1',ref='J1',pins=[])},nets={
            'p':dict(pins=[dict(component_id='J1',pin_id='1')]),'n':dict(pins=[dict(component_id='J1',pin_id='2')])})
        s['components']['J1']['pins']=[dict(pin_id='1',number='1'),dict(pin_id='2',number='2')]
        for key,value in s['nets'].items():value['id']=key
        with self.assertRaisesRegex(ValueError,'PROFILE_MODEL_REQUIRED'):compile_spice(s,scenario,{}, {})

    @unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native solver unavailable')
    def test_native_heating_cooling_trace_matches_independent_ohms_law(self):
        profile=TemperatureProfile(points=[[0.,0.],[.001,60.],[.002,60.],[.003,0.],[.004,0.]])
        for p,n in [('p','0'),('0','p')]:
            lines=emit(model(),1,[p,n],25.,profile)
            deck='Thermal resistor independent current check\nV1 p 0 5\n'+'\n'.join(lines)+'\n.tran 1u 4m 0 1u\n.end\n'
            with tempfile.TemporaryDirectory() as d:
                _,w=spice.run(deck,['time','i(v1)'],Path(d)/'run')
                temp=np.interp(w['time'],[x[0] for x in profile.points],[x[1] for x in profile.points])
                resistance=np.interp(temp,[0.,30.,60.],[20000.,10000.,5000.])
                np.testing.assert_allclose(-w['i(v1)'],5/resistance,rtol=1e-9,atol=1e-12)


if __name__=='__main__':unittest.main()
