"""Bounded-memory validation of large real/forward LTspice binary waveforms.

Chunks must be consumed to exhaustion before drawing a pass conclusion. No
decimation is used for validation; plotting can sample separately afterwards.
"""
from pathlib import Path
import os
import re
import numpy as np


def raw_chunks(path, chunk_points=262144):
    if not isinstance(chunk_points, int) or chunk_points < 1:
        raise ValueError('RAW_INVALID_CHUNK_SIZE')
    with Path(path).open('rb') as stream:
        before = os.fstat(stream.fileno())
        prefix = stream.read(1024 * 1024)
        encoding = 'utf-16-le' if prefix.startswith(b'T\0') else 'utf-8'
        marker = 'Binary:\n'.encode(encoding)
        offset = prefix.find(marker)
        if offset < 0:
            raise ValueError('RAW_BINARY_HEADER_MISSING_OR_TOO_LARGE')
        header = prefix[:offset].decode(encoding)
        meta = dict(line.split(':', 1) for line in header.splitlines() if ':' in line)
        if meta.get('Plotname', '').strip() != 'Transient Analysis':
            raise ValueError('RAW_NOT_TRANSIENT')
        if set(meta.get('Flags', '').split()) != {'real', 'forward'}:
            raise ValueError('RAW_UNSUPPORTED_FLAGS')
        n, count = int(meta['No. Variables']), int(meta['No. Points'])
        variables = re.findall(r'^\s*(\d+)\s+(\S+)\s+(\S+)\s*$', header.split('Variables:\n', 1)[1], re.M)
        if n < 2 or count < 2 or [int(v[0]) for v in variables] != list(range(n)):
            raise ValueError('RAW_INVALID_VARIABLES')
        names = [v[1].lower() for v in variables]
        if len(set(names)) != n or names[0] != 'time':
            raise ValueError('RAW_INVALID_NAMES')
        dtype = np.dtype([('time', '<f8'), ('values', '<f4', (n - 1,))])
        start = offset + len(marker)
        if before.st_size - start != count * dtype.itemsize:
            raise ValueError('RAW_TRUNCATED_OR_EXTRA_DATA')
        stream.seek(start)
        previous = -np.inf
        for index in range(0, count, chunk_points):
            size = min(chunk_points, count - index) * dtype.itemsize
            block = stream.read(size)
            if len(block) != size:
                raise ValueError('RAW_TRUNCATED_OR_EXTRA_DATA')
            rows = np.frombuffer(block, dtype=dtype)
            t, values = rows['time'], rows['values']
            if (not np.isfinite(t).all() or not np.isfinite(values).all()
                    or t[0] < 0 or t[0] <= previous or not np.all(np.diff(t) > 0)):
                raise ValueError('RAW_NONFINITE_OR_TIME_ORDER')
            previous = float(t[-1])
            yield dict(zip(names, [t] + [values[:, i] for i in range(n - 1)]))
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('RAW_CHANGED_DURING_READ')


def power_chain_metrics(chunks, *, stop_s=.035, steady_s=.020,
                        rail_min_v=3., rail_max_v=3.6, hold_s=50e-6,
                        en_low_v=.6, input_max_a=1.5, max_gap_s=20e-6):
    """Check the declared mainboard power fixture, retaining every extreme.

These parameters reproduce the existing fixture checks, not a new chip spec.
Reset hold applies after the LAST out-of-range rail sample, so later brownouts
cannot disappear from the result. Convergence quality is evaluated separately.
    """
    first = previous = stable_after = None
    count = 0
    low, high, input_peak, en_max, en_hold_max = np.inf, -np.inf, -np.inf, -np.inf, -np.inf
    gap = 0.
    first_en_above = None
    for w in chunks:
        t = np.asarray(w['time'])
        if not len(t):
            raise ValueError('EMPTY_WAVEFORM_CHUNK')
        if not all(len(a) == len(t) and np.isfinite(a).all() for a in w.values()):
            raise ValueError('NONFINITE_OR_MISMATCHED_WAVEFORM')
        if not np.all(np.diff(t) > 0) or (previous is not None and t[0] <= previous):
            raise ValueError('INCOMPLETE_OR_INVALID_WINDOW')
        if first is None:
            first = float(t[0])
            stable_after = first
        if previous is not None:
            gap = max(gap, float(t[0] - previous))
        if len(t) > 1:
            gap = max(gap, float(np.max(np.diff(t))))
        previous = float(t[-1]); count += len(t)
        v, en, current = np.asarray(w['v(v3v3)']), np.asarray(w['v(mcu_en)']), -np.asarray(w['i(vraw)'])
        steady = t >= steady_s
        if steady.any():
            low = min(low, float(v[steady].min())); high = max(high, float(v[steady].max()))
        input_peak = max(input_peak, float(current.max()))
        crossed = np.flatnonzero(en >= en_low_v)
        if first_en_above is None and len(crossed):
            first_en_above = float(t[crossed[0]])
        bad = np.flatnonzero((v < rail_min_v) | (v > rail_max_v))
        if len(bad):
            last_bad = int(bad[-1])
            stable_after = float(t[last_bad + 1]) if last_bad + 1 < len(t) else None
            cutoff = last_bad if stable_after is None else int(np.searchsorted(t, stable_after + hold_s, side='right')) - 1
            en_hold_max = max(en_max, float(en[:cutoff + 1].max()))
        else:
            if stable_after is None:
                stable_after = float(t[0])
            held = en[t <= stable_after + hold_s]
            if len(held):
                en_hold_max = max(en_hold_max, float(held.max()))
        en_max = max(en_max, float(en.max()))
    if first != 0 or previous is None or previous < stop_s - 1e-10:
        raise ValueError('INCOMPLETE_OR_INVALID_WINDOW')
    if gap > max_gap_s * 1.01:
        raise ValueError('WAVEFORM_UNDERSAMPLED')
    held = stable_after is not None and stable_after + hold_s < previous and en_hold_max < en_low_v
    checks = dict(rail_in_range=low >= rail_min_v and high <= rail_max_v,
                  reset_held_low=held, input_current_in_range=input_peak <= input_max_a)
    return dict(verdict='scenario_checks_pass' if all(checks.values()) else 'scenario_checks_fail',
                scope='coupled_vendor_models_with_declared_CC_and_load_fixtures',
                checks=checks, samples_checked=count, start_s=first, stop_s=previous,
                rail_20_to_35ms_min_v=low, rail_20_to_35ms_max_v=high,
                stable_after_s=stable_after, en_max_before_stable_plus_50us_v=en_hold_max,
                first_en_at_or_above_0_6v_s=first_en_above,
                en_held_low_through_stable_plus_50us=held, input_peak_a=input_peak,
                maximum_sample_gap_s=gap, physical_verified=False, full_board_function_qualified=False)
