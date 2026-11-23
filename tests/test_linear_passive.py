"""Numerical and coverage faults, independent of KiCad geometry."""
import copy
import unittest
from unittest.mock import patch
from pcb_mcp.linear_passive import solve,analyze


def network():
    s=dict(id='a'*32,revision=1,components={},part_bindings={},footprint_bindings={},nets={},power_sources={})
    for nid in ('vin','out','gnd'):s['nets'][nid]=dict(id=nid,pins=[])
    for ref,nets in [('R1',('vin','out')),('R2',('out','gnd'))]:
        s['components'][ref]=dict(id=ref,ref=ref,library_id='Device:R',pins=[dict(pin_id='1'),dict(pin_id='2')])
        s['part_bindings'][ref]=dict(candidate_id=ref,kind='resistor',evidence_status='available',definition_hash=ref,facts=dict(value_ohm=100000.,tolerance_fraction=.01,temperature_coefficient_ppm_per_k=100.,temperature_range_c=[-55.,155.],rated_power_w=.1,voltage_rating_v=75.))
        for number,nid in enumerate(nets,1):s['nets'][nid]['pins'].append(dict(component_id=ref,pin_id=str(number)))
    pin=dict(component_id='R1',pin_id='1')
    s['power_sources']['S']=dict(boundary_pin=pin,net_id='vin',ground_net_id='gnd',nominal_v=5.)
    s['power_envelope']=dict(positive_pin=pin,ground_net_id='gnd',nominal_v=5.,min_v=4.75,max_v=5.25,available_current_ma=20.,ambient_c=25.)
    return s


class LinearTests(unittest.TestCase):
    def setUp(self):self.s=network()
    def analyze(self,**kwargs):
        with patch('pcb_mcp.parts.catalog',return_value=list(self.s['part_bindings'].values())),patch('pcb_mcp.parts.binding_valid',return_value=True):return analyze(self.s,**kwargs)

    def test_general_bridge_not_divider_special_case(self):
        r=[('p','a',1000.),('a','0',1000.),('p','b',2000.),('b','0',2000.),('a','b',330.)]
        volts,currents=solve(['p','a','b','0'],'0',r,[('p','0',10.)])
        self.assertAlmostEqual(volts['a'],5.);self.assertAlmostEqual(volts['b'],5.)
        self.assertAlmostEqual(currents[0],.0075)

    def test_differential_source(self):
        volts,_=solve(['a','b','0'],'0',[('a','0',1000.),('b','0',1000.)],[('a','b',6.)])
        self.assertAlmostEqual(volts['a'],3.);self.assertAlmostEqual(volts['b'],-3.)

    def test_floating_node_is_error(self):
        with self.assertRaisesRegex(ValueError,'FLOATING'):solve(['p','floating','0'],'0',[('p','0',1000.)],[('p','0',5.)])

    def test_conflicting_sources_is_error(self):
        with self.assertRaisesRegex(ValueError,'CONFLICTING'):solve(['p','0'],'0',[],[('p','0',5.),('p','0',3.)])

    def test_actual_network_corners(self):
        result=self.analyze();self.assertEqual(result['verdict'],'pass');self.assertEqual(result['corners'],8)
        self.assertAlmostEqual(result['nominal_voltages']['out'],2.5)
        # The conservative source temperature span actually misses the proposed
        # 2.7 V requirement; CAD success must not mask that functional failure.
        self.assertGreater(result['rails']['out']['min_v'],2.3)
        self.assertAlmostEqual(result['rails']['out']['max_v'],2.706357914837884)

    def test_source_overload(self):
        self.s['power_envelope']['available_current_ma']=.001
        self.assertIn('BUDGET_SOURCE_CAPACITY',{f['code'] for f in self.analyze()['findings']})

    def test_known_overpower_stays_error(self):
        self.s['part_bindings']['R1']['facts']['rated_power_w']=1e-9
        self.assertEqual(self.analyze()['verdict'],'fail')

    def test_missing_source_range_never_conditional(self):
        self.s.pop('power_envelope');self.assertEqual(self.analyze()['verdict'],'incomplete')

    def test_missing_ratings_never_conditional(self):
        self.s['part_bindings']['R1']['facts'].pop('tolerance_fraction')
        self.assertEqual(self.analyze()['verdict'],'incomplete')

    def test_no_random_sampling_pass(self):self.assertEqual(self.analyze(max_corners=2)['verdict'],'incomplete')

    def test_unknown_active_not_ignored(self):
        self.s['part_bindings']['R1']['kind']='transistor';self.assertIsNone(self.analyze())

    def test_existing_load_declaration_not_ignored(self):
        self.s['power_budget_policy']=dict(reserves=[dict(net_id='vin',unmodeled_dc_reserve_ma=10.)])
        self.assertEqual(self.analyze()['verdict'],'fail')

    def test_stale_source(self):
        self.s['power_sources']['S']['net_id']='out';self.assertEqual(self.analyze()['verdict'],'fail')

    def test_capacitor_leakage_not_zero_proof(self):
        self.s['components']['R2']['library_id']='Device:C';self.s['part_bindings']['R2']['kind']='capacitor'
        result=self.analyze();self.assertEqual(result['verdict'],'incomplete')
        self.assertIn('DC_CAPACITOR_IDEALIZATION',{f['code'] for f in result['findings']})


if __name__=='__main__':unittest.main()
