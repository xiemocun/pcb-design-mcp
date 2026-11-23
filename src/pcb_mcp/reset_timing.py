"""Conservative unloaded EN RC reference, never measured reset qualification."""
import hashlib,json,math
from pathlib import Path
from . import parts,power_budget
from .common import require
from .editing import spec_hash
from .config import asset_root
ROOT=asset_root()
ESP='RF_Module:ESP32-WROOM-32E'


def reference_rules():
    rule=json.loads((ROOT/'catalog/reset-reference.json').read_text());source=rule['source_snapshot'];p=ROOT/'evidence-cache'/source['file']
    return rule,p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==source['sha256']


def en_voltage(t_ms,tau_ms,rise_ms,final_v,initial_v):
    """Exact first-order constant RC solution for a linear ramp then plateau."""
    t=max(0.,t_ms)
    if rise_ms==0:return final_v+(initial_v-final_v)*math.exp(-t/tau_ms)
    ramp_t=min(t,rise_ms);x=ramp_t/tau_ms
    # x+expm1(-x) loses digits for extremely fast ramps: use its series.
    area=(x*x*(.5-x/6+x*x/24-x*x*x/120)) if x<1e-3 else x+math.expm1(-x)
    v=initial_v*math.exp(-x)+final_v*tau_ms/rise_ms*area
    return v if t<=rise_ms else final_v+(v-final_v)*math.exp(-(t-rise_ms)/tau_ms)


def crossing_ms(threshold,tau_ms,rise_ms,final_v,initial_v):
    if not all(math.isfinite(v) for v in (threshold,tau_ms,rise_ms,final_v,initial_v)) or tau_ms<=0 or rise_ms<0:
        raise ValueError('Invalid finite RC/ramp parameters')
    if initial_v>=threshold:return 0.
    if threshold>=final_v:return None  # Plateau equality is approached asymptotically.
    lo=0.;hi=rise_ms+tau_ms
    while en_voltage(hi,tau_ms,rise_ms,final_v,initial_v)<threshold:hi=rise_ms+2*(hi-rise_ms)
    for _ in range(90):
        mid=(lo+hi)/2
        if en_voltage(mid,tau_ms,rise_ms,final_v,initial_v)>=threshold:hi=mid
        else:lo=mid
    return hi


class ResetService:
    def reset_check(self,request):
        with self.locked():
            s=self.load(request.project_id)
            require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            scenario=request.scenario.model_dump() if request.scenario else None
            if scenario:
                require(scenario['supply_stable_at_ms']>=scenario['supply_rise_ms'],'RESET_STABILITY_BEFORE_RAMP_END','本参考模型中，稳定时刻不能早于线性电源爬升结束。')
                require(request.origin is not None and len(request.reason.strip())>=5,'RESET_SCENARIO_ORIGIN_REQUIRED','波形与初始电压必须明确标为假设/用户声明，并提供理由。')
            return evaluate(s,self.bom_check_state(s),scenario=scenario,origin=request.origin,reason=request.reason)


