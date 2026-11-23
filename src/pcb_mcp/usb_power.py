"""Read-only USB-C sink diagnostics; a scalar source claim never proves CC."""
from . import electrical,power_integrity,parts,usb_gate_contract,usb_psd
from .placement_rules import USB,BUCK

SOURCES=[dict(title='TI USB Type-C primer SLYY109B',url='https://www.ti.com/lit/wp/slyy109b/slyy109b.pdf',section='CC orientation, Rd and advertised current'),
         dict(title='TI An Engineers Guide to USB Type-C SLYY228 November 2024',url='https://www.ti.com/lit/eb/slyy228/slyy228.pdf',section='Page 54: sink-only VBUS capacitance and switched power path'),
         dict(title='TI TUSB320 SLLSEN9F',url='https://www.ti.com/lit/ds/symlink/tusb320.pdf',section='7.3.2 current modes; CC detection does not itself limit downstream load')]

def evaluate(s):
    findings=[];ports=[];comps=list(s['components'].values())
    controlled=usb_gate_contract.present(s);contract=usb_gate_contract.evaluate(s) if controlled else None
    if contract:findings.extend(contract['findings'])
    assigned={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    def net(c,name):return next((assigned.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
    power=power_integrity.evaluate(s)
    def add(code,message,severity='error'):findings.append(dict(code=code,severity=severity,message=message))
    for c in comps:
        if c['library_id']!=USB:continue
        vbus,gnd=net(c,'VBUS'),net(c,'GND');rd={}
        for role in ['CC1','CC2']:
            cc=net(c,role)
            rd[role]=[x['ref'] for x in comps if x['library_id']=='Device:R'
                      and cc is not None and gnd is not None and cc!=gnd
                      and {assigned.get((x['id'],p['pin_id'])) for p in x['pins']}=={cc,gnd}
                      and electrical.number(x['value']) is not None and 4080<=electrical.number(x['value'])<=6120]
        cc1,cc2=net(c,'CC1'),net(c,'CC2')
        independent=bool(rd['CC1'] and rd['CC2'] and cc1!=cc2 and not set(rd['CC1'])&set(rd['CC2']))
        if controlled:
            independent=contract['verdict']=='pass'
            if independent:rd={role:['TUSB320LAI internal Rd'] for role in ['CC1','CC2']}
        elif not independent:add('USB_RD_TOPOLOGY_INVALID','CC1/CC2 需要独立受电端下拉，不能短接或共享同一电阻。')
        caps=[]
        for x in comps:
            if x['library_id'] not in ['Device:C','Device:C_Polarized']:continue
            if vbus is not None and gnd is not None and {assigned.get((x['id'],p['pin_id'])) for p in x['pins']}=={vbus,gnd}:
                value=electrical.number(x['value'])
                caps.append(dict(reference=x['ref'],nominal_uf=value*1e6 if value is not None else None,effective_capacitance_verified=False))
        ports.append(dict(reference=c['ref'],independent_rd_present=independent,rd_references=rd,
                          cc_current_detector='source_checked_TUSB320LAI_topology' if controlled and independent else 'not_verified_in_supported_reference',
                          advertised_current='unknown_not_measured',downstream_load_gate='source_checked_TPS2553_steady_logic' if controlled and independent else 'unverified',
                          direct_vbus_capacitors=caps,nominal_direct_capacitance_uf=sum(x['nominal_uf'] or 0 for x in caps)))
        if sum(x['nominal_uf'] or 0 for x in caps)>10.0:
            add('USB_DIRECT_CAP_NOMINAL_EXCESS','VBUS 直连标称容量已超过本参考策略的 10uF 筛选阈值；需要受控电源路径隔离及实际容量/浪涌验证。')
        if controlled:
            add('USB_DEFAULT_MODE_INTENTIONALLY_OFF','受限稳态策略在默认电流模式关闭主电源；不会在普通默认电流 USB 口上承诺完整功能。','warning')
            add('USB_STARTUP_DOWNGRADE_UNQUALIFIED','仅核对稳定辅助电源下的硬件接法和真值表；上电、源电流降级、拔插、模拟门限和实际时序待验证。','warning')
            add('USB_SWITCH_INRUSH_UNQUALIFIED','大电容位于受控输出，已有软启动/限流器件模型；实际浪涌、充电过程、压降及温升尚未资格验证。','warning')
            add('USB_PRE_GATE_LOAD_UNKNOWN','入口侧辅助电路负载上界未完成；限流只约束开关输出，不能当作整个 USB 口的保证电流上界。','warning')
        else:
            default=usb_psd.evaluate(s,c);ports[-1]['default_power']=default
            findings.extend(default['findings'])
            if default['role_verified_from_connected_pins']:
                ports[-1]['cc_current_detector']='not_required_to_claim_only_PSD_default_current'
                ports[-1]['downstream_load_gate']='not_a_universal_requirement_PSD_attach_load_must_be_proven'
            else:
                add('USB_CC_CURRENT_NOT_DETECTED','未识别为纯受电默认模式，且没有受管CC电流检测；不能承诺1.5A/3A。')
                add('USB_DEFAULT_MODE_UNQUALIFIED','未识别为纯受电设备；USB数据/充电/其他默认模式需要独立模型。')
                add('USB_ATTACH_LOAD_POLICY_MISSING','没有可用的默认纯受电负载模型或受控接入策略。')
            if any(x['library_id']==BUCK for x in comps):add('USB_INRUSH_UNQUALIFIED','新增硬插拔输入电容直接挂在 VBUS；尚未证明浪涌限制、VBUS 跌落和附件/保护器件能力。')
    if not ports:add('USB_POWER_PROFILE_UNSUPPORTED','没有受支持的 USB-C 受电端参考电路。')
    ctx=s.get('power_envelope',{})
    if ctx:add('USB_SOURCE_DECLARATION_NOT_CC_EVIDENCE','声明的可用电流只是一项外部假设/用户输入，不是实时 CC 检测或 USB 合规证据。','warning')
    lower=[x.get('input_design_current_lower_bound_a') for x in power['calculations'] if x.get('input_design_current_lower_bound_a') is not None]
    if ctx and lower and ctx['available_current_ma']/1000<sum(lower):add('USB_DECLARED_CURRENT_BELOW_KNOWN_BOUND','声明的输入电流连理想转换下的已知设计需求下界都不能覆盖。')
    return dict(revision=s['revision'],verdict='fail' if any(f['severity']=='error' for f in findings) else 'incomplete',findings=findings,ports=ports,
                source_declaration=ctx or None,minimum_input_design_current_lower_bound_a=sum(lower) if lower else None,
                total_input_current_upper_bound_a=None,sources=SOURCES+usb_psd.SOURCES,
                evidence_hash=parts.rules_hash(),
                manufacturing_approved=False,qualification='static_specification_diagnostics_not_live_CC_measurement_or_USB_compliance_approval')
