"""Small, versioned, source-linked experimental rule packs.

These checks are intentionally narrower than a functional hardware approval.
Nominal voltage propagation is not tolerance, transient, thermal or USB compliance
analysis. Unknown evidence remains visible rather than being inferred by the LLM.
"""
import hashlib
import json
import re
from pathlib import Path

SOURCES={
 'usb_gate':dict(title='TI TUSB320LAI SLLSEQ8D; TPS2553 SLVS841F; SN74LVC1G14 SCES218AA',url='https://www.ti.com/lit/ds/symlink/tusb320lai.pdf',section='GPIO Table 3; source-correct pin maps; separate switched system rail and 3.3V always-on logic'),
 'r78k':dict(title='RECOM R-78K-1.0 Rev. 6-2025',url='https://recom-power.com/pdf/Innoline/R-78K-1.0.pdf',section='Pages 1/2/5/7 ratings, reference conditions, hard-plug input capacitor and pin order'),
 'esp32':dict(title='ESP32-WROOM-32E / 32UE datasheet v2.1',url='https://documentation.espressif.com/esp32-wroom-32e_esp32-wroom-32ue_datasheet_en.html',section='3 Pin definitions; 6.2 Recommended operating conditions; 9 Peripheral schematics'),
 'am2302':dict(title='Aosong AM2302 Technical Manual v1.0',url='https://www.aosong.com/uploadfiles/2025/04/20250417105409216.pdf',section='3.1 power, 4 electrical specifications, 5.1 typical application'),
 'tlv755':dict(title='TI TLV755P SBVS320D Rev D, September 2024',url='https://www.ti.com/lit/ds/symlink/tlv755p.pdf',section='4 pin functions; 5.3 operating conditions; 7.1 capacitors'),
 'txs':dict(title='TI TXS0102 SCES640L January 2026',url='https://www.ti.com/lit/ds/symlink/txs0102.pdf',section='4 pin table; 5.3 supply limits; 7.3.5 internal pullups; 8.3 OE sequencing; 8.4 layout example'),
 'usbc':dict(title='TI SLYY109B USB Type-C primer, March 2022',url='https://www.ti.com/lit/wp/slyy109b/slyy109b.pdf',section='Page 4: UFP independent Rd, CC orientation and advertised current'),
 'usbc_rd':dict(title='Espressif USB Type-C Hardware Design Guide',url='https://docs.espressif.com/projects/esp-iot-solution/en/latest/usb/usb_overview/usb_typec_hardware_guide.html',section='Power supply and CC pins: independent Rd, typically 5.1k'),
}
PACKS={
 'usb_gate_v1':dict(libraries=['PCB_MCP:TUSB320LAI_GPIO','PCB_MCP:TPS2553DBV','PCB_MCP:SN74LVC1G14_DBV'],source='usb_gate',checks=['primary-source package pin identity','sink-only GPIO mode and internal Rd','auxiliary always-on supply without main-rail bootstrap','OUT1 inverter to active-high power switch','conditional current-limit equations; startup and analog behavior remain unqualified']),
 'r78k33_v1':dict(libraries=['PCB_MCP:R78K3V3_1A'],source='r78k',checks=['primary-source 1 IN / 2 GND / 3 OUT mapping','nominal input 4.5..36V','fixed 3.3V nominal output','nominal 27uF polarized hard-plug input capacitor; effective capacity unqualified','separate source current, ripple, transient and thermal diagnostics required']),
 'esp32_module_v1':dict(libraries=['RF_Module:ESP32-WROOM-32E'],source='esp32',checks=['nominal VDD 3.0..3.6 V','GND bound to declared ground','EN 10k pull-up and reset capacitor policy','GPIO0 boot pull-up policy','supply capacitor presence']),
 'am2302_v1':dict(libraries=['Sensor:AM2302','PCB_MCP:AM2302_SIP4'],source='am2302',checks=['nominal VDD 3.3..5.5 V','SDA pull-up to sensor supply','safe connected ESP32 GPIO if present','same nominal sensor / MCU logic rail']),
 'tlv75533_v1':dict(libraries=['Regulator_Linear:TLV75533PDBV'],source='tlv755',checks=['nominal IN 1.45..5.5 V','conservative 0.5V nominal headroom policy','GND and EN bound','input/output capacitor presence']),
 'txs0102_v1':dict(libraries=['Logic_LevelTranslator:TXS0102DCT'],source='txs',checks=['source DCT pin mapping','nominal VCCA 1.65..3.6 / VCCB 2.3..5.5 V','fixed ESP32 IO27/A1/B1/AM2302 data topology','IO26/OE control and nominal 100k pulldown','unused channel grounded; nominal 100nF rail bypass','separate interval module check required; loaded logic and protocol remain unqualified']),
 'usb_c_sink_v1':dict(libraries=['Connector:USB_C_Receptacle_USB2.0_16P'],source='usbc',checks=['nominal 5V-only policy; no PD','VBUS / GND groups consistent','CC1 and CC2 isolated','separate nominal Rd or complete source-checked internal-Rd controller profile']),
}


