"""Analytic carrier, harmonic and in-band nonharmonic calibration."""
import math
import unittest

import numpy as np

from pcb_mcp.audio_spectrum import measure, spectral_grid
from pcb_mcp.functional import THDNMeasurement


class AudioSpectrumTests(unittest.TestCase):
    def setUp(self):
        self.window = [.01, .09]
        self.settings = THDNMeasurement(fundamental_hz=1000., bandwidth_hz=[25.,4000.],
                                        minimum_fundamental_rms_v=.4)

    def test_in_band_harmonic_and_spur_exclude_dc_and_out_of_band_signal(self):
        clock = np.linspace(0, .1, 50001)
        time = clock + 4e-7 * np.sin(math.tau * 70 * clock)
        wave = (2.4 + 2 * np.sin(math.tau * 1000 * time + .3)
                + .04 * np.sin(math.tau * 2000 * time)
                + .03 * np.sin(math.tau * 1375 * time)
                + .5 * np.sin(math.tau * 8000 * time))
        result = measure(wave, time, self.window, self.settings)
        self.assertAlmostEqual(result['fundamental_rms_v'], math.sqrt(2), delta=2e-5)
        self.assertAlmostEqual(result['in_band_trace_residual_rms_v'], .05 / math.sqrt(2), delta=2e-5)

    def test_fundamental_floor_rejects_a_muted_or_wrong_carrier(self):
        time = np.linspace(0, .1, 10001)
        signal = np.sin(math.tau * 2000 * time)
        with self.assertRaisesRegex(ValueError, 'THDN_FUNDAMENTAL_BELOW_DECLARED_FLOOR'):
            measure(signal, time, self.window, self.settings)

    def test_bad_window_and_actual_raw_gap_fail(self):
        with self.assertRaisesRegex(ValueError, 'THDN_REQUIRES_COHERENT_WINDOW'):
            spectral_grid([.01, .0895], 1000., [25., 4000.])
        with self.assertRaisesRegex(ValueError, 'THDN_LOW_BAND_EDGE_UNRESOLVED'):
            spectral_grid([.01, .09], 1000., [10., 4000.])
        time = np.linspace(0, .1, 10001)
        time = time[(time < .055) | (time > .0551)]
        with self.assertRaisesRegex(ValueError, 'THDN_TRACE_TOO_COARSE'):
            measure(np.sin(math.tau * 1000 * time), time, self.window, self.settings)
