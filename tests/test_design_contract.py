"""Composition faults across module boundaries, units and real graph structure."""
import copy
import json
from typing import get_args
import unittest
from pcb_mcp.design_contract import Interval, MEASUREMENT_DIMENSIONS, Proposal, compose
from pcb_mcp.design_jobs import check_requirement_binding
from pcb_mcp.functional import FunctionalContract, FunctionalRequirement


def endpoint(ref, number): return dict(ref=ref,number=number)


def module(ident, direction, lo, hi, current, unit='V'):
    port=dict(name='supply',kind='power',direction=direction,pins=[endpoint('J1','1')],reference='ground',basis='Declared test source or load, not a device model')
    if direction=='output': port.update(drive_voltage=dict(minimum=lo,maximum=hi,unit=unit),available_current=dict(value=current,unit='mA'))
    else: port.update(accept_voltage=dict(minimum=lo,maximum=hi,unit=unit),required_current=dict(value=current,unit='mA'))
    return dict(id=ident,function='Synthetic module interface fixture',
                components=[dict(ref='J1',mpn='SYNTHETIC_TEST',part_definition_hash='a'*64)],
                ports=[port,dict(name='ground',kind='reference',direction='passive',pins=[endpoint('J1','2')],basis='Declared common reference')])


def proposal():
    return dict(name='Two module interface test',intent=dict(description='Supply the declared load from the declared source.',requirements=[dict(id='rail',description='Load supply must remain within limits.',metric='voltage',target=['load','supply'],allowed=dict(minimum=3.0,maximum=3.6,unit='V'))]),
                modules=[module('source','output',3.135,3.465,20.),module('load','input',3.0,3.6,10.)],
                connections=[dict(ports=[['source','supply'],['load','supply']]),dict(ports=[['source','ground'],['load','ground']])],
                settings=[dict(tool='board_constraints_set',args=dict(width_mm=60.,height_mm=40.))])


class CompositionTests(unittest.TestCase):
    def setUp(self): self.raw=proposal()
    def check(self): return compose(Proposal.model_validate(self.raw))
    def codes(self): return {f['code'] for f in self.check()['findings']}

    def test_composition_has_unique_refs_and_retains_intent(self):
        result=self.check()
        self.assertEqual(result['verdict'],'pass')
        self.assertEqual([c['ref'] for c in result['plan']['components']],['J1','J2'])
        self.assertEqual(len(result['plan']['nets']),2)
        self.assertIn('Load supply must remain within limits.',result['requirement_json'])
        self.assertFalse(result['functional_requirements_verified'])

    def test_unit_conversion_not_raw_number_comparison(self):
        self.raw['modules'][1]=module('load','input',3000.,3600.,10.,'mV')
        self.assertEqual(self.check()['verdict'],'pass')

    def test_voltage_outside_range_fails_before_plan(self):
        self.raw['modules'][0]['ports'][0]['drive_voltage']['maximum']=5.0
        result=self.check()
        self.assertIsNone(result['plan'])
        self.assertIn('PORT_VOLTAGE_OUTSIDE_RANGE',{f['code'] for f in result['findings']})

    def test_total_load_current_is_summed(self):
        self.raw['modules'].append(module('load2','input',3.0,3.6,15.))
        for item in self.raw['connections']: item['ports'].append(['load2',item['ports'][0][1]])
        self.assertIn('PORT_CURRENT_BUDGET_EXCEEDED',self.codes())

    def test_same_name_ground_is_not_silently_connected(self):
        self.raw['connections'].pop()
        self.assertIn('REFERENCE_DOMAINS_DISCONNECTED',self.codes())

    def test_power_and_reference_short_fails(self):
        self.raw['connections'].append(dict(ports=[['source','supply'],['load','ground']]))
        self.assertIn('INTERFACE_KIND_MISMATCH',self.codes())

    def test_multiple_drivers_not_silently_parallel(self):
        self.raw['modules'][1]=module('load','output',3.0,3.6,10.)
        self.assertIn('MULTIPLE_PORT_DRIVERS',self.codes())

    def test_missing_voltage_evidence_incomplete(self):
        del self.raw['modules'][0]['ports'][0]['drive_voltage']
        self.assertEqual(self.check()['verdict'],'incomplete')
        self.assertIn('PORT_VOLTAGE_UNDECLARED',self.codes())

    def test_dimension_mismatch_rejected(self):
        self.raw['modules'][0]['ports'][0]['drive_voltage']['unit']='mA'
        with self.assertRaisesRegex(ValueError,'DIMENSION_MISMATCH'): self.check()

    def test_unknown_units_rejected(self):
        self.raw['intent']['requirements'][0]['allowed']['unit']='unknown'
        with self.assertRaisesRegex(ValueError,'UNIT_NOT_SUPPORTED'): self.check()

    def test_reversed_or_nonfinite_bounds_rejected(self):
        for value in (0.0,float('nan')):
            self.raw['intent']['requirements'][0]['allowed']['maximum']=value
            with self.assertRaises(ValueError): self.check()

    def test_disconnected_external_port_fails(self):
        self.raw['connections'].pop(0)
        self.assertIn('UNCONNECTED_MODULE_PORT',self.codes())

    def test_unknown_requirement_target_rejected(self):
        self.raw['intent']['requirements'][0]['target']=['missing','port']
        with self.assertRaisesRegex(ValueError,'UNKNOWN_REQUIREMENT_TARGET'): self.check()

    def test_reference_objects_translate_to_generated_entities(self):
        self.raw['settings'].append(dict(tool='power_source_set',args=dict(boundary_pin={'$port_pin':['source','supply']},ground_net_id={'$port_net':['source','ground']},nominal_v=3.3,source_kind='external_supply',origin='proposed_assumption',reason='Explicit source declaration for a test only')))
        result=self.check()
        args=result['plan']['settings'][-1]['args']
        self.assertEqual(args['boundary_pin'],{'$pin':['J1','1']})
        self.assertEqual(args['ground_net_id'],{'$net':result['port_nets']['source:ground']})

    def test_no_python_expression_reference(self):
        self.raw['settings'][0]['args']['width_mm']={'$eval':['arbitrary']}
        with self.assertRaisesRegex(ValueError,'UNKNOWN_PROPOSAL_REFERENCE'): self.check()

    def test_local_no_connect_may_not_also_be_port(self):
        self.raw['modules'][0]['no_connects']=[dict(pin=endpoint('J1','1'),reason='Intentionally invalid fixture')]
        with self.assertRaisesRegex(ValueError,'CONFLICTING_LOCAL_NO_CONNECT'): self.check()


