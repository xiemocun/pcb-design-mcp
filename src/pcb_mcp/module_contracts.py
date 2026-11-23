"""Finite source-linked port contracts, not arbitrary expressions or full sign-off.

AM2302 to ESP32: source-linked split receiver/driver roles and legacy TXS topology.
Static checks do not establish protocol, loaded logic levels or sequencing.
"""
import hashlib,json
from pathlib import Path
from . import parts
from .signal_roles import definitions as signal_definitions

ESP='RF_Module:ESP32-WROOM-32E'
SENSOR='PCB_MCP:AM2302_SIP4'
TXS='Logic_LevelTranslator:TXS0102DCT'


def graph(s):
    assignment={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    def net(c,name):
        return next((assignment.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
    return assignment,net


def legacy_bridging(s,sensor,mcu):
    _,net=graph(s)
    return [c for c in s['components'].values() if c['library_id']==TXS
            and net(c,'A1')==net(mcu,'IO27') and net(c,'B1')==net(sensor,'SDA')
            and net(c,'A1') is not None and net(c,'B1') is not None
            and net(c,'A1')!=net(c,'B1')
            and net(c,'VCCA')==net(mcu,'VDD') and net(c,'VCCB')==net(sensor,'VDD')
            and net(c,'VCCA')!=net(c,'VCCB')
            and net(c,'GND')==net(mcu,'GND')==net(sensor,'GND')]



def split_bridge(s,sensor,mcu):
    from .signal_roles import roles,numbered_net
    assigned,net=graph(s);available=roles(s);out=[]
    safe={'IO4','IO16','IO17','IO18','IO19','IO21','IO22','IO23','IO25','IO26','IO27','IO32','IO33'}
    for rx,rf in available:
        if rf['signal_role']!='schmitt_receiver':continue
        for tx,tf in available:
            if tf['signal_role']!='open_drain_driver':continue
            at=lambda c,f,key:numbered_net(assigned,c,f[key])
            bus=net(sensor,'SDA');read=at(rx,rf,'output_pin_number');drive=at(tx,tf,'input_pin_number')
            rp=[p for p in mcu['pins'] if p['name'] in safe and assigned.get((mcu['id'],p['pin_id']))==read]
            dp=[p for p in mcu['pins'] if p['name'] in safe and assigned.get((mcu['id'],p['pin_id']))==drive]
            if bus and at(rx,rf,'input_pin_number')==bus==at(tx,tf,'output_pin_number') and read and drive and len({bus,read,drive})==3 and len(rp)==len(dp)==1:
                if all(at(c,f,'supply_pin_number')==net(mcu,'VDD') and at(c,f,'ground_pin_number')==net(mcu,'GND')==net(sensor,'GND') for c,f in [(rx,rf),(tx,tf)]):
                    out.append(dict(receiver=rx,driver=tx,receiver_facts=rf,driver_facts=tf,read_gpio=rp[0]['name'],drive_gpio=dp[0]['name'],bus=bus,read=read,drive=drive))
    return out

def bridging(s,sensor,mcu):
    return legacy_bridging(s,sensor,mcu)+[x['receiver'] for x in split_bridge(s,sensor,mcu)]


def static_findings(s):
    from .electrical import number
    findings=[];assignment,net=graph(s);comps=list(s['components'].values())
    translators=[c for c in comps if c['library_id']==TXS]
    sources={p['symbol_id']:p for p in parts.reference_catalog()}
    def add(code,message,c,severity='error'):
        findings.append(dict(code=code,severity=severity,message=message,entity_ids=[c['id']]))
    def passive(lib,a,b,value):
        return a is not None and b is not None and a!=b and any(c['library_id']==lib
            and {assignment.get((c['id'],p['pin_id'])) for p in c['pins']}=={a,b}
            and number(c['value']) is not None and abs(number(c['value'])-value)<=value*.001 for c in comps)
    for c in translators:
        record=sources[TXS]
        if record['evidence_status']!='available':add('MODULE_SOURCE_CHANGED','TXS0102 厂家资料快照缺失或变化。',c)
        ports=[net(c,n) for n in ('VCCA','VCCB','GND','A1','B1','OE')]
        if None in ports or len(set(ports))!=6:add('MODULE_PORT_SHORT','两侧电源、参考地、两侧数据和 OE 必须是六个明确且不同的网络。',c)
        expected=record['facts']['pin_mapping']
        if {p['number']:p['name'] for p in c['pins']}!=expected:add('MODULE_PIN_SOURCE_CONFLICT','TXS0102 DCT 引脚表与厂家资料不一致。',c)
        host=[x for x in comps if x['library_id']==ESP]
        sensor=[x for x in comps if x['library_id']==SENSOR]
        if len(host)!=1 or len(sensor)!=1 or bridging(s,sensor[0],host[0])!=[c]:
            add('MODULE_TRANSLATION_TOPOLOGY','本模块要求一个 ESP32 IO27→A1、B1→AM2302 SDA；低压侧接 MCU 电源，高压侧接传感器电源，三者共地。',c)
        else:
            if net(c,'OE')!=net(host[0],'IO26') or net(c,'OE') in {None,net(c,'VCCA'),net(c,'VCCB'),net(c,'A1'),net(c,'B1'),net(c,'GND')}:
                add('MODULE_OE_DRIVER','OE 必须由独立的 ESP32 IO26 控制，不可直接接高压电源或数据线。',c)
            if not passive('Device:R',net(c,'B1'),net(c,'VCCB'),10000):
                add('MODULE_SENSOR_PULLUP','本受限参考模块要求 SDA 到高压侧有 10k 标称外部上拉；内部上拉及实际低电平裕量仍需验证。',c)
        if not passive('Device:R',net(c,'OE'),net(c,'GND'),100000):
            add('MODULE_OE_PULLDOWN','本参考模块要求 OE 有 100k 标称下拉；阻值为受限策略，非厂家通用保证。',c)
        idle=net(c,'A2')
        if idle is None or idle!=net(c,'B2') or not passive('Device:R',idle,net(c,'GND'),100):
            add('MODULE_UNUSED_CHANNEL','本参考模块的 A2/B2 必须同接独立闲置网络，并通过受管 100Ω 下拉接地；不允许悬空或接入工作电源/信号。',c)
        elif any(n['id']==idle and len(n['pins'])!=3 for n in s['nets'].values()):
            add('MODULE_UNUSED_CHANNEL','闲置网络只能包含 A2、B2 和下拉电阻，不得连接其他信号。',c)
        for name in ('VCCA','VCCB'):
            if not passive('Device:C',net(c,name),net(c,'GND'),1e-7):
                add('MODULE_BYPASS_MISSING',name+' 到地缺少本参考策略要求的 100nF 标称旁路电容。',c)
        for code,message in [
            ('MODULE_SEQUENCING_UNVERIFIED','固件必须在两侧电源稳定后才拉高 IO26/OE；没有验证实际启动时序。'),
            ('MODULE_LOADED_LOGIC_UNVERIFIED','尚未证明 AM2302 与 TXS0102 在实际负载下的高低电平裕量、线缆电容及协议时序。'),
            ('MODULE_UNUSED_PULLUP_LOAD','闲置 A2/B2 的下拉会消耗内部上拉电流；内部电阻仅有标称值，不应伪造最大负载上界。')]:add(code,message,c,'warning')
    if translators and any(c['library_id']==SENSOR for c in comps):
        add('MODULE_LOGIC_MARGIN_UNPROVEN','AM2302 VOL max 0.30V 超出 TXS0102 的 0.15V 输入低电平保证条件，旧接口不能作为已验证兼容方案。',translators[0])
    findings.extend(split_static_findings(s))
    return findings


def evaluate(s):
    from . import power_integrity
    findings=static_findings(s);_,net=graph(s);rows=[]
    translators=[c for c in s['components'].values() if c['library_id']==TXS]
    catalog={p['symbol_id']:p for p in parts.reference_catalog()};power=power_integrity.evaluate(s)
    def add(code,message,severity='error'):
        findings.append(dict(code=code,severity=severity,message=message))
    if not translators and not any(split_bridge(s,se,mc) for se in s['components'].values() if se['library_id']==SENSOR for mc in s['components'].values() if mc['library_id']==ESP):add('MODULE_PROFILE_UNSUPPORTED','此检查需要 ESP32/AM2302 的来源明确的独立接收/开漏发送路径，或旧 TXS0102 接口。')
    interval_complete=bool(s.get('power_envelope'))
    if any(f['code']=='SUPPLY_ENVELOPE_BOUNDARY_STALE' for f in power['findings']):
        add('MODULE_SUPPLY_BOUNDARY_STALE','外部电源范围声明已不再对应有效入口。');interval_complete=False
    if not interval_complete:add('MODULE_SUPPLY_ENVELOPE_MISSING','没有当前有效的外部电源范围；静态接法不等于最坏情况电压通过。','warning')
    for c in translators:
        f=catalog[TXS]['facts'];va=power['rails'].get(net(c,'VCCA'));vb=power['rails'].get(net(c,'VCCB'))
        row=dict(reference=c['ref'],vcca=va,vccb=vb,scope='recommended_supply_intervals_and_static_port_topology_only',physical_part_binding='bound' if c['id'] in s['part_bindings'] else 'unbound_not_procurement_ready')
        rows.append(row)
        if not va or not vb:add('MODULE_RAIL_UNKNOWN','转换器的一侧缺少可追溯电源。');continue
        if any(v.get('basis')=='reference_25C_DC_terms_only_not_ripple_or_transient_bound' for v in (va,vb)):
            add('MODULE_DC_ONLY_SUPPLY','开关电源范围仅为 25°C 参考 DC 误差筛选；纹波、瞬态和完整工作条件未被此模块检查覆盖。','warning')
        if va['min_v']<f['vcca_min_v'] or va['max_v']>f['vcca_max_v']:add('MODULE_VCCA_RANGE','低压侧超出 TXS0102 VCCA 推荐范围。')
        if vb['min_v']<f['vccb_min_v'] or vb['max_v']>f['vccb_max_v']:add('MODULE_VCCB_RANGE','高压侧超出 TXS0102 VCCB 推荐范围。')
        if va['max_v']>vb['min_v']:add('MODULE_RAIL_ORDER','最坏情况下 VCCA 上界高于 VCCB 下界。')
        sf=catalog[SENSOR]['facts']
        if vb['min_v']<sf['supply_min_v'] or vb['max_v']>sf['supply_max_v']:add('MODULE_SENSOR_SUPPLY_RANGE','传感器供电范围超出 AM2302 推荐范围。')
    verdict='fail' if any(f['severity']=='error' for f in findings) else ('pass' if interval_complete else 'incomplete')
    split_rows,split_findings=split_evaluate(s,power)
    rows.extend(split_rows);findings.extend(split_findings)
    used={ESP,SENSOR}|{c['library_id'] for c in s['components'].values() if c['library_id']==TXS or c['library_id'] in signal_definitions()}
    sources=[catalog[k] for k in sorted(used)]
    for source in sources:
        if source['evidence_status']!='available':
            add('MODULE_SOURCE_CHANGED',source['mpn']+' 厂家资料快照缺失或变化。')
    from . import backend
    current={c['candidate_id'] for c in backend.catalog()}
    if any(c['candidate_id'] not in current for c in s['components'].values()):
        add('LIBRARY_CHANGED','模块引用的符号库已变化。')
    verdict='fail' if any(f['severity']=='error' for f in findings) else ('pass' if interval_complete else 'incomplete')
    evidence=hashlib.sha256(Path(__file__).read_bytes()+json.dumps(sources,sort_keys=True).encode()).hexdigest()
    return dict(revision=s['revision'],verdict=verdict,modules=rows,findings=findings,
                sources=[dict(mpn=p['mpn'],definition_hash=p['definition_hash'],evidence_status=p['evidence_status'],sources=p['sources']) for p in sources],
                evidence_hash=evidence,whole_board_power_verdict=power['verdict'],
                coverage=[dict(check='source_pin_map_and_static_port_topology',status='checked' if rows else 'unsupported'),
                          dict(check='recommended_supply_intervals',status='checked' if interval_complete else 'incomplete'),
                          dict(check='loaded_logic_protocol_firmware_physical_part_and_assembly',status='not_qualified')],
                qualification='restricted_static_port_contract_not_functional_or_manufacturing_approval',manufacturing_approved=False)


def split_static_findings(s):
    from .signal_roles import roles,numbered_net
    from .electrical import number
    assigned,net=graph(s);findings=[];items=roles(s)
    if not items:return findings
    def add(code,msg):findings.append(dict(code=code,severity='error',message=msg))
    sensors=[c for c in s['components'].values() if c['library_id']==SENSOR];hosts=[c for c in s['components'].values() if c['library_id']==ESP]
    bridges=[b for se in sensors for mc in hosts for b in split_bridge(s,se,mc)]
    interface_context=bool(sensors or hosts)
    if interface_context and (len(bridges)!=len(sensors) or not bridges):add('MODULE_SPLIT_TOPOLOGY','每只传感器需要通过独立的耐压接收和开漏发送路径连接支持的 MCU GPIO。')
    selected={c['id'] for b in bridges for c in (b['receiver'],b['driver'])}
    for c,f in items:
        if interface_context and c['id'] not in selected:add('MODULE_SIGNAL_ROLE_UNCONNECTED',c['ref']+' 没有参与完整的数据接口。')
        binding=s.get('part_bindings',{}).get(c['id'])
        if not binding or binding['symbol_id']!=c['library_id']:add('MODULE_PART_REQUIRED',c['ref']+' 必须选择其来源记录中的实际型号。')
        # Pin number is the source role: standard logic symbol A/Y names may be blank.
        for key,expected in [('ground_pin_number','GND'),('supply_pin_number','VCC')]:
            p=next((p for p in c['pins'] if p['number']==f[key]),None)
            if not p or p['name']!=expected:add('MODULE_PIN_SOURCE_CONFLICT',c['ref']+' 电源引脚与厂家编号不符。')
        supply=numbered_net(assigned,c,f['supply_pin_number']);gnd=numbered_net(assigned,c,f['ground_pin_number'])
        caps=[p for p in s['components'].values() if p['library_id']=='Device:C' and {assigned.get((p['id'],pin['pin_id'])) for pin in p['pins']}=={supply,gnd} and (number(p['value']) or 0)>=1e-7*.999]
        if not caps:add('MODULE_BYPASS_MISSING',c['ref']+' 缺少真实同轨旁路电容。')
    for b in bridges:
        tx=b['driver'];pull=[c for c in s['components'].values() if c['library_id']=='Device:R' and {assigned.get((c['id'],p['pin_id'])) for p in c['pins']}=={b['drive'],net(hosts[0],'VDD')} and 4700<=(number(c['value']) or 0)<=10000]
        if not pull:add('MODULE_RELEASE_PULLUP_REQUIRED',tx['ref']+' 输入需要 4.7k–10k 上拉，使 MCU 复位时释放传感器总线。')
        sensor=next(c for c in sensors if net(c,'SDA')==b['bus'])
        pull=[c for c in s['components'].values() if c['library_id']=='Device:R' and {assigned.get((c['id'],p['pin_id'])) for p in c['pins']}=={b['bus'],net(sensor,'VDD')} and 4700<=(number(c['value']) or 0)<=10000]
        if not pull:add('MODULE_BUS_PULLUP_REQUIRED',sensor['ref']+' 总线需要 4.7k–10k 外部上拉；内部上拉不替代该设计要求。')
    return findings


def split_evaluate(s,power):
    assigned,net=graph(s);rows=[];findings=[];catalog={p['symbol_id']:p for p in parts.reference_catalog()}
    for se in s['components'].values():
        if se['library_id']!=SENSOR:continue
        for mc in s['components'].values():
            if mc['library_id']!=ESP:continue
            for b in split_bridge(s,se,mc):
                vs=power['rails'].get(net(se,'VDD'));vm=power['rails'].get(net(mc,'VDD'))
                row=dict(kind='split_open_drain',sensor=se['ref'],receiver=b['receiver']['ref'],driver=b['driver']['ref'],read_gpio=b['read_gpio'],drive_gpio=b['drive_gpio'],drive_release_level=1,voltage_reference=None,physical_qualified=False)
                rows.append(row)
                if not vs or not vm:findings.append(dict(code='MODULE_RAIL_UNKNOWN',severity='error',message='接口缺少当前供电范围。'));continue
                rf=b['receiver_facts'];tf=b['driver_facts']
                # Worst table envelope (not interpolation): includes all tabulated VCC points.
                margins=dict(sensor_low_to_receiver_v=min(rf['negative_threshold_min_v'])-.30,sensor_high_to_receiver_v=.9*vs['min_v']-max(rf['positive_threshold_max_v']),driver_low_to_sensor_v=.3*vs['min_v']-tf['output_low_max_v'],mcu_low_to_driver_v=tf['input_low_max_v']-.1*vm['max_v'],mcu_high_to_driver_v=.8*vm['min_v']-tf['input_high_min_v'],receiver_low_to_mcu_v=.25*vm['min_v']-rf['output_low_max_v'],receiver_high_to_mcu_v=.25*vm['min_v']-rf['output_high_drop_max_v'])
                row['voltage_reference']=dict(margins_v=margins,basis='manufacturer static table envelopes; ground offsets, actual edge/noise and physical waveforms excluded',output_load_limit_a=rf['output_logic_test_load_max_a'])
                for f in [rf,tf]:
                    if vm['min_v']<f['supply_min_v'] or vm['max_v']>f['supply_max_v'] or vs['max_v']>f.get('input_max_v',f.get('output_max_v',0)):
                        findings.append(dict(code='MODULE_BUFFER_RANGE',severity='error',message='接收/发送路径超出厂家电压范围。'))
                if vm['min_v']<tf['logic_supply_range_v'][0] or vm['max_v']>tf['logic_supply_range_v'][1]:findings.append(dict(code='MODULE_LOGIC_MODEL_RANGE',severity='error',message='发送缓冲器逻辑阈值模型仅适用 3.0–3.6V。'))
                if any(v<=0 for v in margins.values()):findings.append(dict(code='MODULE_LOGIC_MARGIN',severity='error',message='分离接口的静态电平参考裕量不为正。',margins_v=margins))
                # AM2302 internal pullup min 1k plus external >=4.7k (including tolerance margin).
                sink=vs['max_v']*(1/1000+1/4400)+1e-5
                row['voltage_reference']['sink_current_reference_a']=sink
                if sink>.008:findings.append(dict(code='MODULE_SINK_CURRENT',severity='error',message='保守上拉电流参考超过传感器 8mA 输出条件。'))
    return rows,findings
