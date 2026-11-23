"""Temperature interior minima and actual series-branch topology failures."""
from pathlib import Path
import tempfile
import unittest
from pcb_mcp.dc_limits import TemperatureLimit,LEDLimits,PassSwitchLimits,validate_scope
from pcb_mcp.dc_led_branch import evaluate
from pcb_mcp import spice


def curve(value):return dict(points=[[-40.,value],[85.,value]])

def fixture(resistance=1000.):
    def component(ref,numbers,library):
        return dict(id=ref,ref=ref,library_id=library,pins=[dict(pin_id=n,number=n) for n in numbers])
    components={r:component(r,p,l) for r,p,l in [('J1',['1','2'],'Connector_Generic:Conn_01x02'),
        ('R1',['1','2'],'Device:R'),('D1',['1','2'],'Device:LED'),
        ('U1',['1','2','3','4'],'EXT_TEST:Switch')]}
    def pin(r,n):return dict(component_id=r,pin_id=n)
    nets={name:dict(id=name,pins=[pin(*p) for p in ps]) for name,ps in [
        ('VIN',[('J1','1'),('U1','1')]),('OUT',[('U1','2'),('R1','1')]),
        ('A',[('R1','2'),('D1','2')]),('GND',[('J1','2'),('D1','1'),('U1','3')]),
        ('CTRL',[('U1','4')]) ]}
    state=dict(components=components,nets=nets,part_bindings={'R1':dict(facts=dict(value_ohm=resistance,tolerance_fraction=.01,
        temperature_coefficient_ppm_per_k=0.,temperature_range_c=[-40.,85.],rated_power_w=.125))},
        power_envelope=dict(positive_pin=pin('J1','1'),ground_net_id='GND',min_v=4.75,nominal_v=5.,max_v=5.25,ambient_c=25.,ambient_range_c=[0.,60.]),
        power_sources={'source':dict(boundary_pin=pin('J1','1'),net_id='VIN',ground_net_id='GND',nominal_v=5.)})
    led=LEDLimits(anode_pin='2',cathode_pin='1',maximum_forward_current_a=curve(.03),
        maximum_power_w=curve(.075),maximum_reverse_voltage_v=curve(5.))
    switch=PassSwitchLimits(input_pin='1',output_pin='2',ground_pin='3',enable_pin='4',supply_range_v=[1.6,5.5],
        maximum_continuous_current_a=curve(1.5),maximum_on_resistance_ohm=.25,maximum_off_leakage_a=1e-6,
        conditions='Synthetic test ratings only; no manufacturer or actual device qualification.')
    models={'D1':('led-model',dict(temperature_range_c=[-40.,85.]),led),
            'U1':('switch-model',dict(temperature_range_c=[-40.,85.]),switch)}
    return state,models


class RatingCurves(unittest.TestCase):
    def test_interior_valley_cannot_be_hidden_by_endpoint_checks(self):
        c=TemperatureLimit(points=[[-40.,.03],[25.,.03],[45.,.004],[85.,.02]])
        self.assertEqual(c.minimum([0.,60.]),.004)
        self.assertAlmostEqual(c.minimum([50.,60.]),.006)

    def test_no_temperature_extrapolation_or_invalid_breakpoints(self):
        for pts in ([[0.,1.],[0.,2.]],[[1.,1.],[0.,2.]],[[0.,float('nan')],[1.,1.]]):
            with self.assertRaises(ValueError):TemperatureLimit(points=pts)
        with self.assertRaisesRegex(ValueError,'OUTSIDE_SCOPE'):
            TemperatureLimit(**curve(.03)).minimum([-41.,60.])

    def test_declared_range_and_pin_roles_require_complete_coverage(self):
        _,models=fixture();led=models['D1'][2]
        validate_scope(led,['1','2'],[0.,60.])
        for pins,span in [(['1'],[0.,60.]),(['1','2'],[-40.,100.])]:
            with self.assertRaises(ValueError):validate_scope(led,pins,span)