class JobMeasurementBindingTests(unittest.TestCase):
    def test_all_scalar_quantities_have_a_dimension(self):
        quantity = FunctionalRequirement.model_fields['quantity'].annotation
        self.assertEqual(set(get_args(quantity)), MEASUREMENT_DIMENSIONS.keys())

    def test_decimal_unit_conversion_and_count_dimensions(self):
        self.assertEqual(Interval(minimum=850, maximum=950, unit='ns').normalized('time'),
                         (8.5e-7, 9.5e-7))
        with self.assertRaisesRegex(ValueError, 'QUANTITY_DIMENSION_MISMATCH'):
            Interval(minimum=4, maximum=4, unit='frame').normalized('word_count')

    def test_observation_window_binds_exact_measurement_interval(self):
        contract = FunctionalContract.model_validate(dict(description='Transient voltage fixture for job binding.',
            scenarios=[dict(id='step', engine='spice_transient', stop_s=0.002, step_s=1e-7)],
            requirements=[dict(id='out', description='Observe the actual output over the stated window.',
                positive=endpoint('J1', '1'), negative=endpoint('J1', '2'), quantity='voltage',
                minimum=3.0, maximum=3.3, scenarios=['step'], time_window_s=[8.5e-7, 9.5e-7])]))
        row = dict(id='out', allowed=dict(minimum=3000, maximum=3300, unit='mV'),
                   conditions={'observation_window': dict(minimum=850, maximum=950, unit='ns')})
        document = dict(natural_language='Measure the voltage in the declared observation window.',
                        formal_requirements=[row])
        self.assertEqual(check_requirement_binding(json.dumps(document), contract), document)
        row['conditions']['observation_window']['minimum'] = 849
        with self.assertRaisesRegex(ValueError, 'FORMAL_MEASUREMENT_WINDOW_UNCOVERED'):
            check_requirement_binding(json.dumps(document), contract)

    def test_explicit_i2c_pullup_requirements_bind_both_terminals_and_resistors(self):
        pullup = dict(id='pullups', sda=endpoint('U1', '4'), scl=endpoint('U1', '5'), supply=endpoint('U1', '1'),
                      sda_ohm=10000, scl_ohm=10000)
        contract = FunctionalContract.model_validate(dict(description='Both lines use 10 kΩ pull-up resistors.',
            scenarios=[], requirements=[], i2c_pullups=[pullup]))
        document = dict(natural_language='Pull SDA and SCL up to the declared supply.')
        self.assertEqual(check_requirement_binding(json.dumps(document), contract), document)
        document['formal_requirements'] = [pullup]
        self.assertEqual(check_requirement_binding(json.dumps(document), contract), document)
        for changed in (dict(pullup, sda_ohm=4700), dict(pullup, supply=endpoint('U1', '2'))):
            document['formal_requirements'] = [changed]
            with self.assertRaisesRegex(ValueError, 'FROZEN_I2C_PULLUP_CHANGED'):
                check_requirement_binding(json.dumps(document), contract)


if __name__=='__main__': unittest.main()
