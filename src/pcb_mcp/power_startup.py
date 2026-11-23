"""Read-only local and upstream charge screens. Never a startup waveform proof."""
import hashlib,json,math
from pathlib import Path
from . import parts,power_budget,usb_gate_contract as gate
from .common import require
from .editing import spec_hash
from .config import asset_root
ROOT=asset_root()


def reference_rules():
    rule=json.loads((ROOT/'catalog/startup-reference.json').read_text())
    good=all((ROOT/'evidence-cache'/x['file']).is_file() and hashlib.sha256((ROOT/'evidence-cache'/x['file']).read_bytes()).hexdigest()==x['sha256'] for x in rule['source_snapshots'])
    return rule,good


class StartupService:
    def startup_check(self,request):
        with self.locked():
            s=self.load(request.project_id)
            require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            seen=set()
            for item in request.slew_assumptions:
                require(item.net_id in s['nets'] and s['nets'][item.net_id]['kind']=='power','STARTUP_POWER_NET_REQUIRED','斜率声明必须对应真实电源网络。')
                require(item.net_id not in seen,'DUPLICATE_STARTUP_RAIL','同一电源网络不能重复声明。');seen.add(item.net_id)
            require(not request.slew_assumptions or (request.origin is not None and len(request.reason.strip())>=5),'STARTUP_ORIGIN_REQUIRED','斜率必须有明确的假设/用户声明来源与理由；不能伪装成测量。')
            return evaluate(s,self.bom_check_state(s),assumptions=[p.model_dump() for p in request.slew_assumptions],origin=request.origin,reason=request.reason)


