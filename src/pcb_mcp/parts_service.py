"""Source-grounded part selection and a deliberately narrow DC stress check."""
import copy,csv,io,json,math
from . import backend,parts,electrical
from .common import require,write_json

class PartsService:
    def parts_search(self,request):
        with self.locked():
            q=request.query.casefold().split();rows=[p for p in parts.catalog() if (not request.kind or p['kind']==request.kind) and all(w in json.dumps(p,ensure_ascii=False).casefold() for w in q)]
            exact=[p for p in rows if p['mpn'].casefold()==request.query.casefold()];rows=exact or rows
            path=self.root/'part_candidates.json';ledger=json.loads(path.read_text()) if path.exists() else {}
            for p in rows:ledger.setdefault(p['candidate_id'],dict(definition_hash=p['definition_hash'],resolved=False))
            write_json(path,ledger)
            return dict(candidates=rows,total=len(rows),scope='Source-linked identity records; stock/price and physical qualification not checked')

    def part_candidate(self,cid,resolved=False):
        path=self.root/'part_candidates.json';ledger=json.loads(path.read_text()) if path.exists() else {}
        require(cid in ledger,'UNKNOWN_PART','先查询受管器件目录。')
        row=next((p for p in parts.catalog() if p['candidate_id']==cid),None)
        require(row is not None and row['definition_hash']==ledger[cid]['definition_hash'],'PART_CATALOG_CHANGED','器件资料或封装定义已变化，需重新查询。')
        if resolved:require(ledger[cid]['resolved'],'PART_NOT_RESOLVED','先读取器件的来源、引脚映射和封装约束。')
        return row,ledger,path

    def part_get(self,request):
        with self.locked():
            p,ledger,path=self.part_candidate(request.candidate_id);ledger[request.candidate_id]['resolved']=True;write_json(path,ledger);return p

    def part_binding_valid(self,s,c,p):
        return parts.binding_valid(s,c,p)

    def part_bind(self,request):
        def change(s):
            require(request.component_id in s['components'],'UNKNOWN_COMPONENT','元件不存在。')
            p,_,_=self.part_candidate(request.candidate_id,True);require(p['evidence_status']=='available','PART_EVIDENCE_UNAVAILABLE','厂家资料快照缺失或哈希改变。')
            c=s['components'][request.component_id]
            require(self.part_binding_valid(s,c,p),'PART_BINDING_MISMATCH','实物型号必须匹配符号、数值和目录指定的真实封装；先选择正确封装。')
            s['part_bindings'][c['id']]=copy.deepcopy(p);return dict(entity_id=c['id'])
        return self.mutate('part_bind',request,change)

    def part_unbind(self,request):
        def change(s):
            require(request.component_id in s['part_bindings'],'UNKNOWN_PART_BINDING','该元件没有实物器件绑定。')
            del s['part_bindings'][request.component_id];return dict(entity_id=request.component_id)
        return self.mutate('part_unbind',request,change)

    def conditions_set(self,request):
        def change(s):
            require(request.min_v<=request.nominal_v<=request.max_v,'INVALID_SUPPLY_RANGE','供电最小值、标称值、最大值顺序不正确。')
            cond=request.model_dump(exclude={'project_id','expected_revision','idempotency_key'})
            require(self.source_valid(s,dict(boundary_pin=cond['positive_pin'],net_id=next((n['id'] for n in s['nets'].values() if cond['positive_pin'] in n['pins']),''),ground_net_id=cond['ground_net_id'],nominal_v=cond['nominal_v'])),'INVALID_POWER_BOUNDARY','运行条件必须指向真实且已连接的外部电源入口。')
            s['operating_conditions']=cond;return dict(entity_id=s['id'])
        return self.mutate('operating_conditions_set',request,change)

    def engineering_state(self,s):
        # Source-bound range models must also work for a three-component
        # branch. Component count is not an electrical model selection rule.
        from . import verification_models
        if s.get('power_envelope') and any(m['engine']=='dc_limits' and
                m['part_definition_hash']==p['definition_hash']
                for m,_ in verification_models.packages().values()
                for p in s.get('part_bindings',{}).values() if p['kind']=='led'):
            from .led_branches import evaluate
            return evaluate(self,s)
        if s['profile']!='led_series_experiment' and len(s['components'])!=3:
            from .led_branches import evaluate
            return evaluate(self,s)
        findings=[];calcs={};evidence_hash=parts.rules_hash()
        def add(code,message,severity='error'):findings.append(dict(code=code,severity=severity,message=message))
        cond=s.get('operating_conditions',{})
        if not cond:add('OPERATING_CONDITIONS_MISSING','需明确电源范围、电流预算、环境温度及其来源。')
        current={p['candidate_id']:p for p in parts.catalog()};kinds={}
        for c in s['components'].values():
            p=s.get('part_bindings',{}).get(c['id'])
            if not p:add('PART_BINDING_MISSING',c['ref']+' 缺少来源明确的实物器件绑定。');continue
            fresh=current.get(p['candidate_id'])
            if fresh!=p or p['evidence_status']!='available':add('PART_EVIDENCE_CHANGED',c['ref']+' 的资料或目录已变化。');continue
            if not self.part_binding_valid(s,c,p):add('PART_BINDING_MISMATCH',c['ref']+' 的数值/符号/封装不再匹配实物。')
            kinds.setdefault(p['kind'],[]).append((c,p))
        complete=len(s['components'])==3 and all(len(kinds.get(k,[]))==1 for k in ['connector','resistor','led'])
        if not complete:add('ENGINEERING_PROFILE_UNSUPPORTED','当前实物电气检查只支持一个连接器、一个电阻和一个 LED 的串联直流电路。')
        if cond and complete and not any(f['severity']=='error' for f in findings):
            (j,jp),(r,rp),(d,dp)=[kinds[k][0] for k in ['connector','resistor','led']]
            assigned={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
            def pin(c,num):return dict(component_id=c['id'],pin_id=next(p['pin_id'] for p in c['pins'] if p['number']==num))
            def net(c,num):p=pin(c,num);return assigned.get((p['component_id'],p['pin_id']))
            vp=net(j,'1');gnd=net(j,'2');a=net(d,'2')
            topology=len(s['nets'])==3 and None not in [vp,gnd,a] and len({vp,gnd,a})==3 and net(d,'1')==gnd and {net(r,'1'),net(r,'2')}=={vp,a} and cond['positive_pin']==pin(j,'1') and cond['ground_net_id']==gnd
            if not topology:add('LED_POLARITY_OR_TOPOLOGY','必须是接口1脚→电阻→LED阳极，LED阴极→接口2脚/参考地。')
            source=next((p for p in s['power_sources'].values() if p['boundary_pin']==cond['positive_pin'] and p['ground_net_id']==gnd and p['nominal_v']==cond['nominal_v'] and self.source_valid(s,p)),None)
            if not source:add('POWER_DECLARATION_REQUIRED','需要与运行条件一致的显式外部电源声明。')
            if cond['ambient_c']!=25.0:add('AMBIENT_PROFILE_UNSUPPORTED','本检查仅在厂家标定的 25°C 环境下计算；其他温度需要独立降额规则。')
            rf=rp['facts'];df=dp['facts'];temperature_span=rf['temperature_range_c'][1]-rf['temperature_range_c'][0];tcr_envelope=rf['temperature_coefficient_ppm_per_k']*1e-6*temperature_span
            rmin=rf['value_ohm']*(1-rf['tolerance_fraction'])*(1-tcr_envelope);rmax=rf['value_ohm']*(1+rf['tolerance_fraction'])*(1+tcr_envelope);vmax=cond['max_v']
            imax=vmax/rmin;pr=vmax*vmax/rmin;pd=vmax*vmax/(4*rmin)
            calcs=dict(tcr_fraction_envelope=tcr_envelope,resistor_temperature_span_c=temperature_span,resistance_min_ohm=rmin,resistance_max_ohm=rmax,dc_current_upper_bound_a=imax,resistor_power_upper_bound_w=pr,led_power_upper_bound_w=pd,nominal_led_current_estimate_a=max(0,(cond['nominal_v']-df['vf_typ_v'])/rf['value_ohm']),estimate_is_guaranteed=False,assumption='Passive forward LED, no other loads, steady DC, 25C; Vf lower bound conservatively zero. LED power bounded by maximum power transfer. Resistor envelope includes tolerance and TCR across its full specified temperature span, conditional on permitted film temperature. Aging, fault/transient/reverse-supply and brightness are not guaranteed.',current_policy_limit_a=.5*df['dc_current_abs_max_a'],led_power_policy_limit_w=.5*df['power_abs_max_w'],resistor_power_policy_limit_w=.5*rf['rated_power_w'])
            if imax>calcs['current_policy_limit_a']:add('LED_CURRENT_MARGIN','电流上界超过本版 50% 绝对最大额定电流策略。')
            if pd>calcs['led_power_policy_limit_w']:add('LED_POWER_MARGIN','LED 功耗上界超过本版降额策略。')
            if pr>calcs['resistor_power_policy_limit_w']:add('RESISTOR_POWER_MARGIN','电阻功耗上界超过本版降额策略。')
            if imax>jp['facts']['rated_current_a']:add('CONNECTOR_CURRENT_RATING','接口电流额定值不足。')
            if imax>cond['available_current_ma']/1000:add('SOURCE_CURRENT_BUDGET','声明的供电电流预算小于保守负载上界。')
        for code,message in [('ASSEMBLY_REVIEW_REQUIRED','需人工核对实物、封装公差、LED极性及未防呆接口接线；尚未实物试装。'),('BRIGHTNESS_NOT_VERIFIED','此工作电流下的亮度未由厂家资料保证。'),('THERMAL_AND_TRANSIENT_UNVERIFIED','功耗降额是窄范围筛选策略；未验证实际温升、老化漂移、异常反接或瞬态。')]:add(code,message,'warning')
        if cond and cond['origin']=='proposed_assumption':add('CONDITIONS_NEED_HUMAN_CONFIRMATION','运行条件目前为方案假设，需人工确认实际电源与环境。','warning')
        verdict='fail' if any(f['severity']=='error' for f in findings) else 'pass'
        if not cond or not s.get('part_bindings'):verdict='incomplete'
        return dict(revision=s['revision'],verdict=verdict,findings=findings,calculations=calcs,evidence_hash=evidence_hash,coverage=[dict(check_id='source_linked_mpn_and_nominal_land_pattern',status='checked' if complete else 'incomplete'),dict(check_id='restricted_dc_stress_bounds',status='checked' if calcs else 'incomplete'),dict(check_id='physical_assembly_and_function',status='requires_human_review')],qualification='restricted_source_linked_prototype_check_not_production_qualification')

    def engineering_check(self,request):
        with self.locked():
            s=self.load(request.project_id);require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。');return self.engineering_state(s)

    def bom_state(self,s):
        rows=[]
        for c in sorted(s['components'].values(),key=lambda c:c['ref']):
            p=s['part_bindings'].get(c['id']);require(p is not None,'BOM_PART_MISSING',c['ref']+' 缺少实物器件。')
            fresh,_,_=self.part_candidate(p['candidate_id'],True)
            require(fresh==p and self.part_binding_valid(s,c,p) and p['evidence_status']=='available','PART_EVIDENCE_CHANGED','BOM 器件证据已变化。')
            sku=p['skus'][0] if p['skus'] else {}
            rows.append(dict(reference=c['ref'],quantity=1,value=c['value'],manufacturer=p['manufacturer'],mpn=p['mpn'],footprint=p['footprint_id'],supplier=sku.get('supplier',''),supplier_sku=sku.get('sku',''),supplier_url=sku.get('url',''),datasheet_url=p['sources'][0]['url'],part_evidence_hash=p['definition_hash'],qualification='source_linked_not_human_qualified',availability='not_checked',supplier_selection='recorded_static_sku' if sku else 'not_selected'))
        return rows

    def bom_export(self,request):
        with self.locked():
            s=self.load(request.project_id);require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。');rows=self.bom_state(s)
            require(rows,'EMPTY_BOM','没有器件。');folder=self.project_path(s['id'])/'exports';folder.mkdir(exist_ok=True)
            stem='bom-r'+str(s['revision']);path=folder/(stem+'.json');write_json(path,rows)
            out=io.StringIO();writer=csv.DictWriter(out,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows);csvpath=folder/(stem+'.csv');csvpath.write_text(out.getvalue())
            return dict(revision=s['revision'],rows=rows,json_path=str(path),csv_path=str(csvpath),sha256=backend.digest(path.read_bytes()),qualification='source_linked_bom_not_live_procurement_or_manufacturing_approval')
