"""Decode standard I²S words and measure receiver timing on actual traces."""
from bisect import bisect_left, bisect_right

import numpy as np

from .digital_waveforms import logic_edges
from .measurements import samples


def sampled_levels(initial, signal, rises, window, name):
    ends = [edge.end for edge in signal]
    bits, setup, hold = [], [], []
    for rise in rises:
        next_index = bisect_right(ends, rise.start)
        prior = signal[next_index-1] if next_index else None
        following = signal[next_index] if next_index < len(signal) else None
        if following and following.start < rise.end:
            raise ValueError('I2S_' + name.upper() + '_TRANSITION_OVERLAPS_CLOCK_RISE')
        bits.append(prior.level if prior else initial)
        setup.append(rise.start - (prior.end if prior else window[0]))
        hold.append((following.start if following else window[1]) - rise.end)
    return bits, setup, hold


def measure(sd, sck, ws, time, window, spec):
    time = np.asarray(time)
    begin, end = window
    data, axis = samples(sd, time, window)
    clock, _ = samples(sck, time, window)
    select, _ = samples(ws, time, window)
    gaps = np.diff(time)[(time[:-1] < end) & (time[1:] > begin)]
    max_gap = float(np.max(gaps))
    if max_gap > spec.maximum_sample_interval_s * (1 + 1e-8):
        raise ValueError('I2S_TRACE_TOO_COARSE')

    data_initial, data_edges = logic_edges(data, axis, spec.low_v, spec.high_v, protocol='I2S')
    _, clock_edges = logic_edges(clock, axis, spec.low_v, spec.high_v, protocol='I2S')
    ws_initial, ws_edges = logic_edges(select, axis, spec.low_v, spec.high_v, protocol='I2S')
    rises = [edge for edge in clock_edges if edge.level]
    if len(rises) < 2 or len(ws_edges) < 2:
        raise ValueError('I2S_COMPLETE_WS_DELIMITED_WORD_REQUIRED')
    data_bits, data_setup, data_hold = sampled_levels(data_initial, data_edges, rises, window, 'data')
    _, ws_setup, ws_hold = sampled_levels(ws_initial, ws_edges, rises, window, 'ws')

    # WS announces the next channel at this rising edge. The MSB is sampled
    # one rising edge later, so the preceding slot can include the WS edge.
    rise_starts = [edge.start for edge in rises]
    boundary_indexes = [bisect_left(rise_starts, edge.end) for edge in ws_edges]
    if (len(set(boundary_indexes)) != len(boundary_indexes) or boundary_indexes[-1] >= len(rises)
            or boundary_indexes[0] >= spec.slot_bits or len(rises)-1-boundary_indexes[-1] >= spec.slot_bits):
        raise ValueError('I2S_UNRESOLVED_OR_MISSING_WS_BOUNDARY')

    words = []
    for edge, start, stop in zip(ws_edges, boundary_indexes, boundary_indexes[1:]):
        if stop-start != spec.slot_bits:
            raise ValueError('I2S_SLOT_CLOCK_COUNT_MISMATCH')
        bits = data_bits[start+1:stop+1]
        value = sum(bit << (spec.word_bits-1-index) for index, bit in enumerate(bits[:spec.word_bits]))
        if bits[0]:
            value -= 1 << spec.word_bits
        words.append(dict(channel='right' if edge.level else 'left', value=value,
                          ws_latched_at_rise=start, msb_at_rise=start+1, slot_end_at_rise=stop,
                          padding_bits=bits[spec.word_bits:]))

    centers = [(edge.start + edge.end)/2 for edge in rises]
    timings = dict(
        clock_period=[b-a for a,b in zip(centers,centers[1:])],
        clock_high=[b.start-a.end for a,b in zip(clock_edges,clock_edges[1:]) if a.level],
        clock_low=[b.start-a.end for a,b in zip(clock_edges,clock_edges[1:]) if not a.level],
        data_setup=data_setup, data_hold=data_hold, ws_setup=ws_setup, ws_hold=ws_hold,
        rise=[edge.end-edge.start for edge in clock_edges+data_edges+ws_edges if edge.level],
        fall=[edge.end-edge.start for edge in clock_edges+data_edges+ws_edges if not edge.level],
    )
    checks = []
    for key, limit in spec.timing.model_dump(exclude_none=True).items():
        op, metric = key.removesuffix('_s').split('_',1)
        values = timings[metric]
        observed = (max(values) if op=='maximum' else min(values)) if values else None
        passed = observed is not None and (observed <= limit if op=='maximum' else observed >= limit)
        checks.append(dict(metric=key, observed_s=observed, limit_s=limit, passed=passed))
    decoded = [(row['channel'], row['value']) for row in words]
    expected = [(row.channel, row.value) for row in spec.expected]
    padding_ok = spec.padding=='ignore' or all(not any(row['padding_bits']) for row in words)
    frame_rates = [1/(centers[z]-centers[a]) for a,z in zip(boundary_indexes,boundary_indexes[2:])]
    return dict(passed=decoded==expected and padding_ok and all(row['passed'] for row in checks),
                words=words, expected_words=[row.model_dump() for row in spec.expected], padding_ok=padding_ok,
                word_bits=spec.word_bits, slot_bits=spec.slot_bits, maximum_sample_interval_s=max_gap,
                timing_checks=checks, timing_ranges_s={key:[min(values),max(values)] for key,values in timings.items() if values},
                stereo_frame_rate_hz=[min(frame_rates),max(frame_rates)] if frame_rates else None,
                low_v=spec.low_v, high_v=spec.high_v)
