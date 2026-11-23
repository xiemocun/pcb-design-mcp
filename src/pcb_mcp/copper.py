"""Measured geometry and explicit, limited copper reference screens.

Independent implementation of mathematical reference formulas documented by
KiCad. No third-party source is copied. No physical thermal/path approval.
"""
import math
from .common import require,write_json
from . import backend

SOURCES=[
 'https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#custom-design-rules',
 'https://docs.kicad.org/10.0/en/pcb_calculator/pcb_calculator.html#track-width',
 'https://raw.githubusercontent.com/KiCad/kicad/10.0/pcb_calculator/calculator_panels/panel_track_width.cpp']
RESISTIVITY_20C=1.72e-8


def policy_findings(s):
    policy=s.get('pcb_copper_policy',{});out=[]
    from .power_paths import loop_legs
    rules={rule['net_id']:rule for rule in policy.get('rules',[])}
    loops=policy.get('dc_loops',[])
    if len({loop['name'] for loop in loops})!=len(loops):
        out.append(dict(code='COPPER_LOOP_DUPLICATE_NAME',severity='error',message='铜线回路名称必须唯一。'))
    for loop in loops:
        try:loop_legs(s,loop,rules)
        except ValueError as e:out.append(dict(code=str(e),severity='error',message='铜线回路端点与实际网络或规则不符。',loop=loop['name']))
    for rule in policy.get('rules',[]):
        net=s['nets'].get(rule['net_id'])
        if net is None:out.append(dict(code='COPPER_RULE_UNKNOWN_NET',severity='error',message='铜线规则引用了已不存在的网络。'))
        elif net['kind'] not in ['power','ground','signal']:out.append(dict(code='COPPER_RULE_NET_KIND',severity='error',message='铜线规则要求已知的实际网络。'))
        if rule.get('return_model','tracks')=='stitched_ground_planes' and (not net or net['kind']!='ground' or s['board_constraints'].get('ground_plane_net_id')!=rule['net_id']):
            out.append(dict(code='COPPER_PLANE_MODEL_ROLE',severity='error',message='拼接平面模型只能用于已明确生成的实际地网络。'))
        if rule['min_width_mm']<s.get('board_constraints',{}).get('track_width_mm',.15):
            out.append(dict(code='COPPER_RULE_BELOW_GLOBAL_MIN',severity='error',message='网络最小线宽低于全局最小值，需调整规则后重新构建。'))
    from .copper_escape import definitions
    try:definitions(s)
    except (ValueError,KeyError) as e:out.append(dict(code='COPPER_ESCAPE_INVALID',severity='error',message=str(e)))
    from .short_escapes import definitions as short_definitions
    try:short_definitions(s)
    except (ValueError,KeyError) as e:out.append(dict(code='COPPER_SHORT_ESCAPE_INVALID',severity='error',message=str(e)))
    return out


def reference_current(width_mm,thickness_um,rise_c,internal=False):
    # IPC-2221 calculator reference; area is square mils. Not IPC-2152 or
    # a solved thermal model. Internal copper uses the internal coefficient.
    return (.024 if internal else .048)*rise_c**.44*(width_mm/.0254*thickness_um/25.4)**.725


def layer_thicknesses(s,policy):
    from .stackup import copper_layers
    stack=s['board_constraints'].get('stackup')
    declared={x['name']:x['thickness_um'] for x in stack['copper']} if stack else {}
    return {layer:min(policy['copper_thickness_um'],declared.get(layer,policy['copper_thickness_um']))
            for layer in copper_layers(s['board_constraints'])}


