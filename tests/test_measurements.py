"""Independent time integrals and contract rejection for extended measurements."""
import copy
import math
import unittest
import numpy as np
from pcb_mcp.measurements import aggregate,periodic
from pcb_mcp.functional import FunctionalContract


class Measurements(unittest.TestCase):
    def test_irregular_samples_do_not_bias_mean(self):
        # y=t on [0,2]; the exact mean is 1, regardless of sample density.
        t=np.array([0.,.001,.002,.01,.2,2.])
        lo,hi=aggregate(t,'average',t)
        self.assertAlmostEqual(lo,1.);self.assertEqual(lo,hi)
        self.assertNotAlmostEqual(float(t.mean()),lo)

    def test_ramp_rms_matches_independent_integral(self):
        t=np.array([0.,.03,.1,.9,2.])
        self.assertAlmostEqual(aggregate(t,'rms',t)[0],2/math.sqrt(3),12)

    def test_window_uses_interpolated_boundaries(self):
        self.assertAlmostEqual(aggregate([0.,2.],'average',[0.,2.],[.5,1.5])[0],1.)
        self.assertAlmostEqual(aggregate([0.,2.],'peak_to_peak',[0.,2.],[.5,1.5])[0],1.)

    def test_signed_power_average_is_preserved(self):
        self.assertEqual(aggregate([-2.,-2.],'average',[0.,1.]),(-2.,-2.))
        self.assertEqual(aggregate([-2.,-2.],'rms',[0.,1.]),(2.,2.))

    def test_invalid_axis_nan_or_uncovered_window_refused(self):
        for values,time,window in [([0.,1.],[1.,0.],None),([0.,float('nan')],[0.,1.],None),([0.,1.],[0.,1.],[0.,2.])]:
            with self.assertRaises(ValueError):aggregate(values,'average',time,window)
        with self.assertRaisesRegex(ValueError,'NONZERO'):
            aggregate([1.,1.],'rms',[0.,1.],[.5,.5])

    def test_duplicate_boundary_samples_use_last_value(self):
        self.assertEqual(aggregate([9.,0.,1.],'average',[0.,0.,1.]),(.5,.5))

    def test_periodic_triangle_against_independent_period_and_duty(self):
        t=np.linspace(0.,5.,501);y=1-np.abs(2*(t%1)-1)
        result=periodic(y,t,.5)
        np.testing.assert_allclose(result['frequency'],1.,atol=1e-12)
        np.testing.assert_allclose(result['duty_cycle'],.5,atol=1e-12)
        self.assertGreaterEqual(result['evidence']['complete_cycles'],3)

    def test_short_or_constant_trace_cannot_prove_periodicity(self):
        for t,y in [(np.linspace(0.,1.,101),np.ones(101)),(np.linspace(0.,2.,201),np.sin(2*np.pi*np.linspace(0.,2.,201)))]:
            with self.assertRaisesRegex(ValueError,'THREE_COMPLETE_CYCLES'):
                periodic(y,t,.5)

    def test_device_current_and_power_require_actual_same_component(self):
        for quantity in ['current','power']:
            raw=dict(description='Bound a source-selected resistor measurement.',
                     requirements=[dict(id='load',description='Bound the actual load measurement.',
                     positive=dict(ref='R1',number='1'),negative=dict(ref='R1',number='2'),
                     quantity=quantity,minimum=0.,maximum=.1,scenarios=['dc'])],
                     scenarios=[dict(id='dc',engine='spice_op')])
            FunctionalContract.model_validate(raw)
            raw['requirements'][0]['negative']['ref']='J1'
            with self.assertRaisesRegex(ValueError,'SAME_COMPONENT'):
                FunctionalContract.model_validate(raw)

    def test_linear_and_ac_cannot_silently_use_voltage_for_power(self):
        for engine in ['linear_dc','spice_ac']:
            raw=dict(description='Bound a source-selected resistor measurement.',
                     requirements=[dict(id='load',description='Bound the actual load power.',
                     positive=dict(ref='R1',number='1'),negative=dict(ref='R1',number='2'),
                     quantity='power',minimum=0.,maximum=.1,scenarios=['dc'])],
                     scenarios=[dict(id='dc',engine=engine,frequency_hz=[1.,10.])])
            with self.assertRaises(ValueError):FunctionalContract.model_validate(raw)


if __name__=='__main__':unittest.main()
