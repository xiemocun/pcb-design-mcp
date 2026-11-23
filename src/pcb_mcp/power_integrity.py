"""Source-linked envelope diagnostics; family screening is not part qualification."""
import hashlib,json
from pathlib import Path
from . import parts,electrical
from .placement_rules import ESP,SENSOR,LDO,USB,BUCK
from .power_stage_roles import PWM_BUCK

def evidence_hash():
    return hashlib.sha256(Path(__file__).read_bytes()+json.dumps(parts.catalog(),sort_keys=True).encode()).hexdigest()

def supports_envelope(s):
    """Accept existing profiles or a bound, source-verified supply-pin model."""
    from .linear_passive import eligible
    if eligible(s):return True
    if any(c['library_id'] in [ESP,SENSOR,LDO,USB,BUCK,'Sensor:AM2302'] for c in s['components'].values()):
        return True
    catalog={p['candidate_id']:p for p in parts.catalog()}
    # Open model support still needs exact current identities for every part;
    # declaring a source cannot legitimize missing or forged component facts.
    if s.get('power_sources'):
        from .verification_models import packages
        modeled={m['part_definition_hash'] for m,_ in packages().values()}
        complete=True
        for c in s['components'].values():
            p=s.get('part_bindings',{}).get(c['id'])
            if not p or catalog.get(p['candidate_id'])!=p or p['evidence_status']!='available' or not parts.binding_valid(s,c,p):complete=False;break
            if p['kind'] not in ('resistor','capacitor','connector') and p['definition_hash'] not in modeled:complete=False;break
        if complete and s['components']:return True
    for c in s['components'].values():
        binding=s.get('part_bindings',{}).get(c['id'])
        if (binding and catalog.get(binding['candidate_id'])==binding
                and binding['evidence_status']=='available'
                and binding['facts'].get('power_budget_model')=='supply_pin_load'
                and parts.binding_valid(s,c,binding)):
            return True
    return False