def curated_sources_hash():
    from . import parts
    return hashlib.sha256(json.dumps(parts.reference_catalog(),sort_keys=True).encode()).hexdigest()


def rulepack(ident,source_hash=None):
    p=PACKS[ident]
    data=dict(id=ident,version='1.0',status='experimental_not_human_approved',applies_to=p['libraries'],
              checks=p['checks'],evidence=SOURCES[p['source']],
              not_checked=['voltage tolerances','USB current negotiation','load/thermal budget','RF/layout','manufacturing'],
              policy_note='Passive values are nominal display values; ratings and tolerances are not verified. Reset/boot component values and LDO headroom are deliberately restricted reference-topology policies, not universal manufacturer minimums.',
              implementation_hash=hashlib.sha256(Path(__file__).read_bytes()+Path(__file__).with_name('module_contracts.py').read_bytes()+Path(__file__).with_name('usb_gate_contract.py').read_bytes()).hexdigest())
    # Delegated checks consume curated source facts and their snapshot status.
    # A changed catalog must invalidate unbound prototype evidence too.
    data['curated_sources_hash']=source_hash if source_hash is not None else curated_sources_hash()
    if ident=='usb_c_sink_v1':data['additional_evidence']=[SOURCES['usbc_rd']]
    data['definition_hash']=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
    return data


def number(value):
    match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([pnumkM]?)(?:F|V|Ω|ohm)?',value)
    if not match:return None
    return float(match[1])*{'':1,'p':1e-12,'n':1e-9,'u':1e-6,'m':1e-3,'k':1e3,'M':1e6}[match[2]]