def evaluate(s,bom,budget=None,assumptions=None,origin=None,reason=''):
    assumptions=assumptions or [];findings=[];inventory=[];rows=[]
    def add(code,message,severity='warning',**kw):findings.append(dict(code=code,severity=severity,message=message,**kw))
    rule,source_ok=reference_rules()
    if not source_ok:add('STARTUP_SOURCE_CHANGED','启动参考公式/芯片资料快照缺失或改变。','error')
    budget=budget if budget is not None else power_budget.evaluate(s,bom)
    if budget['budget_verdict']!='conditional':add('STARTUP_DC_BUDGET_REQUIRED','声明直流预算尚未完整且自洽；不能据此补算本地电容充电。','error' if budget['budget_verdict']=='fail' else 'warning')
    assignment={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    power_nets={n['id']:n for n in s['nets'].values() if n['kind']=='power'}
    declared={p['net_id']:p['max_slew_v_per_ms'] for p in assumptions}
    for nid in set(declared)-set(power_nets):add('STARTUP_ASSUMPTION_STALE','斜率声明对应的电源网络已失效。','error')
    catalog={p['candidate_id']:p for p in parts.catalog()}
    from .regulator_models import analyze as regulator_analyze
    regulator=regulator_analyze(s)
    feedback_refs={r['feedforward_capacitor'] for r in regulator['regulators']} if not any(f['severity']=='error' for f in regulator['findings']) else set()
    cap_totals={nid:dict(nominal_f=0.,nominal_tolerance_f=0.,references=[],complete=True) for nid in power_nets}
    for c in s['components'].values():
        if c['library_id'] not in ['Device:C','Device:C_Polarized']:continue
        p=s.get('part_bindings',{}).get(c['id']);valid=bool(p and catalog.get(p['candidate_id'])==p and p['evidence_status']=='available' and parts.binding_valid(s,c,p))
        ns=[assignment.get((c['id'],pin['pin_id'])) for pin in c['pins']];unique=set(ns)
        feeds=unique&set(power_nets);grounds={nid for nid in unique if nid in s['nets'] and s['nets'][nid]['kind']=='ground'}
        direct=len(ns)==2 and len(unique)==2 and len(feeds)==1 and len(grounds)==1 and None not in unique
        f=p['facts'] if valid else {};value=f.get('capacitance_f');tol=f.get('tolerance_fraction')
        rated=valid and isinstance(value,(int,float)) and math.isfinite(value) and value>0 and isinstance(tol,(int,float)) and math.isfinite(tol) and 0<=tol<1
        row=dict(reference=c['ref'],component_id=c['id'],mpn=p['mpn'] if p else None,pin_nets=[s['nets'].get(nid,{}).get('name') for nid in ns],source_identity_valid=valid,nominal_uf=value*1e6 if rated else None,nominal_tolerance_max_uf=value*(1+tol)*1e6 if rated else None,effective_capacitance_upper_bound_uf=None,scope='direct_power_to_ground' if direct else 'signal_or_unsupported_connection')
        if not rated:add('STARTUP_CAP_RATING_UNKNOWN',c['ref']+' 缺少有效来源的电容量/容差；不能把它当作零电容。','error' if p else 'warning')
        if direct:
            nid=next(iter(feeds));total=cap_totals[nid];total['references'].append(c['ref'])
            if rated:total['nominal_f']+=value;total['nominal_tolerance_f']+=value*(1+tol)
            else:total['complete']=False
            if c['library_id']=='Device:C_Polarized':
                numbers={pin['number']:assignment.get((c['id'],pin['pin_id'])) for pin in c['pins']}
                if not valid or f.get('pin_mapping')!={'1':'positive','2':'negative'}:add('STARTUP_CAP_POLARITY_UNKNOWN',c['ref']+' 缺少可用极性映射。');total['complete']=False
                elif numbers.get('1')!=nid or numbers.get('2') not in grounds:add('STARTUP_CAP_REVERSED',c['ref']+' 极性电容正负脚连接反向。','error');total['complete']=False
        else:
            if valid and c['ref'] in feedback_refs:
                row['scope']='source_modelled_feedback_capacitor'
                row['coupled_charge_included']=False
                add('STARTUP_FEEDBACK_CAP_COUPLED',c['ref']+' 是来源模型确认的反馈电容；不属于对地并联电容，其耦合充电不在本地参考式内。',reference=c['ref'])
            elif feeds:
                add('STARTUP_CAP_TOPOLOGY_UNSUPPORTED',c['ref']+' 不是单一电源对地电容，不能归入并联充电模型。','error')
                for nid in feeds:cap_totals[nid]['complete']=False
            elif None in unique:add('STARTUP_CAP_UNCONNECTED',c['ref']+' 存在未连接引脚。','error')
            else:add('STARTUP_SIGNAL_CAP_EXCLUDED',c['ref']+' 位于信号网络，未纳入电源并联电容模型；复位/信号延时仍未验证。',reference=c['ref'])
        inventory.append(row)
    for f in bom['findings']:
        if f['severity']=='error':add('STARTUP_BOM_CONFLICT',f['message'],'error',original_code=f['code'])
    rail_info={r['net_id']:r for r in budget['rails']};capacities={}
    for stage in budget['stages']:
        nid=next((nid for nid,n in power_nets.items() if n['name']==stage['output_net']),None)
        if nid in capacities:add('STARTUP_DRIVER_CONFLICT','多个供电级驱动同一电源网络。','error')
        if nid:capacities[nid]=dict(current_a=stage['output_capacity_reference_a'],basis='selected_stage_'+stage['kind'],reference=stage['reference'])
    boundary=s.get('power_envelope',{}).get('positive_pin',{})
    source_nid=assignment.get((boundary.get('component_id'),boundary.get('pin_id')))
    if source_nid and s.get('power_envelope'):capacities[source_nid]=dict(current_a=s['power_envelope']['available_current_ma']/1000,basis='declared_input_capacity_not_CC_measurement',reference='external_source')
    calculated=0
    for nid,n in power_nets.items():
        total=cap_totals[nid];rail=rail_info.get(nid,{});voltage=rail.get('supply_reference');capacity=capacities.get(nid,{}).get('current_a');dc=rail.get('declared_dc_reference_a') if budget['budget_verdict']=='conditional' else None;slew=declared.get(nid)
        row=dict(net_id=nid,name=n['name'],initial_voltage_reference_v=0.0,capacitor_references=sorted(total['references']),nominal_capacitance_uf=total['nominal_f']*1e6,nominal_tolerance_capacitance_uf=total['nominal_tolerance_f']*1e6,capacitor_inventory_complete=total['complete'],effective_capacitance_upper_bound_uf=None,voltage_reference_v=voltage,declared_dc_baseline_a=dc,capacity_reference=capacities.get(nid),max_slew_assumption_v_per_ms=slew,capacitor_charge_reference_c=None,local_capacitor_current_reference_a=None,dc_plus_local_cap_reference_a=None,current_margin_reference_a=None,max_allowed_slew_reference_v_per_ms=None,linear_full_rise_time_reference_floor_ms=None,screen='incomplete')
        if total['complete'] and voltage:
            row['capacitor_charge_reference_c']=total['nominal_tolerance_f']*voltage['max_v']
        if slew is None:add('STARTUP_SLEW_MISSING',n['name']+' 没有最大瞬时上升斜率声明；不拿平均或典型上升时间冒充峰值斜率上界。')
        if slew is not None and voltage and capacity is not None and dc is not None and total['complete']:
            cap_i=total['nominal_tolerance_f']*slew*1000;combined=dc+cap_i;margin=capacity-combined;headroom=capacity-dc;calculated+=1
            row.update(local_capacitor_current_reference_a=cap_i,dc_plus_local_cap_reference_a=combined,current_margin_reference_a=margin,screen='within_declared_local_cap_reference' if margin>=0 else 'exceeds_declared_local_cap_reference')
            if total['nominal_tolerance_f']>0 and headroom>0:
                max_slew=headroom/(total['nominal_tolerance_f']*1000);row.update(max_allowed_slew_reference_v_per_ms=max_slew,linear_full_rise_time_reference_floor_ms=voltage['max_v']/max_slew)
            if margin<0:add('STARTUP_REFERENCE_CURRENT_EXCEEDED',n['name']+' 的直流声明加本地电容充电参考值超过容量；该声明组合无法保持所设斜率，可能进入限流或变慢。','error',net_id=nid)
        elif slew is not None:add('STARTUP_RAIL_REFERENCE_UNKNOWN',n['name']+' 缺少本地电容、直流负载、电压或容量依据。')
        rows.append(row)
    add('STARTUP_EFFECTIVE_CAPACITANCE_UNKNOWN','使用标称值加初始容差的常电容参考模型；偏压/温度/老化/频率下的实际电容量及浪涌波形尚未验证。')
    if any(c['library_id']==gate.SW for c in s['components'].values()):
        add('STARTUP_SLEW_NOT_DATASHEET_GUARANTEE','TPS2553上升时间表没有最小值，测试电容/负载也不同；没有据此生成保证的最大瞬时斜率。')
    else:
        add('STARTUP_SLEW_NOT_DATASHEET_GUARANTEE','当前电路没有经过核验的瞬时上升斜率上界；声明斜率仍是条件化充电计算输入，不能代替器件或整板启动波形。')
    from .startup_transfer import evaluate as transfer_evaluate
    transfer=transfer_evaluate(rows,budget['stages'],source_nid,budget['budget_verdict']=='conditional' and source_ok and not any(f['severity']=='error' for f in findings))
    findings.extend(transfer['findings'])
    add('STARTUP_COUPLED_DYNAMICS_UNQUALIFIED','下游对地电容充电已按声明效率和使能电压范围折算到上游；不覆盖从0V启动、控制环/限流动态、反馈电容、入口跌落或复位/USB时序；结果不是实测峰值或启动时间保证。')
    if assumptions:add('STARTUP_SCENARIO_ASSUMED','最大斜率是可核对的外部假设/用户声明，未证明实物满足；本次只读分析不改变规格或任何批准。')
    errors=any(f['severity']=='error' for f in findings)
    valid_inventory=bool(inventory) and all(r['source_identity_valid'] and r['nominal_uf'] is not None for r in inventory)
    conditional=bool(power_nets) and calculated==len(power_nets) and valid_inventory and source_ok and budget['budget_verdict']=='conditional' and transfer['verdict']=='conditional' and not errors
    evidence=hashlib.sha256((parts.rules_hash()+json.dumps(rule,sort_keys=True)).encode()).hexdigest()
    scenario=dict(slew_assumptions=assumptions,origin=origin,reason=reason,baseline='inherited_declared_enabled_DC_budget_plus_transferred_nominal_tolerance_charge',transfer_operating_condition=transfer['operating_condition'])
    return dict(project_id=s['id'],revision=s['revision'],connectivity_basis='current_structured_spec_not_new_KiCad_netlist',verdict='fail' if errors else 'incomplete',reference_slew_verdict='fail' if errors else 'conditional' if conditional else 'incomplete',capacitors=inventory,rails=rows,charge_transfer=transfer,findings=findings,coverage=dict(capacitors_found=len(inventory),direct_power_capacitors=sum(r['scope']=='direct_power_to_ground' for r in inventory),signal_or_other_capacitors=sum(r['scope']!='direct_power_to_ground' for r in inventory),declared_slew_rails=len(declared),required_rails=len(power_nets),calculated_rails=calculated,full_startup_qualified=False),scenario=scenario,scenario_sha256=hashlib.sha256(json.dumps([spec_hash(s),scenario,evidence],sort_keys=True).encode()).hexdigest(),evidence_hash=evidence,sources=rule['source_urls'],actual_peak_input_current_upper_bound_a=None,actual_startup_time_upper_bound_ms=None,physical_qualified=False,manufacturing_approved=False,qualification='declared_local_and_quasi_static_transferred_charge_not_complete_startup_or_USB_qualification')
