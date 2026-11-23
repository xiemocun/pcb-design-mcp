"""Source-bound power-only USB classification, separate from load qualification.

Type-C 2.5 section 4.6.2.1 permits a PSD 500 mA at default advertisement.
This is permission, not evidence that an arbitrary circuit meets that limit or
the USB 2.0 attach load. Client current declarations never fill physical bounds.
"""
import hashlib
import json
from . import parts, power_budget, analog_protection
from .electrical import number

SOURCES = [
    dict(title='USB Type-C Cable and Connector Specification Release 2.5',
         url='https://www.usb.org/document-library/usb-type-cr-cable-and-connector-specification-release-25',
         section='4.6.2.1, pp226–227: PSD default current and higher-current monitoring',
         local_snapshot_file='usb-type-c-2.5.pdf',
         pdf_sha256='6636cd61387a2f78b0fa96c8ea86ccc0f39ec59f98821cdb57b206d31445a328'),
    dict(title='Universal Serial Bus Specification Revision 2.0',
         url='https://www.usb.org/sites/default/files/usb_20_20250603.zip',
         section='7.2.4.1, printed p177: 10uF in parallel with 44ohm attach load',
         local_snapshot_file='usb20-core.pdf',
         pdf_sha256='d39698a33486c399124af92bd02e4f978fd9a836b5cf4e52e6e4633eb1d89f61'),
    dict(title='USB 2.0 Device Capacitance ECN',
         url='https://www.usb.org/sites/default/files/usb_20_20250603.zip',
         section='p2: exposed capacitance minimum1uF, maximum10uF; all VBUS states',
         local_snapshot_file='usb20-device-capacitance-ecn.pdf',
         pdf_sha256='a6a8213b57001e3397e9376a287c09c882d9870ac1ef465465104321385a24d1'),
]


def evidence():
    """Include actual snapshot status in the global rule identity, not just URLs."""
    rows = []
    for source in SOURCES:
        path = parts.ROOT / 'evidence-cache' / source['local_snapshot_file']
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append(dict(source, actual_sha256=actual,
                         available=actual == source['pdf_sha256']))
    return rows


def evidence_hash():
    return hashlib.sha256(json.dumps(evidence(), sort_keys=True).encode()).hexdigest()