def dsn_report(s,text,isolated_nets=()):
    from .sexpr import parse,children,one
    # DSN's quotation declaration is intentionally not a JSON string. Remove
    # just that known declaration before using the existing read-only parser.
    tree=parse(text.replace('(string_quote ")','(string_quote QUOTE)'))
    require(tree['v'][:2]==['pcb','pcb-mcp'],'DSN_RULE_MISMATCH','DSN 必须采用固定设计标识。')
    require(one(tree,'unit')==['um'],'DSN_RULE_MISMATCH','未支持的 DSN 单位。')
    from .stackup import copper_layers
    structure=children(tree,'structure')
    layers=[row['v'][1] for row in children(structure[0],'layer')] if len(structure)==1 else []
    require(layers==copper_layers(s['board_constraints']),'DSN_LAYER_MISMATCH','实际 DSN 铜层与当前叠层不一致。',actual=layers,expected=copper_layers(s['board_constraints']))
    network=children(tree,'network')[0];actual={};classes={}
    for entry in children(network,'class'):
        name=entry['v'][1];width=float(one(children(entry,'rule')[0],'width')[0])/1000
        members=[x for x in entry['v'][2:] if isinstance(x,str)]
        for net in members:
            require(net not in actual,'DSN_RULE_MISMATCH','同一网络出现在多个布线类别中。')
            actual[net]=width;classes[net]=name
    expected={n['name']:s['board_constraints']['track_width_mm'] for n in s['nets'].values()}
    for rule in s.get('pcb_copper_policy',{}).get('rules',[]):expected[s['nets'][rule['net_id']]['name']]=rule['min_width_mm']
    for net in isolated_nets:
        require(net['name'] not in expected and net['name'].lstrip('/').startswith('unconnected-'),'DSN_RULE_MISMATCH','NC 网络标识与实际电气网络冲突。')
        expected[net['name']]=s['board_constraints']['track_width_mm']
    require(set(actual)==set(expected) and all(abs(actual[n]-w)<1e-6 for n,w in expected.items()),'DSN_RULE_MISMATCH','实际 DSN 线宽类别与当前网络规则不一致。',actual=actual,expected=expected)
    return dict(passed=True,copper_layers=layers,expected_mm=expected,actual_mm=actual,classes=classes,dsn_sha256=backend.digest(text.encode()))