def evaluate(s):
    findings=[]; coverage=[]; rails={}; ground=set(); applied=[]
    from . import parts
    findings.extend(parts.reference_binding_findings(s))
    comps=s['components']; nets=s['nets']
    assignment={(p['component_id'],p['pin_id']):n['id'] for n in nets.values() for p in n['pins']}
    def net(c,name):
        p=next((p for p in c['pins'] if p['name']==name),None)
        return assignment.get((c['id'],p['pin_id'])) if p else None
    def finding(code,message,c=None,pack=None,severity='error'):
        findings.append(dict(code=code,severity=severity,message=message,entity_ids=[c['id']] if c else [],
                             evidence_ref=rulepack(pack) if pack else None))
    for source in s.get('power_sources',{}).values():
        rails[source['net_id']]=dict(nominal_v=source['nominal_v'],basis='declared_external_not_measured',source_id=source['id'])
        rails[source['ground_net_id']]=dict(nominal_v=0.0,basis='declared_ground',source_id=source['id'])
        ground.add(source['ground_net_id'])
    # Propagate only a named fixed-output LDO whose input is already known.
    from .feedback_voltage import evaluate as feedback_voltage
    feedback=feedback_voltage(s);findings.extend(feedback['findings'])
    for row in feedback['rows']:
        output=row['output_net_id']
        if output in rails:finding('CONFLICTING_RAIL_DRIVERS','Feedback output conflicts with an existing declared source')
        else:rails[output]=dict(nominal_v=row['nominal_v'],basis=row['qualification'],model_id=row['model_id'])
    if feedback['rows']:
        findings.append(dict(code='FEEDBACK_NOMINAL_ONLY',severity='warning',message='Source-bound feedback setting calculated; input delivery, enable state, load, mode, tolerances, startup and layout are not established by this nominal equation.',entity_ids=[r['component_id'] for r in feedback['rows']]))
    from . import usb_gate_contract as gate
    for c in comps.values():
        if c['library_id']==gate.SW and net(c,'IN') in rails:
            output=net(c,'OUT')
            if output:
                if output in rails:finding('CONFLICTING_RAIL_DRIVERS','受控输出不能与已声明入口重叠。',c,'usb_gate_v1')
                else:rails[output]=dict(nominal_v=rails[net(c,'IN')]['nominal_v'],basis='conditional_enabled_switch_nominal_only',component_id=c['id'])
    for c in comps.values():
        if c['library_id'] in ['Regulator_Linear:TLV75533PDBV','PCB_MCP:R78K3V3_1A'] and net(c,'IN') in rails:
            output=net(c,'OUT')
            if output:
                if output in rails:
                    finding('CONFLICTING_RAIL_DRIVERS','稳压器输出与另一个已知驱动重叠；相同标称电压也不能证明可并联。',c,'r78k33_v1' if c['library_id']=='PCB_MCP:R78K3V3_1A' else 'tlv75533_v1')
                else:rails[output]=dict(nominal_v=3.3,basis='fixed_part_nominal_only',component_id=c['id'])
    from .regulator_models import analyze as regulator_analyze
    pwm=regulator_analyze(s);findings.extend(pwm['findings'])
    for row in pwm['regulators']:
        output=row['output_net_id']
        if output in rails:finding('CONFLICTING_RAIL_DRIVERS','可调电源输出与已有电源重叠。')
        else:rails[output]=dict(nominal_v=row['output_interval_v']['nominal_v'],basis='source_bound_PWM_feedback_nominal_only')
    def voltage(c,pin,min_v,max_v,pack):
        nid=net(c,pin)
        if nid not in rails:
            finding('VOLTAGE_EVIDENCE_MISSING',f'{pin} 缺少可追溯的电源声明或支持的稳压器来源。',c,pack);return
        if not min_v<=rails[nid]['nominal_v']<=max_v:
            finding('NOMINAL_VOLTAGE_OUT_OF_RANGE',f'{pin} 标称电压 {rails[nid]["nominal_v"]}V 不在 {min_v}..{max_v}V 内。',c,pack)
    def resistor(a,b,lo,hi):
        if a is None or b is None or a==b:return False
        for x in comps.values():
            if x['library_id'] not in ['Device:R','Device:R_US']:continue
            bound={assignment.get((x['id'],p['pin_id'])) for p in x['pins']}
            value=number(x['value'])
            if bound=={a,b} and value is not None and lo<=value<=hi:return True
        return False
    def capacitor(a,b,minimum):
        if a is None or b is None or a==b:return False
        for x in comps.values():
            if x['library_id'] not in ['Device:C','Device:C_Polarized']:continue
            bound={assignment.get((x['id'],p['pin_id'])) for p in x['pins']}
            value=number(x['value'])
            if bound=={a,b} and value is not None and value>=minimum*.999:return True
        return False
    for c in comps.values():
        pack=next((i for i,p in PACKS.items() if c['library_id'] in p['libraries']),None)
        if not pack:continue
        applied.append(pack)
        gnd=net(c,'GND')
        if c['library_id'].startswith('Connector:'):
            gnd=next((assignment.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']=='GND'),None)
        if gnd not in ground:finding('GROUND_EVIDENCE_MISSING','元件地线未连接到已声明参考地。',c,pack)
        if c['library_id']=='Sensor:AM2302':finding('SYMBOL_SOURCE_PIN_CONFLICT','厂家表4规定3脚NC、4脚GND；本机旧符号将其堆叠，需改选 PCB_MCP:AM2302_SIP4。',c,pack)
        if pack=='tlv75533_v1':
            vi,vo=net(c,'IN'),net(c,'OUT');voltage(c,'IN',1.45,5.5,pack)
            if vi in rails and rails[vi]['nominal_v']<3.8:finding('LDO_HEADROOM_POLICY','本实验保守策略要求 3.3V LDO 输入至少 3.8V。',c,pack)
            if net(c,'EN')!=vi:finding('LDO_ENABLE_UNSUPPORTED','本规则包只验证 EN 直接接 IN 的常开配置。',c,pack)
            if not capacitor(vi,gnd,1e-6):finding('LDO_INPUT_CAP_MISSING','输入到地需至少 1uF 标称电容。',c,pack)
            if not capacitor(vo,gnd,1e-6):finding('LDO_OUTPUT_CAP_MISSING','输出到地需至少 1uF 标称电容。',c,pack)
        elif pack=='esp32_module_v1':
            v=net(c,'VDD');voltage(c,'VDD',3.0,3.6,pack)
            if not resistor(net(c,'EN'),v,9000,11000):finding('ESP32_EN_PULLUP_MISSING','缺少 EN 到 VDD 的 10k 标称上拉。',c,pack)
            if not capacitor(net(c,'EN'),gnd,1e-6):finding('ESP32_RESET_CAP_MISSING','缺少 EN 到地的 1uF 标称复位电容。',c,pack)
            if not resistor(net(c,'IO0'),v,9000,11000):finding('ESP32_BOOT_PULLUP_MISSING','缺少 IO0 到 VDD 的 10k 标称启动上拉。',c,pack)
            if not capacitor(v,gnd,10e-6):finding('ESP32_BULK_CAP_MISSING','缺少 VDD 到地的 10uF 标称储能电容。',c,pack)
        elif pack=='r78k33_v1':
            from . import parts
            record=next(p for p in parts.reference_catalog() if p['symbol_id']==c['library_id'])
            if record['evidence_status']!='available':finding('BUCK_SOURCE_CHANGED','开关电源资料快照缺失或变化。',c,pack)
            if {p['number']:p['name'] for p in c['pins']}!=record['facts']['pin_mapping']:finding('BUCK_PIN_SOURCE_CONFLICT','模块引脚与厂家记录不一致。',c,pack)
            voltage(c,'IN',4.5,36.0,pack)
            if len({net(c,'IN'),net(c,'OUT'),gnd})!=3 or None in [net(c,'IN'),net(c,'OUT'),gnd]:finding('BUCK_PORT_SHORT','输入、输出和地必须是三个不同的明确网络。',c,pack)
            hotplug=any(x['library_id']=='Device:C_Polarized'
                        and next((assignment.get((x['id'],p['pin_id'])) for p in x['pins'] if p['number']=='1'),None)==net(c,'IN')
                        and next((assignment.get((x['id'],p['pin_id'])) for p in x['pins'] if p['number']=='2'),None)==gnd
                        and number(x['value']) is not None and number(x['value'])>=27e-6*.999 for x in comps.values())
            if not hotplug:finding('BUCK_HOTPLUG_CAP_MISSING','参考硬插拔方案需至少 27uF 标称输入电解电容且正负极正确；有效容量、额定电压和涌入电流仍未验证。',c,pack)
        elif pack=='am2302_v1':
            v,data=net(c,'VDD'),net(c,'SDA');voltage(c,'VDD',3.3,5.5,pack)
            if v in rails and rails[v]['nominal_v']<=3.3:
                finding('SENSOR_SUPPLY_MARGIN_UNVERIFIED','AM2302 标称供电恰在手册下限；3.3V 稳压器负容差可能低于 3.3V，需要更换经核实的传感器方案或验证最坏情况。',c,pack,'warning')
            if not resistor(data,v,4700,10000):finding('SENSOR_PULLUP_MISSING','SDA 需有到传感器供电的上拉；本策略接受 4.7k..10k 标称值。',c,pack)
            for mcu in comps.values():
                if mcu['library_id']!='RF_Module:ESP32-WROOM-32E':continue
                linked=[p for p in mcu['pins'] if assignment.get((mcu['id'],p['pin_id']))==data]
                safe={'IO4','IO16','IO17','IO18','IO19','IO21','IO22','IO23','IO25','IO26','IO27','IO32','IO33'}
                from .module_contracts import bridging
                bridge=bridging(s,c,mcu)
                if not bridge:
                    if not linked or any(p['name'] not in safe for p in linked):finding('SENSOR_GPIO_UNSUPPORTED','传感器未直接连接到支持的 GPIO，且没有受管电平转换模块。',c,pack)
                    if v!=net(mcu,'VDD'):finding('LEVEL_SHIFT_REQUIRED','不同供电域需要完整的受管电平转换模块。',c,pack)
                elif linked:finding('MODULE_UNSHIFTED_GPIO','传感器高压侧同时直接连接了 MCU 引脚，绕过电平转换。',c,pack)
        elif pack=='txs0102_v1':
            voltage(c,'VCCA',1.65,3.6,pack);voltage(c,'VCCB',2.3,5.5,pack)
            va,vb=net(c,'VCCA'),net(c,'VCCB')
            if va in rails and vb in rails and rails[va]['nominal_v']>rails[vb]['nominal_v']:finding('MODULE_RAIL_ORDER','VCCA 不得高于 VCCB。',c,pack)
        elif pack=='usb_gate_v1':
            from . import parts
            record=next(p for p in parts.reference_catalog() if p['symbol_id']==c['library_id'])
            supply='IN' if c['library_id']==gate.SW else ('VCC' if c['library_id']==gate.INV else 'VDD')
            voltage(c,supply,record['facts']['supply_min_v'],record['facts']['supply_max_v'],pack)
        elif pack=='usb_c_sink_v1':
            vbus=net(c,'VBUS')
            if vbus not in rails:finding('VOLTAGE_EVIDENCE_MISSING','VBUS 缺少外部标称供电声明。',c,pack)
            elif abs(rails[vbus]['nominal_v']-5.0)>1e-6:finding('USB_NOMINAL_5V_POLICY','本规则包只覆盖标称 5V、无 PD 的受电实验。',c,pack)
            byname={}
            for p in c['pins']:byname.setdefault(p['name'],set()).add(assignment.get((c['id'],p['pin_id'])))
            for role in ['VBUS','GND']:
                if len(byname.get(role,set()))!=1 or None in byname[role]:finding('USB_POWER_GROUP_INCONSISTENT',f'{role} 的全部引脚必须同网连接。',c,pack)
            cc1,cc2=net(c,'CC1'),net(c,'CC2')
            if cc1 is None or cc2 is None or cc1==cc2:finding('USB_CC_SHORT_OR_MISSING','CC1 与 CC2 必须分别连接且不能短接。',c,pack)
            if not gate.present(s):
                for role,n in [('CC1',cc1),('CC2',cc2)]:
                    if not resistor(n,gnd,5100*.8,5100*1.2):finding('USB_RD_MISSING',f'{role} 缺少独立的约 5.1k 标称下拉至地。',c,pack)
            if any(p['name'] in ['D+','D-','SBU1','SBU2'] and (c['id'],p['pin_id']) in assignment for p in c['pins']):
                finding('USB_DATA_NOT_IN_RULE_SCOPE','此规则包仅覆盖 Type-C 仅供电实验。',c,pack)
            finding('USB_CURRENT_CAPABILITY_UNVERIFIED','Rd 不等于已验证可用电流；CC 电流检测、供电预算与 USB 合规尚未验证。',c,pack,'warning')
        coverage.append(dict(check_id=pack,component_id=c['id'],status='checked',rulepack_hash=rulepack(pack)['definition_hash']))
    from .module_contracts import static_findings
    findings.extend(static_findings(s))
    findings.extend(gate.static_findings(s))
    supported={lib for p in PACKS.values() for lib in p['libraries']}
    for c in comps.values():
        if c['library_id'] not in supported:
            coverage.append(dict(check_id='individual_component_electrical_rule',component_id=c['id'],status='unsupported',
                                 note='No standalone electrical pack. Passives may be checked only in the reference topology of supported parts.'))
    if applied:
        finding('TOLERANCE_THERMAL_BUDGET_UNVERIFIED','本次只检查标称电压及拓扑；电容有效容量、器件容差、LDO 温升和负载预算未验证。',severity='warning')
    coverage.extend([dict(check_id='voltage_tolerances_and_transients',status='unsupported'),
                     dict(check_id='thermal_and_current_budget',status='unsupported'),
                     dict(check_id='human_engineering_review',status='not_performed')])
    from .analog_models import analyze as analog_analyze
    analog=analog_analyze(s);findings.extend(analog['findings'])
    if analog['amplifiers']:
        coverage.append(dict(check_id='source_bound_unity_follower_topology',status='checked',details=analog))
        applied.append('source_bound_unity_follower_topology')
    from .timer_models import analyze as timer_analyze
    timer=timer_analyze(s);findings.extend(timer['findings'])
    if timer['timers'] or timer['requirements']['status']!='not_declared':
        coverage.append(dict(check_id='timer_nominal_requirement_comparison',status='checked',details=timer))
        # This partial nominal check alone must not promote an incomplete result
        # to full electrical pass. Actual timing/output qualification is separate.
    errors=[f for f in findings if f['severity']=='error']
    return dict(verdict='fail' if errors else ('pass' if applied else 'incomplete'),revision=s['revision'],findings=findings,coverage=coverage,
                rails=rails,qualification='experimental_nominal_checks_only' if applied else 'no_applicable_electrical_rules')
