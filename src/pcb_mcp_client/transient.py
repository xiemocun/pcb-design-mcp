"""Read-only circuit/simulation evidence helpers, separate from manufacturing approval.

Supports real, forward LTspice transient RAW files only. This module neither
executes model text nor treats a typical macro-model as a silicon guarantee.
"""
from array import array
from bisect import bisect_left, bisect_right
import hashlib
import math
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def verified_circuit(state, xml_path):
    """Require independently exported KiCad connectivity and values to match state."""
    root = ET.parse(xml_path).getroot()
    components = {c['ref']: c for c in state['components'].values()}
    actual = {c.get('ref'): c.findtext('value') for c in root.findall('components/comp')}
    if actual != {ref: c['value'] for ref, c in components.items()}:
        raise ValueError('NETLIST_COMPONENT_MISMATCH')
    expected, seen = {}, set()
    for net in state['nets'].values():
        endpoints = set()
        for endpoint in net['pins']:
            c = state['components'][endpoint['component_id']]
            pin = next(p for p in c['pins'] if p['pin_id'] == endpoint['pin_id'])
            for number in pin['physical_numbers']:
                key = (c['ref'], number)
                if key in seen:
                    raise ValueError('DUPLICATE_PIN')
                seen.add(key)
                endpoints.add(key)
        expected[net['name']] = endpoints
    nets = {}
    for net in root.findall('nets/net'):
        name = net.get('name').removeprefix('/')
        if name in nets:
            raise ValueError('DUPLICATE_NET')
        nets[name] = {(n.get('ref'), n.get('pin')) for n in net.findall('node')}
    # KiCad exports an isolated net for an explicitly unconnected physical pin.
    # Require both the recorded intent and isolation; a name prefix alone is not evidence.
    disconnected = set()
    for intent in state.get('pin_intents', {}).values():
        if intent['intent'] != 'no_connect':
            continue
        endpoint = intent['pin']
        component = state['components'][endpoint['component_id']]
        pin = next(p for p in component['pins'] if p['pin_id'] == endpoint['pin_id'])
        disconnected.update((component['ref'], number) for number in pin['physical_numbers'])
    if disconnected & seen:
        raise ValueError('NO_CONNECT_PIN_CONNECTED')
    unmatched = {}
    actual_disconnected = set()
    for name, endpoints in nets.items():
        if name not in expected and len(endpoints) == 1 and endpoints <= disconnected:
            if actual_disconnected & endpoints:
                raise ValueError('DUPLICATE_PIN')
            actual_disconnected.update(endpoints)
        else:
            unmatched[name] = endpoints
    if actual_disconnected != disconnected:
        raise ValueError('NO_CONNECT_NETLIST_MISMATCH')
    nets = unmatched
    if nets != expected:
        raise ValueError('NETLIST_CONNECTION_MISMATCH')
    return components, {pin: name for name, pins in nets.items() for pin in pins}


def symbol_order(asy_path):
    """Read original vendor symbol pin names in SpiceOrder, never guess from MPN."""
    pins = []
    for block in re.split(r'^PIN ', Path(asy_path).read_text(), flags=re.M)[1:]:
        name = re.search(r'^PINATTR PinName (.+)$', block, re.M)
        order = re.search(r'^PINATTR SpiceOrder (\d+)$', block, re.M)
        if not name or not order:
            raise ValueError('MODEL_PIN_ORDER_MISSING')
        pins.append((int(order[1]), name[1]))
    pins.sort()
    if [n for n, _ in pins] != list(range(1, len(pins) + 1)) or not pins:
        raise ValueError('MODEL_PIN_ORDER_INVALID')
    return [name for _, name in pins]


def read_raw(path):
    """Strict binary reader; validate shape, timestamps and finite values before use."""
    data = Path(path).read_bytes()
    encoding = 'utf-16-le' if data.startswith(b'T\0') else 'utf-8'
    marker = 'Binary:\n'.encode(encoding)
    offset = data.find(marker)
    if offset < 0:
        raise ValueError('RAW_BINARY_HEADER_MISSING')
    header = data[:offset].decode(encoding)
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
    record = struct.Struct('<d' + 'f' * (n - 1))
    payload = memoryview(data)[offset + len(marker):]
    if len(payload) != count * record.size:
        raise ValueError('RAW_TRUNCATED_OR_EXTRA_DATA')
    columns = [array('d') for _ in names]
    previous = -math.inf
    for row in record.iter_unpack(payload):
        if not all(math.isfinite(v) for v in row) or row[0] <= previous or row[0] < 0:
            raise ValueError('RAW_NONFINITE_OR_TIME_ORDER')
        previous = row[0]
        for column, value in zip(columns, row):
            column.append(value)
    return dict(zip(names, columns))


