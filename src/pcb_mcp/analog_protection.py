"""Trace a real external input through an ADG4612 channel and series limiter.

Source-supported static isolation and resistor-limited DC reference are separate
from ramp dynamics, floating rail voltage and total buffer accuracy.
"""
import math
from . import parts


def analyze(s, amplifier):
    comps=s['components'];catalog={p['candidate_id']:p for p in parts.catalog()}
    a={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    req={q['key']:q['value'] for q in s.get('requirements',[])};findings=[]
    result=dict(mode='unsupported',findings=findings,power_off_topology_supported=False,
                whole_board_usb_backfeed_qualified=False,physical_verified=False)
    def add(code,msg):findings.append(dict(code=code,severity='error',message=msg,entity_ids=[amplifier['id']]))
    def net(c,num):return next((a.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==num),None)
    def bound(c):
        p=s.get('part_bindings',{}).get(c['id'])
        return p if p and catalog.get(p['candidate_id'])==p and p['evidence_status']=='available' and parts.binding_valid(s,c,p) else None
    def members(nid):return {p['component_id'] for p in s['nets'][nid]['pins']} if nid in s['nets'] else set()
    def nc(c,num):
        pin=next(p for p in c['pins'] if p['number']==num)
        return net(c,num) is None and s.get('pin_intents',{}).get(c['id']+':'+pin['pin_id'],{}).get('intent')=='no_connect'
    ap=bound(amplifier)
    port=next((c for c in comps.values() if c['ref']==req.get('analog_input_connector')),None)
    if not ap or not port or not bound(port) or port['library_id']!='Connector_Generic:Conn_01x02':
        add('ANALOG_INPUT_SOURCE_REQUIRED','输入端口和运放需要当前有效的来源/封装。');return result
    t=ap['facts']['terminal_numbers'];ip=net(amplifier,t['noninverting']);v=net(amplifier,t['positive_supply']);g=net(amplifier,t['negative_supply']);external=net(port,'1')
    result.update(input_connector=port['ref'],external_net=external,input_component_ids=[port['id']])
    if None in [ip,v,g,external] or net(port,'2')!=g:
        add('ANALOG_PORT_MAPPING','外接输入必须有独立信号和同一返回地。');return result
    if external==ip:
        result.update(mode='direct',path=[port['ref'],amplifier['ref']+'.'+t['noninverting'],'input_clamp','positive_supply'])
        return result
    chains=[]
    for r in comps.values():
        rp=bound(r)
        if r['library_id']!='Device:R' or not rp:continue
        ns={net(r,'1'),net(r,'2')}
        if ip not in ns or None in ns or len(ns)!=2:continue
        middle=next(iter(ns-{ip}))
        for sw in comps.values():
            p=bound(sw)
            if not p or p['facts'].get('circuit_model')!='power_off_protected_spst':continue
            for channel in p['facts']['channels']:
                if net(sw,channel['source'])==external and net(sw,channel['drain'])==middle:
                    chains.append((r,rp,middle,sw,p,channel))
    if len(chains)!=1:
        add('ANALOG_PROTECTION_PATH_REQUIRED','间接输入需唯一来源支持的S端→D端隔离开关→串联限流电阻→运放通路；普通电阻或改网络名不能替代隔离。');return result
    r,rp,middle,sw,p,ch=chains[0];f=p['facts']
    result.update(mode='protected',switch_reference=sw['ref'],switch_mpn=p['mpn'],series_resistor=r['ref'],
                  input_component_ids=[r['id']],path=[port['ref'],sw['ref']+'.'+ch['source'],sw['ref']+'.'+ch['drain'],r['ref'],amplifier['ref']+'.'+t['noninverting']],sources=p['sources'])
    if (len({external,middle,ip,v,g})!=5 or members(external)!={port['id'],sw['id']} or
            members(middle)!={sw['id'],r['id']} or members(ip)!={r['id'],amplifier['id']}):
        add('ANALOG_PROTECTION_BYPASS','保护路径节点重合、被短接或存在未建模旁路/额外负载。')
    if net(sw,f['positive_supply_pin'])!=v or net(sw,f['negative_supply_pin'])!=g or net(sw,f['ground_pin'])!=g:
        add('ANALOG_PROTECTION_SUPPLY','隔离开关VDD必须随运放供电；VSS和GND需实际接返回地。')
    if net(sw,ch['control'])!=v:add('ANALOG_PROTECTION_ENABLE','受用通道的有效高控制脚必须接同一供电，不能悬空、接地或外部驱动。')
    if not nc(sw,f['nc_pin']):add('ANALOG_PROTECTION_NC','厂家NC脚必须断开并记录意图。')
    for unused in f['channels']:
        if unused==ch:continue
        if any(net(sw,unused[key])!=g for key in ['source','drain','control']):
            add('ANALOG_PROTECTION_UNUSED_CHANNEL','当前单通道模型要求未用通道S/D/控制脚全部接地；不能忽略其外部驱动。')
    ctx=s.get('power_envelope',{});lo=req.get('analog_input_min_v');hi=req.get('analog_input_max_v');rs=req.get('analog_source_max_ohm')
    if (not ctx or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in [lo,hi,rs]) or
            ctx.get('ambient_c')!=25. or ctx.get('min_v',0)<4.5 or ctx.get('max_v',100)>5.5 or not 0<=lo<hi<=ctx.get('min_v',0)):
        add('ANALOG_PROTECTION_REFERENCE_SCOPE','参考仅覆盖25°C、4.5–5.5V单电源及非负且不超过最低电源的输入；不得外推双电源标题参数。');return result
    rf=rp['facts'];span=rf['temperature_range_c'];drift=rf['temperature_coefficient_ppm_per_k']*1e-6*(span[1]-span[0]);rmin=rf['value_ohm']*(1-rf['tolerance_fraction'])*(1-drift);rmax=rf['value_ohm']*(1+rf['tolerance_fraction'])*(1+drift)
    limit=ctx['max_v']/rmin if rmin>0 else math.inf
    # TLV9001 datasheet7.1 note2: beyond rails0.5V, limit input current to10mA.
    if limit>.01 or rmax>100000.:
        add('ANALOG_PROTECTION_LIMITER','串联电阻未覆盖10mA输入钳位限流要求，或超过100kΩ有限源阻抗参考范围。')
    bypass=[c for c in comps.values() if c['library_id']=='Device:C' and bound(c) and {net(c,'1'),net(c,'2')}=={v,g} and bound(c)['facts']['capacitance_f']>=100e-9]
    if not bypass:add('ANALOG_PROTECTION_BYPASS_REQUIRED','隔离开关需要来源明确的电源对地旁路；实际距离由PCB检查验证。')
    result.update(power_off_topology_supported=not findings,
                  series_resistance_reference_ohm=[rmin,rmax],
                  nonnegative_dc_series_current_reference_a=limit,
                  current_reference_condition='quasistatic voltage difference <= declared supply max; no ESD/negative transient claim',
                  amplifier_input_clamp_absolute_limit_a=.01,
                  static_off_mode=dict(vdd='0V or floating; isolation guaranteed at <=1V per Table11',vss='0V',ground='connected',
                                       blocking_voltage_range_v=f['isolation_input_range_v'],
                                       leakage_reference_max_a=f['off_leakage_reference_max_a_no_power'],
                                       leakage_test_condition=f['off_leakage_test_condition'],
                                       actual_leakage_upper_bound_over_entire_input_range_a=None),
                  added_error_reference=dict(switch_leakage_at5p5v_v=f['on_leakage_reference_max_a_at5p5v']*(rs+f['on_resistance_reference_max_ohm_at4p5v']),
                                             opamp_bias_typical_series_drop_v=ap['facts']['input_bias_typical_a_at5v']*rmax,
                                             kind='datasheet_condition_reference_only_not_total_error_bound'),
                  brownout_and_hotplug_qualified=False,guaranteed_total_tracking_error_v=None)
    return result