def evaluate(s,replacement_evidence=None):
    from . import dc_power_tree
    tree=dc_power_tree.reference(s)
    if tree is not None:
        byname={n['name']:n['id'] for n in s['nets'].values()}
        rails={byname[name]:dict(min_v=r['minimum_v'],nominal_v=r['nominal_v'],max_v=r['maximum_v'],basis=r['basis']) for name,r in tree['rails'].items() if name in byname}
        return dict(revision=s['revision'],verdict='fail' if any(f['severity']=='error' for f in tree['findings']) else 'pass',findings=tree['findings'],rails=rails,calculations=[tree],evidence_hash=evidence_hash(),qualification='source_linked_declared_DC_envelopes_not_physical_supply_proof')
    from .linear_passive import analyze
    linear=analyze(s)
    if linear is None or linear['verdict']=='incomplete':
        from .model_power import reference
        model=reference(s,replacement_evidence)
        if model is not None:
            rails={}
            for source in s.get('power_sources',{}).values():
                rails[source['net_id']]=dict(min_v=source['nominal_v'],nominal_v=source['nominal_v'],max_v=source['nominal_v'],basis='source_bound_nominal_model_only')
                rails[source['ground_net_id']]=dict(min_v=0.,nominal_v=0.,max_v=0.,basis='declared_reference')
            return dict(revision=s['revision'],verdict='fail' if model['findings'] else 'pass',findings=model['findings'],rails=rails,calculations=[model],evidence_hash=evidence_hash(),qualification='source_bound_nominal_model_supply_reference_not_physical_proof')
    if linear is not None:
        return dict(revision=s['revision'],verdict=linear['verdict'],findings=linear['findings'],rails=linear['rails'],calculations=[linear],evidence_hash=evidence_hash(),qualification='source_bound_linear_DC_reference_not_physical_qualification')
    findings=[];rails={};calcs=[];comps=s['components'];ctx=s.get('power_envelope',{})
    assignment={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    def net(c,name):return next((assignment.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
    def add(code,msg,c=None,severity='error'):findings.append(dict(code=code,severity=severity,message=msg,entity_ids=[c['id']] if c else []))
    catalog={p['symbol_id']:p for p in parts.reference_catalog()}
    bound=parts.bound_supply_models(s)
    active=[c for c in comps.values() if c['library_id'] in [ESP,SENSOR,LDO,USB,BUCK,PWM_BUCK,'Sensor:AM2302'] or c['id'] in bound or catalog.get(c['library_id'],{}).get('facts',{}).get('power_budget_model')=='supply_pin_load']
    # The LED branch checker now has exact-part, temperature-scoped ratings.
    # Consume the actual result here, rather than accepting the mere presence
    # of a model or applying a different LED's fixed 25 C limits.
    dc_models={}
    try:
        from .dc_limits import bound_models
        from .dc_led_branch import evaluate as dc_led_evaluate
        dc_models=bound_models(s)
        for cid,(_,_,limits) in dc_models.items():
            if limits.kind!='led':continue
            active.append(comps[cid])
            try:calcs.append(dc_led_evaluate(s,comps[cid],dc_models))
            except (ValueError,KeyError,TypeError) as exc:add('LED_DC_RATING_OR_TOPOLOGY_INVALID',str(exc),comps[cid])
    except ValueError as exc:add('LED_DC_MODEL_INVALID',str(exc))
    if not ctx:add('SUPPLY_ENVELOPE_MISSING','需声明板端实际电压范围、可用电流和环境；标称 5V 不能代替边界范围。')
    if ctx and not any(p['boundary_pin']==ctx['positive_pin'] and p['ground_net_id']==ctx['ground_net_id'] and p['nominal_v']==ctx['nominal_v'] and assignment.get((p['boundary_pin']['component_id'],p['boundary_pin']['pin_id']))==p['net_id'] for p in s.get('power_sources',{}).values()):
        add('SUPPLY_ENVELOPE_BOUNDARY_STALE','范围声明已不再对应当前有效电源入口，需重新声明。')
    for source in s.get('power_sources',{}).values():
        applies=ctx and ctx['positive_pin']==source['boundary_pin'] and ctx['ground_net_id']==source['ground_net_id'] and ctx['nominal_v']==source['nominal_v']
        rails[source['net_id']]=dict(min_v=ctx['min_v'] if applies else source['nominal_v'],nominal_v=source['nominal_v'],max_v=ctx['max_v'] if applies else source['nominal_v'],basis='declared_envelope_not_measured' if applies else 'nominal_only_missing_envelope')
        rails[source['ground_net_id']]=dict(min_v=0.0,nominal_v=0.0,max_v=0.0,basis='declared_ground')
    try:
        from .dc_pass_network import propagate
        derived,path_calculations=propagate(s,rails,dc_models)
        rails.update(derived);calcs.extend(path_calculations)
    except (ValueError,KeyError,TypeError) as exc:add('DC_PASS_ENVELOPE_INVALID',str(exc))
    from . import usb_gate_contract as gate
    for c in comps.values():
        if c['library_id']!=gate.SW:continue
        vi=rails.get(net(c,'IN'));out=net(c,'OUT');limit=gate.current_limit(s)
        if vi and out and limit:
            f=catalog[gate.SW]['facts'];drop=f['rds_on_max_ohm']*limit['max_a']
            if out in rails:add('RAIL_DRIVER_CONFLICT','受控系统输出与其它电源重叠，不能覆盖证据。',c)
            else:rails[out]=dict(min_v=vi['min_v']-drop,nominal_v=vi['nominal_v'],max_v=vi['max_v'],basis='conditional_switch_enabled_DC_drop_'+limit['tolerance_basis'],source_component=c['id'])
            calcs.append(dict(component_id=c['id'],mpn_basis=catalog[gate.SW]['mpn'],conditional_current_limit=limit,reference_max_switch_drop_v=drop,always_on_input_load_upper_bound_a=None,physical_inrush_and_thermal_proof=False))
            add('USB_SWITCH_BUDGET_CONDITIONAL',('已核对所绑电阻的厂家标称容差，但未证明实物温漂/老化、入口侧辅助负载、浪涌和实际温升。' if limit['bound_catalog_tolerance_checked'] else '限流范围仍按未绑定电阻容差的假设计算；入口侧辅助负载、浪涌和实际温升也未证明。'),c)
    from .regulator_models import analyze as regulator_analyze
    pwm=regulator_analyze(s);findings.extend(pwm['findings'])
    for row in pwm['regulators']:
        c=comps[row['component_id']];f=catalog[c['library_id']]['facts'];vi=rails.get(row['input_net_id']);out=row['output_net_id']
        if out in rails:add('RAIL_DRIVER_CONFLICT','可调电源输出与其它电源重叠。',c)
        else:rails[out]=row['output_interval_v']
        if vi and (vi['min_v']<f['supply_min_v'] or vi['max_v']>f['supply_max_v']):add('BUCK_INPUT_RANGE','可调电源输入范围超出厂家工作范围。',c)
        if vi and vi['min_v']<=row['output_interval_v']['max_v']:add('BUCK_DROPOUT_MARGIN','降压电源输入下界不足以覆盖输出区间；不能当作升压器。',c)
        calcs.append(dict(row,mpn_basis=catalog[c['library_id']]['mpn']))
        add('BUCK_DYNAMIC_THERMAL_UNQUALIFIED','反馈直流区间不覆盖有效电容、布局寄生、纹波、跃变或实际温升。',c)
    for c in active:
        record=bound.get(c['id'],catalog.get(c['library_id']))
        if record and record['evidence_status']!='available':add('POWER_SOURCE_EVIDENCE_CHANGED','供电规则引用的厂家快照缺失或改变。',c)
        if c['library_id']==BUCK:
            f=record['facts'];vi=rails.get(net(c,'IN'));out=net(c,'OUT')
            # Conservative sum of DC specification terms at the reference
            # conditions. Typical ripple/efficiency never become max/min bounds.
            tol=3.3*(f['output_set_accuracy_fraction']+f['line_regulation_fraction']+f['load_regulation_fraction'])
            vo=dict(min_v=3.3-tol,nominal_v=3.3,max_v=3.3+tol,basis='reference_25C_DC_terms_only_not_ripple_or_transient_bound',mpn_basis=record['mpn'])
            if out:
                if out in rails:add('RAIL_DRIVER_CONFLICT','开关模块输出与已知电源重叠，不能覆盖证据。',c)
                else:rails[out]=vo
            if vi and (vi['min_v']<f['supply_min_v'] or vi['max_v']>f['supply_max_v']):add('BUCK_INPUT_RANGE','板端输入范围超出开关电源工作范围，不能忽略线缆和保护器件压降。',c)
            esp_load=sum(catalog[ESP]['facts']['supply_design_capacity_min_a'] for x in active if x['library_id']==ESP and net(x,'VDD')==out)
            aux=sum(vo['max_v']/electrical.number(x['value']) for x in comps.values() if x['library_id']=='Device:R' and electrical.number(x['value']) and out in {assignment.get((x['id'],p['pin_id'])) for p in x['pins']})
            required=esp_load+aux
            if required>f['rated_output_current_a']:add('REGULATOR_CURRENT_CAPACITY','已知最低输出设计容量超过模块额定电流。',c)
            input_lower=(3.0*esp_load/vi['min_v']) if vi else None
            if ctx and input_lower is not None and ctx['available_current_ma']/1000<input_lower:add('SOURCE_CURRENT_BUDGET_LOWER_BOUND','即使假设理想转换，声明的输入电流也低于已知设计需求下界。',c)
            if ctx and ctx['ambient_c']!=25.0:add('BUCK_TEMPERATURE_SCOPE_UNSUPPORTED','当前模块 DC 筛选仅建模 25°C 参考条件；不能外推温度范围。',c)
            calcs.append(dict(component_id=c['id'],mpn_basis=record['mpn'],output_envelope_v=vo,known_minimum_design_capacity_a=required,regulator_capacity_a=f['rated_output_current_a'],known_output_capacity_screen='pass' if required<=f['rated_output_current_a'] else 'fail',input_design_current_lower_bound_a=input_lower,includes_all_loads=False,guaranteed_efficiency_available=False,actual_thermal_proof=False))
            add('BUCK_INPUT_CURRENT_UPPER_BOUND_UNKNOWN','88% 为典型效率，不能用来证明最大输入电流或 USB 预算；全部负载上界仍未知。',c)
            add('BUCK_DYNAMIC_THERMAL_UNQUALIFIED','DC 误差筛选不覆盖纹波、负载跃变、启动/拔插、反灌或实际温升；完整电源资格仍不通过。',c)
            continue
        if c['library_id']!=LDO:continue
        f=record['facts'];vi=rails.get(net(c,'IN'));out=net(c,'OUT')
        # Add line/load terms conservatively rather than treating the advertised
        # headline initial specification as proof of a complete board rail interval.
        tol=f['output_nominal_v']*f['output_accuracy_fraction_full_junction']
        margin=.002+.060*f['rated_output_current_a']
        vo=dict(min_v=3.3-tol-margin,nominal_v=3.3,max_v=3.3+tol+margin,basis='manufacturer_family_accuracy_plus_conservative_line_load_envelope',mpn_basis=record['mpn'])
        if out:
            if out in rails:add('RAIL_DRIVER_CONFLICT','LDO 输出与另一个已知电源重叠，不能覆盖其证据。',c)
            else:rails[out]=vo
        if vi:
            if vi['min_v']<f['supply_min_v'] or vi['max_v']>f['supply_max_v']:add('LDO_INPUT_RANGE','LDO 输入范围超出厂家工作范围。',c)
            if vi['min_v']-vo['max_v']<f['dropout_max_v_at_rated_current']:add('LDO_DROPOUT_MARGIN','输入最小值不足以覆盖输出上界及最大压差。',c)
        esp_load=sum(catalog[ESP]['facts']['supply_design_capacity_min_a'] for x in active if x['library_id']==ESP and net(x,'VDD')==out)
        aux=0.0
        for x in comps.values():
            if x['library_id']!='Device:R':continue
            ns={assignment.get((x['id'],p['pin_id'])) for p in x['pins']};value=electrical.number(x['value'])
            if out in ns and value and value>0:aux+=vo['max_v']/value
        required=esp_load+aux
        if required>f['rated_output_current_a']:add('REGULATOR_CURRENT_CAPACITY','ESP32 所需供电设计容量加上上拉负载，已超过这只 500mA LDO 的容量；还未计传感器和其他负载。',c)
        if ctx and required>ctx['available_current_ma']/1000:add('SOURCE_CURRENT_BUDGET','声明的外部电源电流小于已知最低设计预算。',c)
        power=(max(0,vi['max_v']-vo['min_v'])*required if vi else None)
        temp=ctx['ambient_c']+power*f['theta_ja_jedec_c_per_w'] if ctx and power is not None else None
        calcs.append(dict(component_id=c['id'],mpn_basis=record['mpn'],output_envelope_v=vo,known_minimum_design_capacity_a=required,includes_sensor_load=False,nominal_pullup_load_estimate_a=aux,ldo_capacity_a=f['rated_output_current_a'],reference_dissipation_w=power,reference_junction_estimate_c=temp,theta_ja_basis='JEDEC reference PCB; not actual board thermal resistance',actual_thermal_proof=False))
        if temp is not None and temp>f['max_operating_junction_c']:add('REFERENCE_THERMAL_BUDGET_EXCEEDED','按厂家 JEDEC 参考热阻估算已超过工作结温上限；实际板温升仍需独立验证。',c)
        add('LOAD_AND_THERMAL_PROOF_INCOMPLETE','未取得传感器/全部负载上界、实际铜面积和瞬态/热验证，不能将筛选结果当作整板证明。',c)
    for c in active:
        f=bound.get(c['id'],catalog.get(c['library_id'],{})).get('facts',{})
        if c['library_id'] in [ESP,SENSOR] or f.get('power_budget_model')=='supply_pin_load':
            for pin in f.get('supply_pins',['VDD']):
                v=rails.get(net(c,pin))
                if not v:add('RAIL_ENVELOPE_UNKNOWN',c['ref']+' '+pin+' 缺少已知电源范围。',c)
                elif v['min_v']<f['supply_min_v'] or v['max_v']>f['supply_max_v']:add('SUPPLY_ENVELOPE_OUT_OF_RANGE',c['ref']+' 的最坏情况电源范围超出厂家工作范围。',c)
        if c['library_id']=='Sensor:AM2302':add('SYMBOL_SOURCE_PIN_CONFLICT','本机旧符号把 3 脚与 GND 堆叠；厂家表 4 指定 3=NC、4=GND，需选择 PCB_MCP:AM2302_SIP4。',c)
        if c['library_id']==USB:
            if gate.present(s):add('USB_HARDWARE_TIMING_UNQUALIFIED','已建模 CC 稳态互锁，但上电、源降级、拔插及实际 USB 时序未完成资格验证。',c)
            else:add('USB_CURRENT_CAPABILITY_UNVERIFIED','独立 Rd 只声明受电角色，未证明上游可用电流和枚举/CC 检测；当前不能批准制造。',c)
    for c in comps.values():
        if c['library_id'] not in (gate.CC,gate.INV,gate.SW):continue
        f=catalog[c['library_id']]['facts'];pin='IN' if c['library_id']==gate.SW else ('VCC' if c['library_id']==gate.INV else 'VDD')
        v=rails.get(net(c,pin))
        if not v:add('USB_CONTROL_RAIL_UNKNOWN','USB 控制器件缺少可追溯的条件化供电范围。',c)
        elif v['min_v']<f['supply_min_v'] or v['max_v']>f['supply_max_v']:add('USB_CONTROL_SUPPLY_RANGE','USB 控制器件供电范围超出该具体型号的推荐条件。',c)
    if not active:add('POWER_PROFILE_UNSUPPORTED','本检查尚未覆盖该电路，不把无适用规则当作通过。')
    verdict='fail' if any(f['severity']=='error' for f in findings) else 'pass'
    if not ctx or not active:verdict='incomplete'
    return dict(revision=s['revision'],verdict=verdict,findings=findings,rails=rails,calculations=calcs,evidence_hash=evidence_hash(),qualification='source_family_envelope_diagnostics_not_full_function_or_selected_mpn_qualification')