def evaluate(s,bom,budget=None,scenario=None,origin=None,reason=''):
    findings=[];corners=[];topology={};calculation=None
    def add(code,message,severity='warning',**kw):findings.append(dict(code=code,severity=severity,message=message,**kw))
    rule,source_ok=reference_rules()
    if not source_ok:add('RESET_REFERENCE_SOURCE_CHANGED','复位门限/时序的原厂摘要缺失或改变。','error')
    budget=budget if budget is not None else power_budget.evaluate(s,bom)
    if budget['budget_verdict']!='conditional':add('RESET_DC_REFERENCE_REQUIRED','已有供电/负载声明尚未完整且自洽，不能计算有依据的复位参考。','error' if budget['budget_verdict']=='fail' else 'warning')
    modules=[c for c in s['components'].values() if c['library_id']==ESP]
    assignment={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    def net(c,name):return next((assignment.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
    def pair(c):return {assignment.get((c['id'],p['pin_id'])) for p in c['pins']}
    catalog={p['candidate_id']:p for p in parts.catalog()}
    def binding(c):
        p=s.get('part_bindings',{}).get(c['id'])
        if p and catalog.get(p['candidate_id'])==p and p['evidence_status']=='available' and parts.binding_valid(s,c,p):return p
        add('RESET_PART_UNRESOLVED',c['ref']+' 缺少当前有效的型号/数值/封装与资料。','error' if p else 'warning');return None
    if len(modules)!=1:add('RESET_PROFILE_UNSUPPORTED','本模型只覆盖唯一ESP32-WROOM-32E的EN上拉RC。','error')
    else:
        esp=modules[0];ep=binding(esp);en=net(esp,'EN');vdd=net(esp,'VDD');gnd=net(esp,'GND')
        if not any(p['name']=='EN' and p['number']==rule['en_pin_number'] for p in esp['pins']):add('RESET_EN_PIN_CONFLICT','模块EN引脚与所记载的真实封装引脚不一致。','error')
        if ep and ep['mpn']!=rule['module_mpn']:add('RESET_MPN_UNSUPPORTED','门限规则未覆盖该绑定型号。','error')
        separate=None not in [en,vdd,gnd] and len({en,vdd,gnd})==3
        if not separate or s['nets'].get(vdd,{}).get('kind')!='power' or s['nets'].get(gnd,{}).get('kind')!='ground':add('RESET_RAIL_TOPOLOGY','EN、模块供电和地必须连接并互相分离。','error')
        resistors=[c for c in s['components'].values() if c['library_id']=='Device:R' and separate and pair(c)=={en,vdd}]
        capacitors=[c for c in s['components'].values() if c['library_id']=='Device:C' and separate and pair(c)=={en,gnd}]
        if len(resistors)!=1:add('RESET_PULLUP_REQUIRED','要求唯一的EN到该模块VDD上拉电阻；不支持并联等效或其他电源上拉。','error')
        if len(capacitors)!=1:add('RESET_CAP_REQUIRED','要求唯一的EN到该模块地的非极性电容。','error')
        topology=dict(module=esp['ref'],en_net=s['nets'].get(en,{}).get('name'),vdd_net=s['nets'].get(vdd,{}).get('name'),ground_net=s['nets'].get(gnd,{}).get('name'),pullup_references=[c['ref'] for c in resistors],capacitor_references=[c['ref'] for c in capacitors],external_ports=[])
        if len(resistors)==len(capacitors)==1:
            resistor,cap=resistors[0],capacitors[0];rp=binding(resistor);cp=binding(cap)
            allowed={esp['id'],resistor['id'],cap['id']}
            for c in s['components'].values():
                if c['id'] in allowed or en not in pair(c):continue
                if c['library_id']=='Connector_Generic:Conn_01x06':
                    binding(c);topology['external_ports'].append(c['ref'])
                else:add('RESET_ADDITIONAL_LOAD_UNSUPPORTED',c['ref']+' 额外接入EN，不能假定为无负载RC。','error')
            if topology['external_ports'] and (not scenario or scenario.get('external_en_ports')!='assumed_disconnected'):add('RESET_EXTERNAL_PORT_UNKNOWN','EN接出编程排针；外部驱动/电容未知，不能静默忽略。')
            if rp and cp:
                rf=rp['facts'];cf=cp['facts'];r=rf.get('value_ohm');c=cf.get('capacitance_f');rt=rf.get('tolerance_fraction');ct=cf.get('tolerance_fraction')
                rated=all(isinstance(v,(int,float)) and math.isfinite(v) for v in [r,c,rt,ct]) and r>0 and c>0 and 0<=rt<1 and 0<=ct<1
                if not rated:add('RESET_RC_RATING_UNKNOWN','缺少有效的RC标称值与初始容差。')
                else:
                    tau_lo=r*(1-rt)*c*(1-ct)*1000;tau_hi=r*(1+rt)*c*(1+ct)*1000
                    calculation=dict(resistor_mpn=rp['mpn'],capacitor_mpn=cp['mpn'],resistance_nominal_ohm=r,capacitance_nominal_f=c,tau_nominal_ms=r*c*1000,tau_initial_tolerance_reference_ms=[tau_lo,tau_hi],effective_capacitance_bounds_f=None,reset_low_max_reference_v=rule['guaranteed_low_max_reference_v'],high_fraction_reference=rule['high_level_fraction_reference'],minimum_delay_reference_us=rule['minimum_stable_to_activation_reference_us'],reference_model='Unloaded constant RC; initial tolerance only; no temperature/bias/aging/leakage/internal pull or actual waveform proof')
                    if scenario:
                        rails={row['net_id']:row for row in budget['rails']};voltage=rails.get(vdd,{}).get('supply_reference')
                        if not voltage or budget['budget_verdict']!='conditional':add('RESET_SUPPLY_RANGE_UNKNOWN','缺少当前有效的模块供电参考范围。')
                        elif s.get('power_envelope',{}).get('ambient_c')!=rule['reference_ambient_c']:add('RESET_TEMPERATURE_SCOPE','复位门限参考来自3.3V、25°C特性表，不能推广实物温区。','error')
                        else:
                            deadline=scenario['supply_stable_at_ms']+rule['minimum_stable_to_activation_reference_us']/1000
                            for tau in [tau_lo,tau_hi]:
                                for final in [voltage['min_v'],voltage['max_v']]:
                                    endpoint=en_voltage(deadline,tau,scenario['supply_rise_ms'],final,scenario['initial_en_v']);maximum=max(scenario['initial_en_v'],endpoint)
                                    corners.append(dict(tau_ms=tau,supply_final_reference_v=final,en_at_stable_plus_delay_v=endpoint,max_en_through_deadline_v=maximum,hold_low_margin_reference_v=rule['guaranteed_low_max_reference_v']-maximum,first_to_0p6v_reference_ms=crossing_ms(rule['guaranteed_low_max_reference_v'],tau,scenario['supply_rise_ms'],final,scenario['initial_en_v']),first_to_final_supply_75pct_reference_ms=crossing_ms(rule['high_level_fraction_reference']*final,tau,scenario['supply_rise_ms'],final,scenario['initial_en_v'])))
                            envelope=en_voltage(deadline,tau_lo,scenario['supply_rise_ms'],voltage['max_v'],0.)+scenario['initial_en_v']
                            calculation.update(check_deadline_ms=deadline,conservative_max_en_through_deadline_reference_v=envelope,conservative_low_hold_margin_reference_v=rule['guaranteed_low_max_reference_v']-envelope,initial_residual_bound='No decay credited to initial residual; forced zero-initial response uses fastestRC and highest supply')
                            if envelope>=rule['guaranteed_low_max_reference_v']:add('RESET_LOW_HOLD_NOT_ESTABLISHED','保守上包络无法证明EN保持低电平直到电源稳定+50µs；这不是实测失效结论，也不能用75%门限的较晚到达时间冒充最早释放保证。','error')
    for f in bom['findings']:
        if f['severity']=='error':add('RESET_BOM_CONFLICT',f['message'],'error',original_code=f['code'])
    if not scenario:add('RESET_SUPPLY_WAVEFORM_MISSING','未声明电源爬升、稳定时刻及EN初始电压；RC时间常数不是上电资格证明。')
    add('RESET_UNLOADED_REFERENCE_ONLY','RC计算假设无外接负载、无内部上拉/泄漏及常电容量；实际偏压/温漂/预充电/外部编程器尚未验证。')
    add('RESET_RELEASE_THRESHOLD_UNSPECIFIED','0.6V低电平保证到0.75×VDD高电平保证之间没有精确翻转门限；75%到达时间只作参考。')
    add('RESET_POWER_CYCLE_UNQUALIFIED','尚未验证实际电源爬升、慢/重复上下电、复位脉冲、过冲/跌落、棕断或外部自动下载时序；制造资格仍不通过。')
    errors=any(f['severity']=='error' for f in findings)
    allowed_warnings={'RESET_UNLOADED_REFERENCE_ONLY','RESET_RELEASE_THRESHOLD_UNSPECIFIED','RESET_POWER_CYCLE_UNQUALIFIED'}
    conditional=bool(corners) and source_ok and not errors and not any(f['code'] not in allowed_warnings for f in findings)
    evidence=hashlib.sha256((parts.rules_hash()+json.dumps(rule,sort_keys=True)).encode()).hexdigest();declared=dict(parameters=scenario,origin=origin,reason=reason)
    return dict(project_id=s['id'],revision=s['revision'],connectivity_basis='current_structured_spec_not_new_KiCad_netlist',verdict='fail' if errors else 'incomplete',reference_timing_verdict='fail' if errors else 'conditional' if conditional else 'incomplete',topology=topology,calculation=calculation,corners=corners,findings=findings,scenario=declared,scenario_sha256=hashlib.sha256(json.dumps([spec_hash(s),declared,evidence],sort_keys=True).encode()).hexdigest(),evidence_hash=evidence,sources=rule['source_urls'],actual_reset_release_time_bounds_ms=None,physical_qualified=False,manufacturing_approved=False,qualification='initial_tolerance_unloaded_RC_reference_not_physical_reset_or_startup_qualification')
