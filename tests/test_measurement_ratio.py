"""Dimensioned, signed aggregate ratios and real multiport power balance."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from pcb_mcp import functional,measurements,registry
from test_external_ports import fixture,terminal


def term(ref,quantity='power',scope='external_port',polarity=1):
    return dict(positive=terminal(ref),negative=terminal(ref,'2'),quantity=quantity,measurement_scope=scope,polarity=polarity)


def requirement():
    return dict(id='efficiency',description='Sum of delivered load power divided by absorbed input power',
        quantity='ratio',minimum=.39999,maximum=.40001,scenarios=['case'],aggregation='final',
        ratio=dict(numerator=[term('J2'),term('J3')],denominator=[term('J1',polarity=-1)]))


class RatioNumerics(unittest.TestCase):
    def test_ratio_of_integrals_is_not_mean_of_pointwise_ratios(self):
        t=np.array([0.,.01,.1,1.])
        r=measurements.ratio_of_aggregates([t],[1+t],'average',t)
        self.assertAlmostEqual(r['ratio'],1/3,places=14)
        self.assertAlmostEqual(r['numerator_aggregate'],.5)
        self.assertAlmostEqual(r['denominator_aggregate'],1.5)
        self.assertNotAlmostEqual(r['ratio'],1-np.log(2),places=4)

    def test_sum_precedes_rms_and_same_window_is_used(self):
        t=np.array([0.,.1,1.])
        r=measurements.ratio_of_aggregates([t,-t],[1+t],'rms',t,[.1,.9])
        self.assertEqual(r['ratio'],0.)
        self.assertAlmostEqual(measurements.ratio_of_aggregates([t],[1+t],'average',t,[.1,.9])['ratio'],1/3)

    def test_zero_cancellation_and_vector_broadcast_are_refused(self):
        for numerator,denominator,time,code in [([np.array([1.,1.])],[np.array([-1.,1.])],[0.,1.],'DENOMINATOR_TOO_SMALL'),
                ([np.ones(3)],[np.ones(1)],[0.,1.,2.],'VECTOR_LENGTH_MISMATCH')]:
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,code):
                measurements.ratio_of_aggregates(numerator,denominator,'average',time)

    def test_polarity_is_not_silently_replaced_by_absolute_value(self):
        self.assertEqual(measurements.ratio_of_aggregates([np.array([2.])],[np.array([-5.])],'final')['ratio'],-.4)


class RatioContracts(unittest.TestCase):
    def test_term_dimensions_duplicates_and_dead_fields_are_rejected(self):
        raw=requirement();functional.FunctionalRequirement.model_validate(raw)
        bad=copy.deepcopy(raw);bad['ratio']['denominator'][0]['quantity']='current'
        with self.assertRaisesRegex(ValueError,'MATCHING_DIMENSIONS'):functional.FunctionalRequirement.model_validate(bad)
        bad=copy.deepcopy(raw);bad['ratio']['numerator'].append(term('J2',polarity=-1))
        with self.assertRaisesRegex(ValueError,'DUPLICATE_OBSERVATION'):functional.FunctionalRequirement.model_validate(bad)
        for change in [dict(positive=terminal('J1')),dict(aggregation='range'),dict(quantity='voltage')]:
            with self.subTest(change=change),self.assertRaises(ValueError):functional.FunctionalRequirement.model_validate(dict(raw,**change))

    def test_ac_or_linear_cannot_claim_unimplemented_power_ratio(self):
        for engine in ['spice_ac','linear_dc']:
            case=dict(id='case',engine=engine)
            if engine=='spice_ac':case['frequency_hz']=[10.,100.]
            with self.assertRaisesRegex(ValueError,'RATIO_REQUIRES_OP_OR_TRANSIENT|LINEAR_DC_MEASUREMENT_ADAPTER_REQUIRED'):
                functional.FunctionalContract(description='No implicit ratio engine conversions',requirements=[requirement()],scenarios=[case])


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native ngspice unavailable')
class NativePowerRatios(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.state,self.records=fixture();self.counter=0
    def tearDown(self):self.temp.cleanup()
    def evaluate(self,requirements):
        case=dict(id='case',engine='spice_op',drives=[dict(positive=terminal('J1'),negative=terminal('J1','2'),dc_v=5.)],
            boundary_loads=[dict(positive=terminal('J2'),negative=terminal('J2','2'),resistance_ohm=1000.),dict(positive=terminal('J3'),negative=terminal('J3','2'),resistance_ohm=2000.)])
        contract=functional.FunctionalContract(description='Real native multiport resistor power ratio',requirements=requirements,scenarios=[case]);request=SimpleNamespace(contract=contract,contract_sha256=registry.sha(registry.canonical(contract.model_dump(mode='json'))),model_bindings={})
        self.counter+=1;folder=Path(self.temp.name)/str(self.counter);folder.mkdir()
        with patch('pcb_mcp.functional.resolved_parts',return_value=self.records):return functional.evaluate(self.state,request,folder)

    def test_actual_input_loads_and_dissipation_sum_to_one(self):
        delivered=requirement();loss=copy.deepcopy(delivered);loss.update(id='loss',minimum=.59999,maximum=.60001);loss['ratio']['numerator']=[term('R1',scope='component')]
        result=self.evaluate([delivered,loss]);self.assertEqual(result['verdict'],'pass',result['findings']);rows=result['measurements']
        self.assertAlmostEqual(rows[0]['minimum'],.4,places=12);self.assertAlmostEqual(rows[1]['minimum'],.6,places=12)
        self.assertAlmostEqual(sum(x['minimum'] for x in rows),1.,places=12)
        self.assertAlmostEqual(rows[0]['ratio_evidence']['denominator_aggregate'],.015,places=12)

    def test_wrong_input_power_direction_fails_the_frozen_bound(self):
        raw=requirement();raw['ratio']['denominator'][0]['polarity']=1
        result=self.evaluate([raw]);self.assertEqual(result['verdict'],'fail');self.assertAlmostEqual(result['measurements'][0]['minimum'],-.4)

    def test_component_dissipation_cannot_stand_in_for_port_transfer(self):
        raw=requirement();raw['ratio']['denominator'][0]['measurement_scope']='component'
        result=self.evaluate([raw]);self.assertEqual(result['verdict'],'fail');self.assertIn('MEASUREMENT_COMPONENT_OR_PORT_SCOPE_MISMATCH',{x['code'] for x in result['findings']})

    def test_actual_zero_reference_does_not_produce_a_ratio(self):
        raw=requirement();raw['ratio']=dict(numerator=[term('J2',quantity='voltage',scope='component')],
            denominator=[dict(positive=terminal('J1','2'),negative=terminal('J1','2'),quantity='voltage')])
        result=self.evaluate([raw]);self.assertEqual(result['verdict'],'fail');self.assertIn('RATIO_DENOMINATOR_TOO_SMALL',{x['code'] for x in result['findings']});self.assertFalse(result['measurements'])


if __name__=='__main__':unittest.main()
