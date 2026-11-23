"""Measured frequency references and conservative crossing brackets."""
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
from pcb_mcp import functional,measurements,registry
from test_external_ports import fixture,terminal


def crossing(**extra):
    return functional.FrequencyCrossing(**dict(dict(reference_frequency_hz=1.,level_db=-2.5,
        direction='falling',search_window_hz=[1.,5.]),**extra))


def requirement(quantity,**extra):
    return dict(id='response',description='Actual transfer magnitude relative to a measured frequency',
        positive=terminal('J2'),negative=terminal('J2','2'),denominator=terminal('J1'),
        quantity=quantity,minimum=-50.,maximum=1.,scenarios=['case'],**extra)


class ACReferenceNumerics(unittest.TestCase):
    def test_absolute_and_relative_db_have_different_meanings(self):
        absolute,_=measurements.ac_gain_db([10.,5.],[100.,1000.])
        relative,evidence=measurements.ac_gain_db([10.,5.],[100.,1000.],100.)
        np.testing.assert_allclose(absolute,[20.,20*np.log10(5.)])
        np.testing.assert_allclose(relative,[0.,-20*np.log10(2.)])
        self.assertEqual(evidence['reference_gain'],10.)

    def test_reference_must_be_an_actual_unique_frequency(self):
        with self.assertRaisesRegex(ValueError,'MATCH_ONE_MEASURED'):
            measurements.ac_gain_db([1.,.5],[100.,1000.],500.)
        with self.assertRaisesRegex(ValueError,'MATCH_ONE_MEASURED'):
            measurements.ac_gain_db([1.,.9],[100.,100.+1e-9],100.)

    def test_zero_gain_bad_axis_and_complex_frequency_refused(self):
        for gain,axis in [([0.,1.],[1.,2.]),([1.,1.],[2.,1.]),([1.,1.],[1.,1.]),([1.,1.],[1.,2.+1j])]:
            with self.subTest(axis=axis),self.assertRaises(ValueError):measurements.ac_gain_db(gain,axis)

    def test_crossing_retains_sample_bracket_not_false_precision(self):
        r=measurements.ac_frequency_crossing([0.,-1.,-2.,-3.,-4.],[1.,2.,3.,4.,5.],crossing())
        self.assertEqual(r['estimate_hz'],3.5);self.assertEqual(r['bracket_hz'],[3.,4.])

    def test_exact_sample_crossing_is_degenerate_bracket(self):
        r=measurements.ac_frequency_crossing([0.,-1.,-2.,-3.,-4.],[1.,2.,3.,4.,5.],crossing(level_db=-3.))
        self.assertEqual(r['bracket_hz'],[4.,4.])

    def test_missing_crossing_and_uncovered_search_fail(self):
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):
            measurements.ac_frequency_crossing([0.,0.],[1.,5.],crossing())
        with self.assertRaisesRegex(ValueError,'UNCOVERED'):
            measurements.ac_frequency_crossing([0.,-4.],[2.,4.],crossing())


class ACReferenceContracts(unittest.TestCase):
    def test_reference_and_crossing_require_correct_quantity_and_coverage(self):
        with self.assertRaisesRegex(ValueError,'REFERENCE_REQUIRES_GAIN_DB'):
            functional.FunctionalRequirement(**requirement('gain',reference_frequency_hz=100.))
        raw=requirement('gain_db',reference_frequency_hz=100.)
        for case,code in [(dict(id='case',engine='spice_op'),'GAIN_REQUIRES_AC'),
                          (dict(id='case',engine='spice_ac',frequency_hz=[1.,10.]),'REFERENCE_FREQUENCY_UNCOVERED')]:
            with self.assertRaisesRegex(ValueError,code):
                functional.FunctionalContract(description='Reference coverage is explicit',requirements=[raw],scenarios=[case])
        bad=dict(requirement('ac_crossing_frequency',ac_crossing=crossing().model_dump()),minimum=1.,maximum=5.,aggregation='final')
        with self.assertRaisesRegex(ValueError,'CROSSING_OPTIONS_INVALID'):functional.FunctionalRequirement.model_validate(bad)


@unittest.skipUnless(Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice.0.dylib').is_file(),'Native ngspice unavailable')
class NativeACReference(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.state,self.records=fixture();self.count=0
    def tearDown(self):self.temp.cleanup()
    def evaluate(self,reqs,points=1001):
        case=dict(id='case',engine='spice_ac',frequency_hz=[1.,1001.],points=points,
            drives=[dict(positive=terminal('J1'),negative=terminal('J1','2'),dc_v=1.,ac_v=1.)],
            boundary_loads=[dict(positive=terminal('J2'),negative=terminal('J2','2'),capacitance_f=1e-6)])
        c=functional.FunctionalContract(description='Native RC measured AC reference',requirements=reqs,scenarios=[case]);request=SimpleNamespace(contract=c,contract_sha256=registry.sha(registry.canonical(c.model_dump(mode='json'))),model_bindings={})
        self.count+=1;folder=Path(self.temp.name)/str(self.count);folder.mkdir()
        with patch('pcb_mcp.functional.resolved_parts',return_value=self.records):return functional.evaluate(self.state,request,folder)

    def test_db_response_matches_independent_complex_transfer(self):
        raw=requirement('gain_db',reference_frequency_hz=1.)
        result=self.evaluate([raw]);self.assertEqual(result['verdict'],'pass',result['findings'])
        expected=20*np.log10(abs(1/(1+2j*np.pi*1001*.001))/abs(1/(1+2j*np.pi*.001)))
        self.assertAlmostEqual(result['measurements'][0]['minimum'],expected,places=9)
        self.assertAlmostEqual(result['measurements'][0]['maximum'],0.,places=12)

    def test_crossing_bracket_contains_analytic_rc_solution(self):
        level=-20*math.log10(math.sqrt(2.))
        raw=dict(requirement('ac_crossing_frequency',ac_crossing=dict(reference_frequency_hz=1.,level_db=level,direction='falling',search_window_hz=[1.,1001.])),minimum=158.9,maximum=160.1)
        result=self.evaluate([raw]);self.assertEqual(result['verdict'],'pass',result['findings'])
        m=result['measurements'][0];expected=math.sqrt(1+2*(2*math.pi*.001)**2)/(2*math.pi*.001)
        self.assertLessEqual(m['minimum'],expected);self.assertGreaterEqual(m['maximum'],expected)
        self.assertAlmostEqual(m['crossing_evidence']['estimate_hz'],expected,delta=.01)
        # A coarse grid must not pass merely because interpolation hits the band.
        coarse=self.evaluate([raw],points=11);self.assertEqual(coarse['verdict'],'fail')
        self.assertLess(coarse['measurements'][0]['minimum'],raw['minimum'])

    def test_unmeasured_reference_is_not_filled_by_interpolation(self):
        result=self.evaluate([requirement('gain_db',reference_frequency_hz=1.5)])
        self.assertEqual(result['verdict'],'fail');self.assertIn('AC_REFERENCE_MUST_MATCH_ONE_MEASURED_FREQUENCY',{x['code'] for x in result['findings']})


if __name__=='__main__':unittest.main()
