"""Decode sampled I²C at actual receiver terminals using two logic thresholds."""
from bisect import bisect_left, bisect_right

import numpy as np

from .measurements import samples
from .digital_waveforms import logic_edges as sampled_edges


def logic_edges(values, axis, low, high):
    return sampled_edges(values, axis, low, high, protocol='I2C', idle_high=True)[1]


def decode_frame(clocks, data, data_starts, data_ends, start, end, repeated, window_start, timings):
    if len(clocks) < 20 or len(clocks) % 2:
        raise ValueError('I2C_INCOMPLETE_CLOCK_GROUP')
    timings['start_hold'].append(clocks[0].start - start.end)
    timings['stop_setup' if end.level else 'repeated_start_setup'].append(end.start - clocks[-1].end)
    lows = [rise.start - fall.end for fall, rise in zip(clocks[::2], clocks[1::2])]
    timings['clock_low'].extend(lows)
    bits = []
    for i in range(1, len(clocks) - 1, 2):
        rise, fall, next_rise = clocks[i:i+3]
        last = bisect_right(data_ends, rise.start) - 1
        following = bisect_left(data_starts, fall.end)
        next_change = data[following].start if following < len(data) else next_rise.start
        timings['data_setup'].append(rise.start - (data[last].end if last >= 0 else window_start))
        timings['data_hold'].append(min(next_change, next_rise.start) - fall.end)
        timings['clock_high'].append(fall.start - rise.end)
        bits.append(data[last].level if last >= 0 else 1)
    if len(bits) % 9:
        raise ValueError('I2C_CLOCK_COUNT_NOT_WHOLE_BYTES_WITH_ACK')
    words = []
    for offset in range(0, len(bits), 9):
        value = sum(bit << (7 - j) for j, bit in enumerate(bits[offset:offset+8]))
        words.append(dict(value=value, ack=bits[offset+8] == 0))
    address, *payload = words
    frame = dict(start='repeated_start' if repeated else 'start', address=address['value'] >> 1,
                 direction='read' if address['value'] & 1 else 'write', address_ack=address['ack'], data=payload)
    return frame


def measure(sda, scl, time, window, spec):
    time = np.asarray(time)
    sda, axis = samples(sda, time, window)
    scl, _ = samples(scl, time, window)
    begin, end = window
    gaps = np.diff(time)[(time[:-1] < end) & (time[1:] > begin)]
    max_gap = float(np.max(gaps))
    if max_gap > spec.maximum_sample_interval_s * (1 + 1e-8):
        raise ValueError('I2C_TRACE_TOO_COARSE')

    data = logic_edges(sda, axis, spec.low_v, spec.high_v)
    clock = logic_edges(scl, axis, spec.low_v, spec.high_v)
    clock_ends = [edge.end for edge in clock]
    data_starts = [edge.start for edge in data]
    data_ends = [edge.end for edge in data]
    events = []
    for edge in data:
        preceding = bisect_right(clock_ends, edge.start)
        if preceding < len(clock) and clock[preceding].start < edge.end:
            raise ValueError('I2C_DATA_TRANSITION_OVERLAPS_CLOCK_EDGE')
        if preceding == 0 or clock[preceding-1].level == 1:
            events.append((edge, preceding))

    timings = {key: [] for key in ('clock_high', 'clock_low', 'data_setup', 'data_hold',
                                  'start_hold', 'repeated_start_setup', 'stop_setup', 'bus_free', 'rise', 'fall')}
    for edge in clock + data:
        timings['rise' if edge.level else 'fall'].append(edge.end - edge.start)
    frames, boundaries = [], []
    start = None
    idle_since, idle_index = begin, 0
    repeated = False
    for edge, index in events:
        if start is None:
            if edge.level or index != idle_index:
                raise ValueError('I2C_UNEXPECTED_BUS_ACTIVITY_WHILE_IDLE')
            timings['bus_free'].append(edge.start - idle_since)
        else:
            frames.append(decode_frame(clock[start_index:index], data, data_starts, data_ends,
                                       start, edge, repeated, begin, timings))
            boundaries.append(dict(start_s=start.start, end_s=edge.end, end='stop' if edge.level else 'repeated_start'))
        if edge.level:
            start = None
            idle_since, idle_index = edge.end, index
        else:
            repeated = start is not None
            start, start_index = edge, index
    if start is not None or idle_index != len(clock) or not frames:
        raise ValueError('I2C_COMPLETE_STOP_AND_IDLE_REQUIRED')

    limits = spec.timing.model_dump(exclude_none=True)
    checks = []
    for key, limit in limits.items():
        op, metric = key.removesuffix('_s').split('_', 1)
        longest = metric == 'longest_clock_low'
        values = timings['clock_low' if longest else metric]
        observed = (max(values) if longest or op == 'maximum' else min(values)) if values else None
        passed = observed is not None and (observed <= limit if op == 'maximum' else observed >= limit)
        checks.append(dict(metric=key, observed_s=observed, limit_s=limit, passed=passed))
    expected = [frame.model_dump() for frame in spec.expected]
    return dict(passed=frames == expected and all(check['passed'] for check in checks), frames=frames,
                expected_frames=expected, boundaries=boundaries, timing_checks=checks,
                timing_ranges_s={key: [min(values), max(values)] for key, values in timings.items() if values},
                maximum_sample_interval_s=max_gap, low_v=spec.low_v, high_v=spec.high_v)