def evaluate(s,actual,pcb_id,connectivity_verified=False):
    policy=s.get('pcb_copper_policy',{});rules={r['net_id']:r for r in policy.get('rules',[])}
    findings=policy_findings(s);rows=[];geometry_ok=True;screen_ok=True;missing=False
    def add(code,message,severity='error',**details):findings.append(dict(code=code,severity=severity,message=message,**details))
    from .ground_planes import definition as plane_definition
    try:plane_required=bool(plane_definition(s))
    except ValueError as error:
        add(str(error),'当前器件组合不支持已定义的地平面布局。');geometry_ok=False;plane_required=True
    if plane_required and not actual.get('copper_zone_count',0):
        add('COPPER_GROUND_PLANE_MISSING','当前布局策略要求的地平面没有生成。');geometry_ok=False
    if actual.get('copper_zone_count',0):
        plane=actual.get('ground_planes',{})
        if not plane.get('supported'):
            add('COPPER_ZONES_UNSUPPORTED','铺铜不在当前已核验的地平面范围内，不能跳过。');geometry_ok=False
        elif plane.get('status')!='pass' or not plane.get('return_connected',plane.get('track_only_ground_connected')) or not plane.get('native_refill_matches_saved'):
            add('COPPER_GROUND_PLANE_INVALID','实际地平面填充、焊盘接触或独立地线连接检查失败。',details=plane);geometry_ok=False
        else:
            add('COPPER_ZONE_NETWORK_NOT_SOLVED','地平面几何与连接已核验；以下筛选仅为保留走线，不计铜区电阻、分流或回流电感。','warning');missing=True
    if not connectivity_verified:add('COPPER_CONNECTIVITY_UNVERIFIED','需先通过当前实际焊盘网络与 DRC 检查。','warning');missing=True
    from .copper_escape import definitions,allowed_segment
    try:escapes=definitions(s)
    except (ValueError,KeyError):escapes=[]
    from . import short_escapes
    try:necks=short_escapes.definitions(s)
    except (ValueError,KeyError):necks=[]
    for net in s['nets'].values():
        if net['kind'] not in ['power','ground'] and net['id'] not in rules:continue
        rule=rules.get(net['id']);tracks=[t for t in actual.get('conductors',[]) if t['net']==net['name']]
        from .stackup import copper_layers
        lines=[t for t in tracks if t['kind']=='track' and t['layer'] in copper_layers(s['board_constraints'])
               and math.isfinite(t['width_mm']) and t['width_mm']>0 and math.isfinite(t['length_mm']) and t['length_mm']>=0]
        vias=[t for t in tracks if t['kind']=='via'];other=[t for t in tracks if t not in lines+vias]
        width=rule['min_width_mm'] if rule else s['board_constraints']['track_width_mm']
        escape=next((d for d in escapes if d['net_id']==net['id']),None);narrowed=[];thin=[]
        net_necks=[d for d in necks if d['net_id']==net['id']];neck_tracks={d['component_id']:[] for d in net_necks}
        for t in lines:
            if t['width_mm']+1e-6>=width:continue
            allowed=False
            if escape:allowed=allowed_segment(escape,t,actual,net['name'])
            if allowed:narrowed.append(t)
            else:
                match=next((d for d in net_necks if short_escapes.allowed(d,t,actual,net['name'])),None)
                if match:neck_tracks[match['component_id']].append(t)
                else:thin.append(t['uuid'])
        if escape and sum(t['length_mm'] for t in narrowed)>escape['max_total_length_mm']+1e-6:
            add('COPPER_ESCAPE_LENGTH',net['name']+' 控制器全部窄引出线长度超过受管上限。');geometry_ok=False
        proof=next((p for p in actual.get('escape_isolation',[]) if p['net_id']==net['id']),{})
        if narrowed and (not proof.get('non_controller_pads_still_connected') or set(proof.get('removed_tracks',[]))!={t['uuid'] for t in narrowed}):
            add('COPPER_ESCAPE_BACKBONE_UNPROVEN',net['name']+' 移除窄引出线后，未证明其他地线焊盘仍连通；不能把它当作仅供控制器的支路。');geometry_ok=False
        if thin:add('COPPER_TRACK_TOO_NARROW',net['name']+' 实际走线低于已声明最小线宽。',net_id=net['id'],tracks=thin);geometry_ok=False
        if other:add('COPPER_GEOMETRY_UNSUPPORTED',net['name']+' 存在未支持的铜线类型或层，不能跳过。',items=[t['uuid'] for t in other]);geometry_ok=False
        if not lines:add('COPPER_TRACKS_MISSING',net['name']+' 未找到支持的实际走线，不能视为零电阻。','warning');missing=True
        row=dict(net_id=net['id'],name=net['name'],kind=net['kind'],rule=rule,track_count=len(lines),via_count=len(vias),total_track_length_mm=sum(t['length_mm'] for t in lines),min_track_width_mm=min((t['width_mm'] for t in lines),default=None),width_violations=thin,escape=dict(definition=escape,tracks=[t['uuid'] for t in narrowed],total_length_mm=sum(t['length_mm'] for t in narrowed),isolation=proof),track_reference=None)
        row['pad_necks']=[short_escapes.reference(d,neck_tracks[d['component_id']],layer_thicknesses(s,policy)['F.Cu'],policy['temperature_rise_c']) for d in net_necks]
        if any(not r['passed'] for r in row['pad_necks']):
            add('COPPER_SHORT_ESCAPE_REFERENCE_FAILED',net['name']+' 的短引出线总长或相对端点温升参考超限。');geometry_ok=False;screen_ok=False
        if row['pad_necks']:
            add('COPPER_SHORT_ESCAPE_CONDITIONAL',net['name']+' 仅通过全网电流、实际短引出长度、声明铜厚及固定端温的电阻/一维热参考；不证明绝对端温或实物载流。','warning')
        if rule and lines:
            thickness=policy['copper_thickness_um'];rise=policy['temperature_rise_c']
            layer_thickness=layer_thicknesses(s,policy)
            resistance=sum(RESISTIVITY_20C*t['length_mm']*1e6/(t['width_mm']*layer_thickness[t['layer']]) for t in lines)
            short_tracks=[t for ts in neck_tracks.values() for t in ts]
            backbone=[t for t in lines if t not in narrowed+short_tracks]
            def current_reference(t):return reference_current(t['width_mm'],layer_thickness[t['layer']],rise,t['layer'] not in ['F.Cu','B.Cu'])
            current=min((current_reference(t) for t in backbone),default=0.0)
            escape_current=min((current_reference(t) for t in narrowed),default=None)
            isolated=not narrowed or (proof.get('non_controller_pads_still_connected') and set(proof.get('removed_tracks',[]))=={t['uuid'] for t in narrowed})
            satisfied=isolated and rule['design_current_a']<=current and (escape_current is None or escape['branch_current_a']<=escape_current)
            drop=resistance*rule['design_current_a']
            row['track_reference']=dict(series_sum_resistance_20c_ohm=resistance,series_sum_drop_20c_v=drop,minimum_external_layer_current_reference_a=current,escape_current_reference_a=escape_current,current_reference_satisfied=satisfied,track_sum_budget_satisfied=drop<=rule['reference_drop_budget_v'],via_and_pad_resistance_included=False,branch_currents_solved=False,actual_path_drop=False)
            row['track_reference'].update(minimum_layer_current_reference_a=current,layer_thickness_um=layer_thickness,internal_layer_model='IPC2221_internal_coefficient_not_thermal_qualification')
            row['track_reference']['minimum_external_layer_current_reference_a']=min((current_reference(t) for t in backbone if t['layer'] in ['F.Cu','B.Cu']),default=None)
            plane_return=rule.get('return_model','tracks')=='stitched_ground_planes'
            if plane_return:
                row['track_only_comparison_not_applicable_to_plane']=row['track_reference']
                row['track_reference']=None
                row['plane_reference']=dict(connectivity_verified=bool(actual.get('ground_planes',{}).get('return_connected')),declared_net_current_a=rule['design_current_a'],current_distribution_solved=False,ampacity_qualified=False,drop_qualified=False)
                add('COPPER_PLANE_DISTRIBUTION_UNMODELED',net['name']+' 是已核验物理拼接连接的平面回流；逐支线施加全网电流的串联模型不适用。保留旧比较值，但未建立平面分流、温升或压降结论。','warning');missing=True
            if not satisfied and not plane_return:
                add('COPPER_CURRENT_REFERENCE_LIMIT',net['name']+' 的声明电流超出当前铜厚/温升假设下的线宽参考筛选；这不是实测电流或热认证。',net_id=net['id']);screen_ok=False
            if not plane_return and drop>rule['reference_drop_budget_v']:
                add('COPPER_TRACK_SUM_BUDGET',net['name']+' 全部直线段电阻求和的参考压降超过预算；分支求和可能保守，不能据此宣称实际引脚欠压。','warning',net_id=net['id']);missing=True
        else:
            add('COPPER_RULE_MISSING',net['name']+' 缺少供电铜线电流/铜厚筛选规则。','warning',net_id=net['id']);missing=True
        rows.append(row)
    if not rows:add('COPPER_NO_POWER_NETS','规格中没有 power/ground 网络，不能给出供电铜线通过结论。','warning');missing=True
    # Missing pad/barrel geometry, physical material and coupled thermal proofs
    # cannot be promoted to approval by a client-declared origin or budget.
    add('COPPER_PHYSICAL_QUALIFICATION_MISSING','尚未验证实物铜厚、焊盘/过孔与回流路径电阻、实际温度、热耦合及全部最大负载；仅为有条件的参考筛选。','warning')
    from . import power_paths,power_integrity
    paths=power_paths.evaluate(s,actual,power_integrity.evaluate(s)['rails'])
    loops=power_paths.evaluate_loops(s,actual,connectivity_verified)
    for loop in loops:
        if loop['status']=='fail':
            add('COPPER_DC_LOOP_REFERENCE_FAILED','声明回路缺少有效铜线路径或超出参考压降预算。',loop=loop['name'],reason=loop['reason']);screen_ok=False
    for path in paths:
        if path['status']=='margin_not_established':
            add('COPPER_DC_TRACK_REFERENCE_MARGIN',path['reference']+' 在声明电流和选取整段铜线参考模型下未建立最低供电裕量；这是参考预算失败，不是实测欠压。',reference=path['reference'],margin_reference_v=path['margin_reference_v'])
    has_error=any(f['severity']=='error' for f in findings)
    return dict(project_id=s['id'],pcb_build_id=pcb_id,revision=s['revision'],verdict='fail' if has_error else 'incomplete',geometry_verdict='pass' if geometry_ok and not policy_findings(s) else 'fail',reference_screen='fail' if not screen_ok or has_error else 'incomplete' if missing else 'pass',findings=findings,nets=rows,dc_track_paths=paths,dc_loop_references=loops,assumptions=dict(policy,reference_resistivity_20c_ohm_m=RESISTIVITY_20C,reference_temperature_c=20.0,reference_formula='IPC-2221 external-layer calculator approximation; no physical thermal qualification',sources=SOURCES),evidence=dict(connectivity_verified=connectivity_verified,ground_planes=actual.get('ground_planes',{})),thermal_qualified=False,full_path_drop_qualified=False,manufacturing_approved=False,qualification='actual_width_check_and_declared_reference_screen_not_physical_ampacity_or_complete_IR_drop_proof')


