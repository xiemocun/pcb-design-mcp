"""The previous word's LSB and next word's WS can share one rising edge."""
import unittest

import numpy as np

from pcb_mcp.functional import I2SMeasurement
from pcb_mcp.i2s_protocol import measure


class I2SProtocolTests(unittest.TestCase):
    def setUp(self):
        self.time = np.linspace(0, 9, 18001)
        self.spec = I2SMeasurement(
            clock_positive={'ref': 'J1', 'number': '1'}, clock_negative={'ref': 'J1', 'number': '4'},
            ws_positive={'ref': 'J1', 'number': '2'}, ws_negative={'ref': 'J1', 'number': '4'},
            low_v=.8, high_v=2., maximum_sample_interval_s=.001, word_bits=4, slot_bits=4,
            expected=[{'channel': 'left', 'value': 5}, {'channel': 'right', 'value': -3}],
            timing=dict(minimum_clock_period_s=.99, maximum_clock_period_s=1.01,
                        minimum_clock_low_s=.45, minimum_clock_high_s=.45,
                        minimum_data_setup_s=.2, minimum_data_hold_s=.2,
                        minimum_ws_setup_s=.2, minimum_ws_hold_s=.2),
        )
        self.clock = self.wave(0, [(i + phase, state) for i in range(9)
                                  for phase, state in ((.5, 1), (.98, 0))])
        self.select = self.wave(1, [(.05, 0), (4.05, 1), (8.05, 0)])

    def wave(self, initial, changes):
        points = [(0, initial)]
        level = initial
        for time, new in changes:
            if level != new:
                points.extend([(time, level), (time + .01, new)])
                level = new
        points.append((9, level))
        t, voltage = zip(*points)
        return np.interp(self.time, t, voltage) * 3.3

    def test_full_width_words_include_lsb_at_next_ws_boundary(self):
        data = self.wave(0, [(i+.1, bit) for i,bit in enumerate([0,0,1,0,1,1,1,0,1])])
        result = measure(data, self.clock, self.select, self.time, [0.,9.], self.spec)
        self.assertTrue(result['passed'])
        self.assertEqual([row['value'] for row in result['words']], [5,-3])
        self.assertEqual(result['words'][0]['slot_end_at_rise'], result['words'][1]['ws_latched_at_rise'])

    def test_left_justified_stream_with_valid_clock_is_rejected(self):
        data = self.wave(0, [(i+.1, bit) for i,bit in enumerate([0,1,0,1,1,1,0,1,0])])
        result = measure(data, self.clock, self.select, self.time, [0.,9.], self.spec)
        self.assertFalse(result['passed'])
        self.assertTrue(all(row['passed'] for row in result['timing_checks']))
        self.assertNotEqual([row['value'] for row in result['words']], [5,-3])

    def test_extra_ws_pulse_between_clock_rises_is_rejected(self):
        select = self.wave(1, [(.05, 0), (2.05, 1), (2.1, 0), (4.05, 1), (8.05, 0)])
        with self.assertRaisesRegex(ValueError, 'I2S_UNRESOLVED_OR_MISSING_WS_BOUNDARY'):
            measure(self.wave(0, []), self.clock, select, self.time, [0.,9.], self.spec)
