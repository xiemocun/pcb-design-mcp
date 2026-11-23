"""Physical layout and consolidated readiness gates for explicit prototypes."""
import json,math
from . import backend,electrical,power_integrity
from .common import require,DomainError,write_json
from .pcb_service import worker
from .models import PCBRef
from .placement_rules import ESP,SENSOR,LDO,USB,TXS,BUCK,CC,INV,SW
from .power_stage_roles import PWM_BUCK
from .pin_roles import ground_names

def layout_report(s,actual,pcb_id,stage):
    findings=[];measurements=[];byref=actual['components'];assigned={(p['component_id'],p['pin_id']):n['name'] for n in s['nets'].values() for p in n['pins']}
    def add(code,msg,c=None):findings.append(dict(code=code,severity='error',message=msg,entity_ids=[c['id']] if c else []))
    def net(c,name):return next((assigned.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
    def intersect(a,b):return a[0]<b[2]-1e-6 and a[2]>b[0]+1e-6 and a[1]<b[3]-1e-6 and a[3]>b[1]+1e-6
    for c in s['components'].values():
        if c['library_id']!=ESP:continue
        fp=byref[c['ref']];zones=fp.get('rule_areas',[])
        keep=[z for z in zones if z['all_copper'] and all(z[k] for k in ['no_tracks','no_vias','no_pads','no_fills','no_footprints'])]
        if fp.get('angle_deg')!=0 or len(keep)!=1:add('RF_KEEPOUT_MISSING','ESP32 需要方向受控且覆盖双面铜、走线、过孔、焊盘和器件的厂家来源禁布区。',c)
        else:
            x,y=fp['position_mm'];expected=[x-24,y-27.75,x+24,y-6.56];area=keep[0]['bbox_mm']
            if any(abs(a-b)>1e-5 for a,b in zip(area,expected)):add('RF_KEEPOUT_CHANGED','实际天线禁布区与选定库的受管矩形不一致。',c)
            for ref,item in byref.items():
                if ref!=c['ref'] and intersect(area,item['bbox_mm']):add('RF_COMPONENT_INTRUSION',ref+' 的外形/焊盘侵入天线禁布区域。',c)
            for item in actual.get('copper_items',[]):
                if intersect(area,item['bbox_mm']):add('RF_COPPER_INTRUSION','实际铜线或过孔侵入天线禁布区。',c);break
            if actual.get('copper_zone_count',0):
                proof=actual.get('ground_planes',{})
                checks=[x for x in proof.get('antenna_keepout_checks',[]) if x.get('ref')==c['ref']]
                from .stackup import copper_layers
                layers=copper_layers(s['board_constraints'])
                valid=proof.get('supported') and proof.get('status')=='pass' and proof.get('native_refill_matches_saved') and len(checks)==len(layers) and {x.get('layer') for x in checks}==set(layers) and all(x.get('expected_outline_sha256') and x.get('copper_overlap_polygons')==0 and x.get('copper_overlap_area_mm2')==0 for x in checks)
                if not valid:add('RF_COPPER_ZONE_PROOF_UNSUPPORTED','缺少当前实际铜区的双面天线禁区几何证明，或证明失败。',c)
                else:measurements.append(dict(check='rf_filled_copper_exclusion',reference=c['ref'],native_checks=checks))
            measurements.append(dict(check='rf_keepout',reference=c['ref'],bbox_mm=area,source='KiCad ESP32-WROOM-32E footprint; Espressif placement guide',checked_layers=['F.Cu','B.Cu']))
    from . import parts
    source_parts={p['symbol_id']:p['facts'] for p in parts.reference_catalog()}
    source_bound_loads=parts.bound_supply_models(s)
    if not any(c['library_id']==ESP for c in s['components'].values()):
        dims=s['board_constraints'];measurements.append(dict(check='generic_native_geometry',components=len(byref),clearance_authority='separate KiCad courtyard/copper DRC'))
        for c in s['components'].values():
            item=byref[c['ref']];box=item['bbox_mm']
            if c['library_id']==USB:
                if item['angle_deg']!=0. or any(abs(a-b)>1e-5 for a,b in zip(item['position_mm'],[dims['width_mm']/2,dims['height_mm']-3])):add('USB_EDGE_POSITION','USB 插座需要朝向板边，不能埋在普通网格内部。',c)
            elif box[0]<0 or box[1]<0 or box[2]>dims['width_mm'] or box[3]>dims['height_mm']:add('GENERIC_COMPONENT_OUTSIDE_BOARD',c['ref']+' 的实际外形/焊盘超出板框。',c)
    from .signal_roles import definitions as signal_definitions
    signal_definitions=signal_definitions()
    # This is distance/rail identity only, not effective capacitance or loop inductance.
    for c in s['components'].values():
        roles=([('VDD',3.0)] if c['library_id']==CC else ([('VCC',3.0)] if c['library_id']==INV else ([('IN',3.0)] if c['library_id']==SW else ([('VDD',5.0)] if c['library_id'] in [ESP,SENSOR] else ([('IN',3.0),('OUT',3.0)] if c['library_id']==LDO else ([('IN',15.0),('OUT',15.0)] if c['library_id']==BUCK else ([('VCCA',3.0),('VCCB',3.0)] if c['library_id']==TXS else [])))))))
        if c['library_id']==PWM_BUCK:roles=[('IN',3.0),('OUT',3.0)]
        if c['library_id'] in signal_definitions:roles=[(p,3.0) for p in signal_definitions[c['library_id']]['supply_pins']]
        elif source_parts.get(c['library_id'],{}).get('power_budget_model')=='supply_pin_load':roles=[(p,3.0) for p in source_parts[c['library_id']]['supply_pins']]
        if c['id'] in source_bound_loads:roles=[(p,3.0) for p in source_bound_loads[c['id']]['facts']['supply_pins']]
        for role,limit in roles:
            supply=net(c,role);gnd=next((net(c,name) for name in ground_names(s,c) if net(c,name) is not None),None)
            if gnd is None:
                connected={assigned.get((c['id'],p['pin_id'])) for p in c['pins']}
                grounds={n['name'] for n in s['nets'].values() if n['kind']=='ground' and n['name'] in connected}
                if len(grounds)==1:gnd=next(iter(grounds))
            numbers={n for p in c['pins'] if p['name']==role for n in p.get('physical_numbers',backend.expanded_number(p['number']))}
            targets=[p for p in byref[c['ref']]['pads'] if p['net']==supply and p['number'] in numbers];distances=[]
            for cap in s['components'].values():
                if cap['library_id']!='Device:C':continue
                val=electrical.number(cap['value'])
                if val is None or val<(1e-6 if c['library_id']==LDO else 1e-7)*.999:continue
                pads=byref[cap['ref']]['pads']
                if {p['net'] for p in pads}!={supply,gnd}:continue
                for p in pads:
                    if p['net']==supply:
                        distances.extend((math.dist(p['at_mm'],t['at_mm']),cap['ref']) for t in targets)
            best=min(distances) if distances else None
            measurements.append(dict(check='decoupling_pad_distance',reference=c['ref'],pin=role,closest_capacitor=best[1] if best else None,distance_mm=best[0] if best else None,policy_limit_mm=limit))
            if not best or best[0]>limit:add('DECOUPLING_DISTANCE',c['ref']+' '+role+' 的旁路电容距离超过受限布局策略。',c)
    from .regulator_layout import evaluate as regulator_layout
    local=regulator_layout(s,actual,stage);findings.extend(local['findings']);measurements.extend(local['measurements'])
    from .path_constraints import evaluate as check_paths
    paths=check_paths(s,actual,stage);findings.extend(paths['findings']);measurements.extend(paths['measurements'])
    if not measurements:add('LAYOUT_PROFILE_UNSUPPORTED','本布局检查没有适用的天线/旁路规则，不能视为已检查通过。')
    return dict(pcb_build_id=pcb_id,revision=s['revision'],verdict='fail' if findings else 'pass',findings=findings,details=dict(stage=stage,measurements=measurements,qualification='geometry_and_proximity_policy_not_RF_or_thermal_qualification'))

class DesignChecks:
    def envelope_set(self,request):
        def change(s):
            require(power_integrity.supports_envelope(s),'POWER_PROFILE_UNSUPPORTED','需要已有电源诊断器件或有效绑定的供电脚负载模型；LED 请使用 operating_conditions_set。')
            require(request.min_v<=request.nominal_v<=request.max_v,'INVALID_SUPPLY_RANGE','供电最小、标称、最大值顺序不正确。')
            value=request.model_dump(exclude={'project_id','expected_revision','idempotency_key'})
            matched=[p for p in s['power_sources'].values() if p['boundary_pin']==value['positive_pin'] and p['ground_net_id']==value['ground_net_id'] and p['nominal_v']==value['nominal_v'] and self.source_valid(s,p)]
            require(bool(matched),'INVALID_POWER_BOUNDARY','范围必须对应已有、有效的外部电源声明。')
            s['power_envelope']=value;return dict(entity_id=s['id'])
        return self.mutate('power_envelope_set',request,change)

    def power_check(self,request):
        with self.locked():
            s=self.load(request.project_id);require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。');return power_integrity.evaluate(s)

    def layout_check(self,request):
        with self.locked():
            s,b,folder=self.pcb_target(request);name=request.stage+'.kicad_pcb'
            require(name in b['sources'],'ROUTING_REQUIRED','缺少待检查的实际 PCB。')
            report=layout_report(s,worker('inspect',folder,name),b['id'],request.stage)
            filename='layout_'+request.stage+'.json';write_json(folder/filename,report)
            b['checks']['layout_'+request.stage]=dict(verdict=report['verdict'],files={filename:backend.digest((folder/filename).read_bytes())});self.save(s);return report

    def readiness(self,request):
        with self.locked():
            s=self.load(request.project_id);require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            gates=[];blockers=[];actions=[]
            def gate(key,passed,message,tool):
                gates.append(dict(check_id=key,status='pass' if passed else 'blocked',message=message))
                if not passed:blockers.append(dict(code=key,message=message));actions.append(dict(tool=tool,purpose=message))
            v=self.validate_state(s);gate('SPECIFICATION',v['verdict']=='pass','规格、引脚和已声明规则检查。','spec_validate')
            bom=self.bom_check_state(s);bound=bom['identity_verdict']=='pass'
            gate('REAL_PARTS',bound,'所有元件都需要有来源且匹配数值/封装的实物型号。','parts_search')
            from . import prototype_policy
            eligibility=prototype_policy.assessment(self,s)
            gate('PROTOTYPE_FILE_POLICY',eligibility['verdict']=='eligible_for_prototype_review','已知错误必须消除，声明预算和器件来源必须完整。','spec_validate')
            complex_board=any(c['library_id'] in [ESP,SENSOR,LDO,USB,BUCK,'Sensor:AM2302'] for c in s['components'].values())
            if complex_board:
                gates.append(dict(check_id='PHYSICAL_QUALIFICATION',status='unqualified',message='实物供电动态、温升、完整铜线、复位及射频资格尚未验证，需在独立原型审阅中明确接受。'))
            current=[b for b in s['pcb_builds'].values() if b['revision']==s['revision']]
            gate('CURRENT_PCB',bool(current),'需要与当前规格版本一致的实际 PCB。','pcb_build')
            if current:
                b=current[-1];folder=self.project_path(s['id'])/'pcb_builds'/b['id']
                try:
                    self.pcb_target(PCBRef(project_id=s['id'],pcb_build_id=b['id']));evidence_valid=True
                except DomainError as e:evidence_valid=False;blockers.append(e.payload)
                gate('PCB_EVIDENCE',evidence_valid,'当前 PCB 及原理图输入/检查证据必须保持完整。','pcb_verify')
                for key,tool in [('verify_routed','pcb_verify'),('drc','drc_run')]+([('layout_routed','pcb_layout_check')] if complex_board else []):
                    check=b['checks'].get(key,{});valid=check.get('verdict')=='pass' and bool(check.get('files'))
                    valid=valid and all((folder/n).is_file() and backend.digest((folder/n).read_bytes())==sha for n,sha in check.get('files',{}).items())
                    gate('PCB_'+key.upper(),valid,'需要当前且未被篡改的 '+key+' 通过报告。',tool)
                if evidence_valid:
                    try:
                        self._pcb_checks(b,folder)
                        self.export_assessment(s,b,folder)
                        gate('PROTOTYPE_PCB_POLICY',True,'当前实际 PCB、铜线策略与文件导出策略一致。','manufacturing_review_prepare')
                    except DomainError as e:
                        gate(e.payload['code'],False,e.payload['message'],'pcb_copper_check' if e.payload['code'].startswith('COPPER_') else 'pcb_review_export')
                check=b['checks'].get('preview',{});valid=check.get('verdict')=='produced' and bool(check.get('files'))
                valid=valid and all((folder/n).is_file() and backend.digest((folder/n).read_bytes())==sha for n,sha in check.get('files',{}).items())
                gate('PCB_PREVIEW',valid,'需要当前且未被篡改的实际 PCB 预览。','pcb_preview')
            return dict(project_id=s['id'],revision=s['revision'],status='blocked' if blockers else 'ready_for_human_review',gates=gates,blockers=blockers,next_actions=actions,manufacturing_approved=False,ordered=False,qualification='prototype_files_review_only_physical_and_production_qualification_remain_false')