class CopperService:
    def copper_rules_set(self,request):
        def change(s):
            value=request.model_dump(exclude={'project_id','expected_revision','idempotency_key'})
            ids=[r['net_id'] for r in value['rules']]
            require(len(set(ids))==len(ids),'DUPLICATE_NET_RULE','同一网络不能有多条互相覆盖的铜线规则。')
            for ident in ids:require(ident in s['nets'],'UNKNOWN_NET','铜线规则必须引用当前真实网络。')
            trial=dict(s,pcb_copper_policy=value);findings=policy_findings(trial)
            require(not findings,'COPPER_RULE_INVALID','铜线规则不合法。',findings=findings)
            s['pcb_copper_policy']=value;return dict(entity_id=s['id'])
        return self.mutate('pcb_net_rules_set',request,change)

    def copper_check(self,request):
        from .pcb_service import worker
        with self.locked():
            s,b,folder=self.pcb_target(request)
            require('routed.kicad_pcb' in b['sources'],'ROUTING_REQUIRED','需先生成实际布线结果。')
            verified=True
            for key in ['verify_routed','drc']:
                check=b['checks'].get(key,{})
                valid=check.get('verdict')=='pass' and bool(check.get('files'))
                for n,sha in check.get('files',{}).items():
                    require((folder/n).is_file() and backend.digest((folder/n).read_bytes())==sha,'ARTIFACT_CHANGED','铜线检查依赖的实际网络/DRC 证据已变化。')
                verified=verified and valid
            actual=worker('inspect',folder,'routed.kicad_pcb')
            report=evaluate(s,actual,b['id'],verified)
            write_json(folder/'copper_geometry.json',actual)
            report['evidence'].update(board_sha256=b['sources']['routed.kicad_pcb'],native_geometry_sha256=backend.digest((folder/'copper_geometry.json').read_bytes()))
            write_json(folder/'copper_report.json',report)
            b['checks']['copper']=dict(verdict=report['verdict'],files={n:backend.digest((folder/n).read_bytes()) for n in ['copper_geometry.json','copper_report.json']})
            self.save(s);return report