class ActualBranches(unittest.TestCase):
    def check(self,s,models):return evaluate(s,s['components']['D1'],models)

    def test_only_source_bound_output_discharge_terminal_can_share_feed(self):
        s,models=fixture()
        s['components']['U1']['pins'].append(dict(pin_id='5',number='5',electrical_type='output'))
        s['nets']['OUT']['pins'].append(dict(component_id='U1',pin_id='5'))
        with self.assertRaisesRegex(ValueError,'ANOTHER_DRIVER'):self.check(s,models)
        models['U1'][2].discharge_pin='5'
        validate_scope(models['U1'][2],['1','2','3','4','5'],[0.,60.]);self.check(s,models)
        models['U1'][2].discharge_pin='2'
        with self.assertRaisesRegex(ValueError,'PIN_ROLES'):validate_scope(models['U1'][2],['1','2','3','4','5'],[0.,60.])

    def test_new_parameter_branches_preserve_independent_dc_upper_bound(self):
        for resistance in (1000.,1500.,2200.):
            s,models=fixture(resistance);r=self.check(s,models)
            self.assertAlmostEqual(r['dc_current_upper_reference_a'],5.25/(.99*resistance))
            self.assertEqual(r['temperature_range_c'],[0.,60.])
            self.assertEqual(r['feed'],'U1')

    def test_missing_switch_model_and_wrong_actual_supply_mapping_fail(self):
        s,models=fixture();models.pop('U1')
        with self.assertRaises(ValueError):self.check(s,models)
        s,models=fixture();s['power_sources']['source']['net_id']='OUT'
        with self.assertRaises(ValueError):self.check(s,models)

    def test_shared_resistor_tap_cannot_hide_another_driver(self):
        s,models=fixture();s['nets']['A']['pins'].append(dict(component_id='J1',pin_id='3'))
        with self.assertRaisesRegex(ValueError,'HAS_TAP'):self.check(s,models)

    def test_feed_cannot_hide_active_output_or_choose_one_parallel_switch(self):
        import copy
        s,models=fixture();s['components']['X1']=dict(id='X1',ref='X1',library_id='EXT:Other',pins=[dict(pin_id='1',number='1',electrical_type='power_out')])
        s['nets']['OUT']['pins'].append(dict(component_id='X1',pin_id='1'))
        with self.assertRaisesRegex(ValueError,'ANOTHER_DRIVER'):self.check(s,models)
        s,models=fixture();s['components']['U2']=copy.deepcopy(s['components']['U1']);s['components']['U2'].update(id='U2',ref='U2');models['U2']=models['U1']
        for net,pin in [('VIN','1'),('OUT','2'),('GND','3'),('CTRL','4')]:s['nets'][net]['pins'].append(dict(component_id='U2',pin_id=pin))
        with self.assertRaisesRegex(ValueError,'ANOTHER_DRIVER'):self.check(s,models)

    def test_reversed_led_and_bypassed_resistor_fail(self):
        s,models=fixture();models['D1'][2].anode_pin='1';models['D1'][2].cathode_pin='2'
        with self.assertRaisesRegex(ValueError,'POLARITY'):self.check(s,models)
        s,models=fixture();s['nets']['A']['pins'].remove(dict(component_id='D1',pin_id='2'))
        s['nets']['OUT']['pins'].append(dict(component_id='D1',pin_id='2'))
        with self.assertRaisesRegex(ValueError,'RESISTOR_REQUIRED'):self.check(s,models)

    def test_hot_or_interior_derating_fail_even_when_nominal_25c_passes(self):
        s,models=fixture();models['D1'][2].maximum_forward_current_a=TemperatureLimit(points=[[-40.,.03],[25.,.03],[45.,.008],[85.,.02]])
        with self.assertRaisesRegex(ValueError,'CURRENT_MARGIN'):self.check(s,models)
        s['power_envelope']['ambient_range_c']=None
        self.check(s,models)

    def test_voltage_temperature_scope_and_insufficient_resistance_fail(self):
        for change,code in [('voltage','UNSUPPORTED'),('temperature','TEMPERATURE'),('resistor','CURRENT_MARGIN')]:
            s,models=fixture()
            if change=='voltage':s['power_envelope']['max_v']=6.
            elif change=='temperature':s['power_envelope']['ambient_range_c']=[0.,90.]
            else:s['part_bindings']['R1']['facts']['value_ohm']=100.
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,code):self.check(s,models)

    @unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native solver unavailable')
    def test_native_nonlinear_led_never_exceeds_vf_zero_bound(self):
        s,models=fixture();bound=self.check(s,models)['dc_current_upper_reference_a']
        with tempfile.TemporaryDirectory() as d:
            deck='Independent diode branch\nV1 in 0 5.25\nR1 in a 990\nD1 a 0 light\n.model light D(Is=1e-20 N=2)\n.op\n.end\n'
            _,waves=spice.run(deck,['i(v1)','v(a)'],Path(d)/'run')
            current=-float(waves['i(v1)'][0]);self.assertGreater(current,0.)
            self.assertLess(current,bound)
            self.assertAlmostEqual(current,(5.25-float(waves['v(a)'][0]))/990.,delta=1e-10)


if __name__=='__main__':unittest.main()
