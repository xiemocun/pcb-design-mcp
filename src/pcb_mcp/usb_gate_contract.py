"""Finite hardware interlock contract; steady logic is not physical sign-off."""
import hashlib,json,math
from pathlib import Path
from . import parts,electrical
CC='PCB_MCP:TUSB320LAI_GPIO'
INV='PCB_MCP:SN74LVC1G14_DBV'
SW='PCB_MCP:TPS2553DBV'
AUX='Regulator_Linear:TLV75533PDBV'
USB='Connector:USB_C_Receptacle_USB2.0_16P'
BUCK='PCB_MCP:R78K3V3_1A'
from .power_stage_roles import BUCKS

def present(s):return any(c['library_id'] in (CC,INV,SW) for c in s['components'].values())

def graph(s):
    a={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    def net(c,name):return next((a.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
    return a,net

def roles(s):
    rows={}
    for role,lib in [('cc',CC),('inv',INV),('switch',SW),('aux',AUX),('usb',USB),('buck',BUCK)]:
        found=[c for c in s['components'].values() if c['library_id'] in (BUCKS if role=='buck' else (lib,))]
        if len(found)!=1:return None
        rows[role]=found[0]
    return rows

def static_findings(s):
    if not present(s):return []
    findings=[];r=roles(s)
    def add(code,msg,severity='error'):findings.append(dict(code=code,severity=severity,message=msg))
    if not r:
        add('USB_GATE_PROFILE_INCOMPLETE','受限模块要求唯一的 TUSB320LAI、施密特反相器、TPS2553、独立辅助 LDO、USB 接口和主电源模块。');return findings
    cc,inv,sw,aux,usb,buck=[r[n] for n in ['cc','inv','switch','aux','usb','buck']]
    a,net=graph(s);comps=list(s['components'].values());catalog={p['symbol_id']:p for p in parts.reference_catalog()}
    for c in (cc,inv,sw):
        record=catalog[c['library_id']]
        if record['evidence_status']!='available':add('USB_GATE_SOURCE_CHANGED','控制模块的厂家快照缺失或变化。')
        if {p['number']:p['name'] for p in c['pins']}!=record['facts']['pin_mapping']:add('USB_GATE_PIN_SOURCE_CONFLICT','控制模块引脚与已记录的具体封装不一致。')
    raw,sys,ground,logic=net(usb,'VBUS'),net(sw,'OUT'),net(usb,'GND'),net(aux,'OUT')
    if None in (raw,sys,ground,logic) or len({raw,sys,ground,logic})!=4:add('USB_GATE_RAIL_SEPARATION','入口、受控输出、独立逻辑电源和参考地必须是四个不同的网络。')
    if net(sw,'IN')!=raw or net(buck,'IN')!=sys:add('USB_GATE_POWER_PATH','主电源必须由 TPS2553 输出供电，不可绕过受控路径。')
    if net(aux,'IN')!=raw or net(aux,'EN')!=raw or net(cc,'VDD')!=logic or net(inv,'VCC')!=logic:
        add('USB_GATE_BOOTSTRAP_SUPPLY','CC 与逻辑必须由入口侧独立辅助 LDO 供电，不能依赖尚未开启的主电源；TUSB320LAI 也不能直连最高可超过 5V 的入口。')
    if logic==net(buck,'OUT'):
        add('USB_GATE_BOOTSTRAP_SUPPLY','辅助 3.3V 与主电源 3.3V 不得并联；否则会绕过主电源开关。')
    for c in comps:
        if c['library_id']=='RF_Module:ESP32-WROOM-32E' and net(c,'VDD')!=net(buck,'OUT'):
            add('USB_GATE_POWER_PATH','ESP32 必须由受控主电源模块输出供电，不能接入口侧辅助电源。')
    if any(net(c,'GND')!=ground for c in (cc,inv,sw,aux,buck)):add('USB_GATE_GROUND','控制电路和主电源必须共用明确参考地。')
    if net(cc,'PORT')!=ground or net(cc,'EN_N')!=ground:add('USB_GATE_SINK_MODE','仅覆盖 PORT=地的 Sink 与 EN_N=地的常开控制器模式。')
    def nc(c,name):
        p=next(p for p in c['pins'] if p['name']==name)
        return (c['id'],p['pin_id']) not in a and s['pin_intents'].get(c['id']+':'+p['pin_id'],{}).get('intent')=='no_connect'
    if any(not nc(cc,n) for n in ['ADDR','OUT2','OUT3','ID']):add('USB_GATE_GPIO_MODE','ADDR 需明确悬空选择 GPIO；本受限策略的 OUT2/OUT3/ID 也需明确悬空。')
    if not nc(inv,'NC') or not nc(sw,'FAULT_N'):add('USB_GATE_UNUSED_PIN','未用的逻辑 NC 和开漏故障输出需明确悬空。')
    def passive(lib,x,y,value):
        return [c for c in comps if c['library_id']==lib and None not in (x,y) and x!=y
                and {a.get((c['id'],p['pin_id'])) for p in c['pins']}=={x,y}
                and electrical.number(c['value']) is not None and abs(electrical.number(c['value'])-value)<=value*.001]
    for label in ['CC1','CC2']:
        node=net(cc,label)
        if node is None or node!=net(usb,label) or node in (raw,sys,ground,logic):add('USB_GATE_CC_CONNECTION','接口 CC1/CC2 必须分别直达控制器对应端口。')
        if any(c['library_id']=='Device:R' and {a.get((c['id'],p['pin_id'])) for p in c['pins']}=={node,ground} for c in comps):add('USB_GATE_DUPLICATE_RD','此控制器已内置 Rd，不能再并联原来的外部 Rd。')
    if net(cc,'CC1')==net(cc,'CC2'):add('USB_GATE_CC_CONNECTION','CC1/CC2 不得短接。')
    sense=net(cc,'VBUS_DET')
    if sense in (None,raw,sys,ground,logic) or not passive('Device:R',raw,sense,887000.):add('USB_GATE_VBUS_SENSE','VBUS_DET 需经本参考策略的 887k 标称电阻接入口侧 VBUS。')
    signal=net(cc,'OUT1');enable=net(inv,'Y')
    if signal in (None,raw,sys,ground,logic) or signal!=net(inv,'A') or not passive('Device:R',signal,logic,10000.):add('USB_GATE_CURRENT_SIGNAL','OUT1 必须经独立辅助电源上拉后驱动反相器输入。')
    if enable in (None,raw,sys,ground,logic,signal) or enable!=net(sw,'EN') or not passive('Device:R',enable,ground,100000.):add('USB_GATE_ENABLE_CONTROL','反相器输出必须控制 TPS2553 EN，并有标称 100k 下拉。')
    # Extra drivers could enable a board while default-current mode should be off.
    allowed={cc['id'],inv['id']}|{c['id'] for c in passive('Device:R',signal,logic,10000.)}
    if any(p['component_id'] not in allowed for n in s['nets'].values() if n['id']==signal for p in n['pins']):add('USB_GATE_CURRENT_OVERRIDE','电流判定信号含有本策略外的驱动或负载。')
    allowed={inv['id'],sw['id']}|{c['id'] for c in passive('Device:R',enable,ground,100000.)}
    if any(p['component_id'] not in allowed for n in s['nets'].values() if n['id']==enable for p in n['pins']):add('USB_GATE_ENABLE_OVERRIDE','主电源使能含有本策略外的驱动；不能用 MCU 或常高信号绕过互锁。')
    limit=current_limit(s)
    if limit is None:add('USB_GATE_CURRENT_LIMIT','ILIM 必须仅经一颗有效的 15–232kΩ、≤1% 电阻接地；非默认值必须绑定厂家来源，禁止额外负载或驱动。')
    elif limit['max_a']>1.2:add('USB_GATE_CURRENT_LIMIT','限流上界超过当前全结温范围 1.2A 参考策略；不能仅为提高启动电流而放宽。')
    for c,rail,value in [(cc,logic,1e-7),(inv,logic,1e-7),(sw,raw,1e-6),(aux,logic,1e-6)]:
        if not passive('Device:C',rail,ground,value):add('USB_GATE_BYPASS','控制器、逻辑、开关或辅助电源缺少受管旁路电容。')
    for c in comps:
        if c['library_id'] in ['Device:R','Device:C','Device:C_Polarized'] and {a.get((c['id'],p['pin_id'])) for p in c['pins']}=={raw,sys}:
            add('USB_GATE_BYPASS_PATH','入口与受控主电源之间存在本参考策略不允许的被动旁路。')
    return findings

def current_limit(s):
    r=roles(s)
    if not r:return None
    catalog=parts.catalog();fresh={p['candidate_id']:p for p in catalog}
    source=next(p for p in parts.reference_catalog() if p['symbol_id']==SW)
    if source['evidence_status']!='available':return None
    f=source['facts'];eq=f['current_limit_equations']
    a,net=graph(s);node=net(r['switch'],'ILIM');ground=net(r['switch'],'GND')
    resistors=[c for c in s['components'].values() if c['library_id']=='Device:R' and None not in (node,ground) and node!=ground
               and {a.get((c['id'],p['pin_id'])) for p in c['pins']}=={node,ground}]
    if len(resistors)!=1:return None
    resistor=resistors[0];p=s.get('part_bindings',{}).get(resistor['id']);bound=False
    # SLVS841F p15: one programming resistor; other loads invalidate its formula.
    allowed={(r['switch']['id'],q['pin_id']) for q in r['switch']['pins'] if q['name']=='ILIM'}
    allowed|={(resistor['id'],q['pin_id']) for q in resistor['pins'] if a.get((resistor['id'],q['pin_id']))==node}
    if {(q['component_id'],q['pin_id']) for q in s['nets'][node]['pins']}!=allowed:return None
    if p:
        if fresh.get(p['candidate_id'])!=p or p['evidence_status']!='available' or not parts.binding_valid(s,resistor,p):return None
        t=p['facts'].get('tolerance_fraction');nominal=p['facts'].get('value_ohm',0)/1000
        if not isinstance(t,(int,float)) or not math.isfinite(t) or not 0<=t<=.01:return None
        bound=True
    else:
        # Preserve the legacy unbound 30.1k reference only. Never substitute it
        # for a different value written into the actual circuit.
        value=electrical.number(resistor['value'])
        if value is None or not math.isclose(value,f['current_limit_resistor_nominal_ohm'],rel_tol=1e-12):return None
        nominal=value/1000;t=f['current_limit_resistor_fraction_assumption']
    if not math.isfinite(nominal) or not 15<=nominal<=232:return None
    return dict(min_a=eq['min_coefficient']/(nominal*(1+t))**eq['min_exponent']/1000,
                max_a=eq['max_coefficient']/(nominal*(1-t))**eq['max_exponent']/1000,
                resistor_tolerance_fraction=t,assumed_resistor_tolerance_fraction=None if bound else t,
                tolerance_basis='bound_source_catalog_nominal_tolerance' if bound else 'unverified_assumption',
                bound_catalog_tolerance_checked=bound,resistor_reference=resistor['ref'],resistor_mpn=p['mpn'] if bound else None,
                resistor_nominal_ohm=nominal*1000,
                physical_resistor_tolerance_verified=False,
                qualification='conditional_datasheet_equations_not_whole_port_current_or_inrush_qualification')

def evaluate(s):
    findings=static_findings(s);r=roles(s);rows=[]
    if not present(s):findings=[dict(code='USB_GATE_PROFILE_UNSUPPORTED',severity='error',message='当前电路没有受支持的 USB 硬件互锁。')]
    if present(s):findings.append(dict(code='USB_GATE_ATTACH_STATE_ONLY',severity='warning',message='资料描述进入 Attached.SNK 时报告电流模式；不能从此真值表推断持续检测或源电流降级响应。'))
    valid=r is not None and not any(f['severity']=='error' for f in findings)
    if r:
        cc=next(p for p in parts.reference_catalog() if p['symbol_id']==CC)
        for mode in cc['facts']['gpio_current_truth']:
            enabled=not bool(mode['out1'])
            rows.append(dict(**mode,expected_main_enabled=mode['mode'] in ['advertised_1p5a','advertised_3a'],predicted_main_enabled=enabled if valid else None,topology_checked=valid,scope='stable_valid_aux_supply_and_ideal_logic_states_only'))
    sources=[p for p in parts.reference_catalog() if p['symbol_id'] in (CC,INV,SW)]
    return dict(revision=s['revision'],verdict='pass' if valid else 'fail',findings=findings,scenarios=rows,current_limit=current_limit(s),
                startup_and_analog_behavior='not_qualified',live_cc_measurement=False,manufacturing_approved=False,
                sources=[dict(mpn=p['mpn'],sources=p['sources']) for p in sources],
                evidence_hash=hashlib.sha256(Path(__file__).read_bytes()+json.dumps(sources,sort_keys=True).encode()).hexdigest(),
                qualification='fixed_static_topology_and_conditional_truth_table_not_physical_or_USB_compliance_approval')
