"""Coherent differential voltage measurements on saved solver samples."""
import math

import numpy as np

from .measurements import samples


def spectral_grid(window, fundamental_hz, bandwidth_hz):
    duration = window[1] - window[0]
    low, high = bandwidth_hz
    cycles = duration * fundamental_hz
    count = duration * high * 16
    if not math.isfinite(count) or count > 262144:
        raise ValueError('THDN_SPECTRAL_GRID_TOO_LARGE')
    carrier_bin = round(cycles)
    if carrier_bin < 4 or not math.isclose(cycles, carrier_bin, rel_tol=0, abs_tol=1e-8):
        raise ValueError('THDN_REQUIRES_COHERENT_WINDOW_OF_AT_LEAST_FOUR_CYCLES')
    if duration * low < 1 - 1e-9:
        raise ValueError('THDN_LOW_BAND_EDGE_UNRESOLVED')
    first = max(1, math.ceil(duration * low - 1e-9))
    last = math.floor(duration * high + 1e-9)
    if not first <= carrier_bin <= last or last == first:
        raise ValueError('THDN_IN_BAND_RESIDUAL_BINS_REQUIRED')
    return math.ceil(count), carrier_bin, first, last


def measure(signal, time, window, settings):
    count, carrier_bin, first, last = spectral_grid(window, settings.fundamental_hz, settings.bandwidth_hz)
    signal, time = samples(signal, time)
    start, end = window
    if time[0] > start or time[-1] < end:
        raise ValueError('TRANSIENT_WINDOW_NOT_COVERED')
    gaps = np.diff(time)[(time[:-1] < end) & (time[1:] > start)]
    maximum_gap = float(np.max(gaps))
    if maximum_gap * settings.bandwidth_hz[1] > .1 * (1 + 1e-8):
        raise ValueError('THDN_TRACE_TOO_COARSE')
    signal, time = samples(signal, time, window)
    relative = time - start
    duration = end - start
    phase = math.tau * carrier_bin * relative / duration
    basis = np.column_stack((np.ones(len(relative)), np.cos(phase), np.sin(phase)))
    # Remove the dominant carrier before interpolating the adaptive time grid.
    # The residual's carrier bin still contributes to the measured fundamental.
    intervals = np.diff(relative)
    weights = np.sqrt(np.r_[intervals[0], intervals[:-1] + intervals[1:], intervals[-1]] / 2)
    fit, _, rank, _ = np.linalg.lstsq(basis * weights[:, None], signal * weights, rcond=None)
    if rank != 3:
        raise ValueError('THDN_CARRIER_FIT_UNRESOLVED')
    grid = np.arange(count) * (duration / count)
    spectrum = np.fft.rfft(np.interp(grid, relative, signal - basis @ fit))
    fundamental = float(abs(2 * spectrum[carrier_bin] / count + complex(fit[1], -fit[2])) / math.sqrt(2))
    if not math.isfinite(fundamental) or fundamental < settings.minimum_fundamental_rms_v:
        raise ValueError('THDN_FUNDAMENTAL_BELOW_DECLARED_FLOOR')
    band = spectrum[first:last + 1].copy()
    band[carrier_bin - first] = 0
    residual = float(np.linalg.norm(band) * math.sqrt(2) / count)
    if not math.isfinite(residual):
        raise ValueError('THDN_RESIDUAL_NONFINITE')
    return dict(
        fundamental_hz=settings.fundamental_hz, fundamental_rms_v=fundamental,
        in_band_trace_residual_rms_v=residual, bandwidth_hz=list(settings.bandwidth_hz),
        window_s=list(window), bin_width_hz=1 / duration, included_bins=[first, last],
        excluded_fundamental_bin=carrier_bin, uniform_samples=count,
        maximum_original_sample_interval_s=maximum_gap,
        method='time-weighted carrier fit, linear residual interpolation, coherent rectangular FFT, in-band RMS excluding DC and the fundamental',
    )