def pulse_metrics(wave, requirement, supply_v, output='v(pulse_out)', max_step_s=0.0001):
    """Every complete cycle in the observation window; preserve failed measurements.

    Period/duty use supply/2 crossings. Levels use the central 50% of each high
    and low plateau, excluding unavoidable transition edges. Edge times are
    reported separately; no undeclared edge-speed limit is invented.
    """
    t, v = wave['time'], wave[output]
    start = requirement['operation']['steady_state_after_power_on_s']
    end = start + requirement['operation']['continuous_observation_s']
    if t[0] > 0 or t[-1] < end:
        raise ValueError('OBSERVATION_WINDOW_INCOMPLETE')
    if len(t) != len(v) or not all(math.isfinite(x) for x in v):
        raise ValueError('WAVEFORM_INVALID')
    if any(b <= a for a, b in zip(t, t[1:])):
        raise ValueError('WAVEFORM_TIME_ORDER')
    if max(b - a for a, b in zip(t, t[1:])) > max_step_s * 1.01:
        raise ValueError('WAVEFORM_UNDERSAMPLED')
    def crossings(level, rising):
        result = []
        for i in range(1, len(t)):
            hit = v[i-1] < level <= v[i] if rising else v[i-1] > level >= v[i]
            if hit:
                result.append(t[i-1] + (t[i]-t[i-1]) * (level-v[i-1]) / (v[i]-v[i-1]))
        return result
    rising, falling = crossings(supply_v / 2, True), crossings(supply_v / 2, False)
    lo, hi = requirement['output']['low_max_v'], requirement['output']['high_min_v']
    edge_lo_up, edge_hi_up = crossings(lo, True), crossings(hi, True)
    edge_hi_down, edge_lo_down = crossings(hi, False), crossings(lo, False)
    cycles, startup = [], None
    for a, b in zip(rising, rising[1:]):
        # Include cycles intersecting both window edges; simulations must have guard time.
        falls = falling[bisect_right(falling, a):bisect_left(falling, b)]
        if len(falls) != 1:
            raise ValueError('PULSE_SEQUENCE_INVALID')
        f = falls[0]
        def samples(x, y):
            values = v[bisect_left(t, x):bisect_right(t, y)]
            if not values:
                raise ValueError('PLATEAU_UNSAMPLED')
            return values
        high = min(samples(a + .25*(f-a), a + .75*(f-a)))
        low = max(samples(f + .25*(b-f), f + .75*(b-f)))
        def edge_width(first, second, at):
            i, j = bisect_right(first, at)-1, bisect_left(second, at)
            if i < 0 or j == len(second):
                return None
            dt = second[j] - first[i]
            return dt if 0 <= dt < b-a else None
        c = dict(start_s=a, end_s=b, frequency_hz=1/(b-a), duty=(f-a)/(b-a),
                 high_v=high, low_v=low, rise_s=edge_width(edge_lo_up, edge_hi_up, a),
                 fall_s=edge_width(edge_hi_down, edge_lo_down, f))
        q = requirement['output']
        c['pass'] = (q['frequency_min_hz'] <= c['frequency_hz'] <= q['frequency_max_hz']
                     and q['duty_min'] <= c['duty'] <= q['duty_max']
                     and high >= hi and low <= lo and c['rise_s'] is not None and c['fall_s'] is not None)
        if startup is None and c['pass']:
            startup = b
        if b > start and a < end:
            cycles.append(c)
    covered = bool(cycles and cycles[0]['start_s'] <= start and cycles[-1]['end_s'] >= end)
    ok = covered and startup is not None and startup <= start and all(c['pass'] for c in cycles)
    result = dict(verdict='pass' if ok else 'fail', evidence='vendor_macro_model_simulation',
                  physical_verified=False, silicon_corner_guarantee=False,
                  observation_start_s=start, observation_end_s=end, continuous_coverage=covered,
                  first_valid_cycle_end_s=startup, cycles_checked=len(cycles),
                  failed_cycles=[c for c in cycles if not c['pass']][:20],
                  level_method='central_50_percent_of_each_plateau', threshold_v=supply_v/2)
    for key in ('frequency_hz', 'duty', 'high_v', 'low_v', 'rise_s', 'fall_s'):
        values = [c[key] for c in cycles if c[key] is not None]
        result[key] = {'min': min(values), 'max': max(values)} if values else None
    return result
