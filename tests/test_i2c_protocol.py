import unittest

import numpy as np

from pcb_mcp.functional import Drive, I2CMeasurement, Scenario
from pcb_mcp.i2c_protocol import logic_edges, measure


def address_trace():
    """An address-only write to 0x48 with a NACK, then a STOP."""
    time = np.arange(130, dtype=float) * 1e-6
    sda = np.full(len(time), 3.3)
    scl = sda.copy()
    sda[6:] = 0
    scl[11:] = 0
    for i, bit in enumerate([1, 0, 0, 1, 0, 0, 0, 0, 1]):
        start = 11 + 10*i
        sda[start+2:] = 3.3 if bit else 0
        scl[start+5:start+10] = 3.3
    sda[103:] = 0
    scl[107:] = 3.3
    sda[113:] = 3.3
    return sda, scl, time


class I2CProtocolTests(unittest.TestCase):
    def test_decodes_explicit_nack_and_checks_timing_against_waveform(self):
        data, clock, time = address_trace()
        spec = I2CMeasurement.model_validate(dict(clock_positive=dict(ref='J1',number='3'),
            clock_negative=dict(ref='J1',number='4'), low_v=.99, high_v=2.31,
            maximum_sample_interval_s=1.1e-6, timing=dict(minimum_clock_high_s=4e-6),
            expected=[dict(start='start',address=0x48,direction='write',address_ack=False,data=[])]))
        result = measure(data, clock, time, [0.,float(time[-1])], spec)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['frames'][0]['address'],0x48)
        self.assertEqual(result['boundaries'][0]['end'],'stop')
        slower = spec.timing.model_copy(update={'minimum_clock_high_s':5e-6})
        self.assertFalse(measure(data, clock, time, [0.,float(time[-1])], spec.model_copy(update={'timing':slower}))['passed'])
        with self.assertRaisesRegex(ValueError,'I2C_TRACE_TOO_COARSE'):
            measure(data[::2], clock[::2], time[::2], [0.,float(time[-2])], spec)

    def test_unknown_band_excursion_cannot_be_treated_as_stable_logic(self):
        with self.assertRaisesRegex(ValueError,'I2C_AMBIGUOUS_LOGIC_EXCURSION'):
            logic_edges(np.array([3.3,1.5,3.3]),np.arange(3.),.99,2.31)

    def test_open_drain_is_exclusive_and_transient_only(self):
        positive=dict(ref='J1',number='1');negative=dict(ref='J1',number='2')
        self.assertNotIn('open_drain',Drive(positive=positive,negative=negative,dc_v=3.3).model_dump())
        sink=dict(kind='open_drain',positive=positive,negative=negative,
            open_drain=dict(on_resistance_ohm=100.,off_resistance_ohm=1e9,control_pwl=[[0.,0.],[1e-3,1.]]))
        with self.assertRaisesRegex(ValueError,'OPEN_DRAIN_USES_ONLY_SWITCH_CONTROL'):
            Drive.model_validate(dict(sink,dc_v=3.3))
        with self.assertRaisesRegex(ValueError,'OPEN_DRAIN_REQUIRES_COMPLETE_TRANSIENT_CONTROL'):
            Scenario.model_validate(dict(id='case',engine='spice_op',drives=[sink]))


if __name__=='__main__':
    unittest.main()