def evaluate(s, usb):
    findings = []
    comps = s['components']
    assigned = {(p['component_id'], p['pin_id']): n['id']
                for n in s['nets'].values() for p in n['pins']}
    catalog = {p['candidate_id']: p for p in parts.catalog()}

    def add(code, message, **extra):
        findings.append(dict(code=code, severity='error', message=message, **extra))

    def bound(c):
        p = s.get('part_bindings', {}).get(c['id'])
        return p if (p and catalog.get(p['candidate_id']) == p and
                     p['evidence_status'] == 'available' and parts.binding_valid(s, c, p)) else None

    def node(c, number_):
        return next((assigned.get((c['id'], p['pin_id'])) for p in c['pins']
                     if p['number'] == number_), None)

    def named(name):
        pins = [p for p in usb['pins'] if p['name'] == name]
        ns = {assigned.get((usb['id'], p['pin_id'])) for p in pins}
        return next(iter(ns)) if len(ns) == 1 and None not in ns else None

    # NC means a real disconnected pin plus recorded intent; a label is not enough.
    data = [p for p in usb['pins'] if p['name'] not in {'VBUS', 'GND', 'SHIELD', 'CC1', 'CC2'}]
    nc = bool(data) and all(
        (usb['id'], p['pin_id']) not in assigned and
        s.get('pin_intents', {}).get(usb['id'] + ':' + p['pin_id'], {}).get('intent') == 'no_connect'
        for p in data)
    vbus, ground = named('VBUS'), named('GND')
    cc_nodes = [named('CC1'), named('CC2')]
    topology_ok = None not in [vbus, ground, *cc_nodes] and len({vbus, ground, *cc_nodes}) == 4
    rd = []
    for cc in cc_nodes:
        candidates = []
        for c in comps.values():
            p = bound(c)
            if c['library_id'] != 'Device:R' or not p or None in [cc, ground]:
                continue
            if {node(c, '1'), node(c, '2')} != {cc, ground}:
                continue
            f = p['facts']; resistance = f.get('value_ohm'); tol = f.get('tolerance_fraction')
            if resistance is None or tol is None or not (4080 <= resistance*(1-tol) and resistance*(1+tol) <= 6120):
                continue
            # No extra CC loads, including a second parallel Rd.
            cc_members = s['nets'][cc]['pins']
            if len(cc_members) == 2 and {x['component_id'] for x in cc_members} == {usb['id'], c['id']}:
                candidates.append(c['ref'])
        rd.append(candidates)
    role = bool(bound(usb) and nc and topology_ok and all(len(x) == 1 for x in rd) and rd[0] != rd[1])
    result = dict(role='power_only_sink_candidate' if role else 'unclassified',
                  role_verified_from_connected_pins=role, usb_data_and_sbu_disconnected=nc,
                  rd_references=rd, default_current_permission_a=.5 if role else None,
                  higher_current_permission_a=None, actual_current_upper_bound_a=None,
                  full_usb_qualified=False, findings=findings)
    if not role:
        add('USB_PSD_TOPOLOGY_UNSUPPORTED', '纯受电分类需要真实USB型号、独立来源Rd及全部数据/SBU脚明确断开；不能靠文字声明。')
        return result
    result['standard_sources_verified'] = all(x['available'] for x in evidence())
    if not result['standard_sources_verified']:
        result['default_current_permission_a'] = None
        add('USB_PSD_SOURCE_UNAVAILABLE', 'USB标准快照缺失或已改变；不能应用默认纯受电模型。')
    ctx = s.get('power_envelope', {})
    positive = ctx.get('positive_pin', {})
    sources = [c for c in comps.values() if c['library_id'] == usb['library_id']]
    if (len(sources) != 1 or positive.get('component_id') != usb['id'] or
            assigned.get((positive.get('component_id'), positive.get('pin_id'))) != vbus or
            ctx.get('ground_net_id') != ground):
        add('USB_PSD_SOURCE_BOUNDARY', '当前有限模型需要唯一USB入口，其供电声明必须对应真实VBUS/地。')
    budget = power_budget.evaluate(s, dict(findings=[]))
    reference = budget.get('declared_dc_reference_input_a')
    result['declared_dc_reference_a'] = reference
    result['budget_verdict'] = budget['budget_verdict']
    result['budget_findings'] = budget['findings']
    if budget['budget_verdict'] != 'conditional':
        add('USB_PSD_BUDGET_INCOMPLETE', '逐器件来源和供电脚预算未完整；不能跳过未知负载。')
    if reference is not None and reference > .5:
        add('USB_PSD_DEFAULT_CURRENT_EXCESS', '声明条件下的直流需求已超过纯受电默认500mA；提高available_current声明不能改变允许电流。')

    caps = []
    for c in comps.values():
        if c['library_id'] not in {'Device:C', 'Device:C_Polarized'}:
            continue
        p = bound(c); f = p['facts'] if p else {}
        value, tol = f.get('capacitance_f'), f.get('tolerance_fraction')
        direct = {node(c, '1'), node(c, '2')} == {vbus, ground}
        caps.append(dict(reference=c['ref'], direct_vbus=direct,
                         nominal_uf=number(c['value'])*1e6 if number(c['value']) is not None else None,
                         initial_tolerance_min_uf=value*(1-tol)*1e6 if value is not None and tol is not None else None,
                         initial_tolerance_max_uf=value*(1+tol)*1e6 if value is not None and tol is not None else None))
    direct = [c for c in caps if c['direct_vbus']]
    complete = all(c['initial_tolerance_min_uf'] is not None for c in direct)
    lo = sum(c['initial_tolerance_min_uf'] for c in direct) if complete else None
    hi = sum(c['initial_tolerance_max_uf'] for c in direct) if complete else None
    result['capacitance'] = dict(components=caps, direct_initial_min_uf=lo, direct_initial_max_uf=hi,
                                effective_exposed_min_uf=None, effective_exposed_max_uf=None,
                                required_exposed_range_uf=[1., 10.])
    if not complete:
        add('USB_PSD_CAP_SOURCE_REQUIRED', '入口电容缺少当前型号/容量/容差来源。')
    elif hi > 10.:
        add('USB_PSD_CAP_TOLERANCE_EXCESS', '入口容量加初始正容差超过10uF，不能只检查标称值。')
    if complete and lo < 1.:
        add('USB_PSD_DIRECT_CAP_MINIMUM', '直连入口容量连初始负容差下的1uF都不满足；间接电容不能未经分析补作拔插保持容量。')
    # These missing bounds remain errors; neither a budget nor a nominal BOM is a proof.
    add('USB_PSD_EFFECTIVE_CAPACITANCE_UNPROVEN', 'USB要求各供电状态下暴露容量1–10uF；初始容差未覆盖偏压、温度、老化及间接电容。')
    add('USB_PSD_ATTACH_LOAD_UNPROVEN', '尚无与USB2.0的10uF并联44ohm等效负载相符的上电包络；声明直流预算不能证明浪涌。')
    add('USB_PSD_CURRENT_BOUND_UNPROVEN', '500mA是标准允许值；实际整口最坏电流上界仍未建立。')
    result['attach_reference'] = dict(parallel_resistance_ohm=44., capacitance_max_uf=10.,
                                      actual_peak_current_a=None, qualified=False)

    # Trace indirect paths too: a series resistor or renamed net cannot evade this check.
    result['protected_analog_inputs'] = []
    for c in comps.values():
        p = bound(c)
        if not p or p['mpn'] != 'TLV9001IDCKR':
            continue
        f = p['facts']; terminals = f['terminal_numbers']
        if node(c, terminals['positive_supply']) != vbus:
            continue
        protection=analog_protection.analyze(s,c)
        if protection['mode']=='protected' and protection['power_off_topology_supported']:
            result['protected_analog_inputs'].append(protection)
        elif protection['mode']=='direct':
            add('USB_EXTERNAL_INPUT_BACKFEED_PATH',
                '外接信号直连TLV9001轨钳位输入，断电时缺少隔离/限流；源阻抗上界不是限流下界。',
                reference=c['ref'],terminal=terminals['noninverting'],external_connectors=[protection['input_connector']],
                path=[protection['input_connector'],c['ref']+'.'+terminals['noninverting'],'input_clamp','VBUS'],
                source=p['sources'],source_section='7.1 note2: rail clamps and10mA current limit',actual_injection_current_upper_bound_a=None)
        else:
            findings.extend(protection['findings'])
            add('USB_INPUT_PROTECTION_INVALID','没有建立来源支持的外接输入断电保护路径；改网络名或只串电阻不能绕过检查。')
    return result
