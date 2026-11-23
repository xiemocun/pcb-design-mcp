"""Declared enabled-state DC budget. Never an actual whole-port current bound."""
from . import parts,electrical,power_integrity,usb_gate_contract as gate
from .common import require
from .editing import spec_hash

LOAD_ROLES={
 'RF_Module:ESP32-WROOM-32E':('VDD',),
 'PCB_MCP:AM2302_SIP4':('VDD',),
 'Logic_LevelTranslator:TXS0102DCT':('VCCA','VCCB'),
 gate.CC:('VDD',),gate.INV:('VCC',)}
from .power_stage_roles import BUCKS
STAGE_ROLES={**{lib:'buck' for lib in BUCKS},gate.AUX:'ldo',gate.SW:'load_switch'}


def load_roles(s):
 roles=dict(LOAD_ROLES)
 for part in parts.bound_supply_models(s).values():
  if part['facts'].get('power_budget_model')=='supply_pin_load':roles[part['symbol_id']]=tuple(part['facts']['supply_pins'])
 return roles

def expected(s):
 roles=LOAD_ROLES;bound=parts.bound_supply_models(s);loads={};stages={}
 for c in s['components'].values():
  for name in bound.get(c['id'],{}).get('facts',{}).get('supply_pins',roles.get(c['library_id'],())):
   p=next((p for p in c['pins'] if p['name']==name),None)
   if p:loads[(c['id'],p['pin_id'])]=(c,p)
  if c['library_id'] in STAGE_ROLES:stages[c['id']]=(c,STAGE_ROLES[c['library_id']])
 return loads,stages


class PowerBudgetService:
 def power_budget_set(self,request):
  def change(s):
   wanted,stages=expected(s);seen=set()
   for item in request.loads:
    key=(item.pin.component_id,item.pin.pin_id)
    require(key in wanted,'BUDGET_LOAD_PIN_UNSUPPORTED','负载必须指向受支持器件的真实供电脚。')
    require(key not in seen,'DUPLICATE_BUDGET_LOAD','同一供电脚重复声明。');seen.add(key)
   seen=set()
   for item in request.stages:
    require(item.component_id in stages and item.kind==stages[item.component_id][1],'BUDGET_STAGE_ROLE_MISMATCH','电源级种类与真实器件不一致。')
    require(item.component_id not in seen,'DUPLICATE_BUDGET_STAGE','同一电源级重复声明。');seen.add(item.component_id)
    require((item.kind=='buck')==(item.efficiency_lower_bound is not None),'BUDGET_EFFICIENCY_ROLE','仅开关转换级需要明确声明效率下界；线性/开关通路不得伪填转换效率。')
   seen=set()
   for item in request.reserves:
    require(item.net_id in s['nets'] and s['nets'][item.net_id]['kind']=='power','BUDGET_RESERVE_NET_UNSUPPORTED','额外余量必须对应真实电源网络。')
    require(item.net_id not in seen,'DUPLICATE_BUDGET_RESERVE','同一网络余量重复声明。');seen.add(item.net_id)
   s['power_budget_policy']=request.model_dump(exclude={'project_id','expected_revision','idempotency_key'})
   return dict(entity_id=s['id'])
  return self.mutate('power_budget_set',request,change)

 def power_budget_check(self,request):
  with self.locked():
   s=self.load(request.project_id);require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
   return evaluate(s,self.bom_check_state(s))


def evaluate(s,bom,replacement_evidence=None):
 from . import dc_power_tree
 tree=dc_power_tree.reference(s)
 if tree is not None:return dc_power_tree.budget_report(s,bom,tree)
 from . import linear_passive
 linear=linear_passive.analyze(s)
 if linear is not None and linear['verdict'] in ('pass','fail'):return linear_passive.budget_report(s,bom,linear)
 from .model_power import reference,budget
 model=reference(s,replacement_evidence)
 if model is not None:return budget(s,bom,model)
 if linear is not None:return linear_passive.budget_report(s,bom,linear)
 findings=[];load_rows=[];stage_rows=[];passives=[];policy=s.get('power_budget_policy',{});ctx=s.get('power_envelope',{})
 def add(code,message,severity='warning',**kw):findings.append(dict(code=code,severity=severity,message=message,**kw))
 catalog={p['candidate_id']:p for p in parts.catalog()};valid={}
 for c in s['components'].values():
  p=s.get('part_bindings',{}).get(c['id'])
  if p and catalog.get(p['candidate_id'])==p and p['evidence_status']=='available' and parts.binding_valid(s,c,p):valid[c['id']]=p
  else:add('BUDGET_PART_UNRESOLVED',c['ref']+' 缺少当前有效的实物身份/来源/封装。','error' if p else 'warning',reference=c['ref'])
 for f in bom['findings']:
  if f['severity']=='error':add('BUDGET_BOM_CONFLICT',f['message'],'error',original_code=f['code'])
 wanted,stage_expected=expected(s);assigned={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
 def net(c,name):return next((assigned.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
 supplied={(p['pin']['component_id'],p['pin']['pin_id']):p for p in policy.get('loads',[])}
 stages={p['component_id']:p for p in policy.get('stages',[])};reserves={p['net_id']:p['unmodeled_dc_reserve_ma']/1000 for p in policy.get('reserves',[])}
 power_nets={n['id']:n for n in s['nets'].values() if n['kind']=='power'}
 # Scope is deliberately finite: unknown active parts never disappear from coverage.
 active=list(s['components'].values())
 legacy=power_integrity.evaluate(s,replacement_evidence);rails=legacy['rails']
 paths=[row for row in legacy['calculations'] if 'path_current_bound_a' in row and row.get('enable_verified') is False]
 from .functional import passive_connector_role
 allowed={*load_roles(s),*STAGE_ROLES,'Device:R','Device:C','Device:C_Polarized','Device:LED',gate.USB,'Connector_Generic:Conn_01x06','Connector_Generic:Conn_01x02'}
 unsupported=[c for c in active if c['library_id'] not in allowed
              and not passive_connector_role(c,valid.get(c['id'],{'kind':None}))]
 if not wanted or unsupported:add('BUDGET_PROFILE_UNSUPPORTED','存在没有受管供电脚/电源级模型的器件，不能忽略其负载。','error')
 if not policy:add('POWER_BUDGET_MISSING','需要逐个声明供电脚负载、转换损耗假设及各电源网络余量。')
 if not ctx:add('BUDGET_ENVELOPE_MISSING','没有板端实际范围的声明，标称5V不足以推导电流预算。')
 for key in set(supplied)-set(wanted):add('BUDGET_LOAD_STALE','负载声明已不再对应当前供电脚。','error')
 for key in set(stages)-set(stage_expected):add('BUDGET_STAGE_STALE','电源级声明已失效。','error')
 for cid in set(stages)&set(stage_expected):
  item=stages[cid];kind=stage_expected[cid][1];eta=item.get('efficiency_lower_bound')
  if item['kind']!=kind or ((kind=='buck')!=(eta is not None)) or (eta is not None and not 0<eta<=1):add('BUDGET_STAGE_STALE','当前电源级的种类/效率声明已失效。','error')
 for key in set(reserves)-set(power_nets):add('BUDGET_RESERVE_STALE','余量指向已失效的电源网络。','error')
 for key in set(wanted)-set(supplied):add('BUDGET_LOAD_MISSING',wanted[key][0]['ref']+' '+wanted[key][1]['name']+' 缺少直流供电脚电流上界声明。')
 for key in set(stage_expected)-set(stages):add('BUDGET_STAGE_MISSING',stage_expected[key][0]['ref']+' 缺少转换效率/入口开销声明。')
 for key in set(power_nets)-set(reserves):add('BUDGET_RESERVE_MISSING',power_nets[key]['name']+' 未声明未建模直流负载余量。')
 voltage_errors={'SUPPLY_ENVELOPE_BOUNDARY_STALE','BUCK_INPUT_RANGE','BUCK_DROPOUT_MARGIN','LDO_INPUT_RANGE','LDO_DROPOUT_MARGIN','SUPPLY_ENVELOPE_OUT_OF_RANGE','USB_CONTROL_SUPPLY_RANGE','RAIL_DRIVER_CONFLICT','BUCK_TEMPERATURE_SCOPE_UNSUPPORTED','DC_PASS_ENVELOPE_INVALID'}
 for f in legacy['findings']:
  if f['code'] in voltage_errors or (f['severity']=='error' and f['code'].startswith('PWM_')):add(f['code'],f['message'],'error')
 demand={nid:0. for nid in power_nets}
 for nid,value in reserves.items():
  if nid in demand:demand[nid]+=value
 for key,(c,pin) in wanted.items():
  entry=supplied.get(key);nid=assigned.get(key);rail=rails.get(nid);part=valid.get(c['id'])
  row=dict(reference=c['ref'],pin=pin['name'],net=power_nets.get(nid,{}).get('name'),mpn=part['mpn'] if part else None,declared_current_a=entry['max_current_ma']/1000 if entry else None,origin=policy.get('origin'),status='incomplete')
  if entry and nid in demand and part and rail:
   f=part['facts'];prefix='vcca' if pin['name']=='VCCA' else 'vccb' if pin['name']=='VCCB' else 'supply'
   lo=f.get(prefix+'_min_v');hi=f.get(prefix+'_max_v')
   row['supply_reference_v']=[rail['min_v'],rail['max_v']];row['recommended_supply_v']=[lo,hi]
   if lo is None or hi is None:add('BUDGET_SUPPLY_RATING_UNKNOWN',c['ref']+' 缺少推荐电压范围。')
   elif rail['min_v']<lo or rail['max_v']>hi:add('BUDGET_LOAD_VOLTAGE',c['ref']+' 的参考电源范围超出所绑型号推荐范围。','error')
   demand[nid]+=entry['max_current_ma']/1000;row['status']='declared'
  elif entry:add('BUDGET_RAIL_UNKNOWN',c['ref']+' 的供电脚缺少可用的电源范围或有效实物来源。')
  load_rows.append(row)
 for c in s['components'].values():
  if c['library_id']=='Logic_LevelTranslator:TXS0102DCT':
   a=rails.get(net(c,'VCCA'));b=rails.get(net(c,'VCCB'))
   if a and b and a['max_v']>b['min_v']:add('BUDGET_DUAL_RAIL_ORDER','TXS0102 的 VCCA 上界大于 VCCB 下界，不能证明供电次序条件。','error')
 # Direct rail-fed resistors: conservative nonnegative-logic DC envelope,
 # catalog tolerance + full specified TCR span. This is not an analog solution.
 global_max=max((v['max_v'] for v in rails.values()),default=0)
 for c in s['components'].values():
  if c['library_id']!='Device:R':continue
  ns={assigned.get((c['id'],p['pin_id'])) for p in c['pins']};feeds=ns&set(power_nets)
  if not feeds:continue
  part=valid.get(c['id']);f=part['facts'] if part else {};value=electrical.number(c['value']);tol=f.get('tolerance_fraction');tcr=f.get('temperature_coefficient_ppm_per_k');span=f.get('temperature_range_c')
  if value is None or tol is None or tcr is None or not span:add('BUDGET_RESISTOR_RATING_UNKNOWN',c['ref']+' 缺少计算所需的阻值/容差/温漂来源。');continue
  rmin=value*(1-tol)*(1-tcr*1e-6*(span[1]-span[0]))
  if rmin<=0:add('BUDGET_RESISTOR_MODEL_INVALID',c['ref']+' 的电阻下界无效。','error');continue
  current=global_max/rmin
  for nid in feeds:demand[nid]+=current
  passives.append(dict(reference=c['ref'],mpn=part['mpn'],resistance_min_reference_ohm=rmin,dc_current_reference_a=current,nets=[power_nets[n]['name'] for n in sorted(feeds)],assumption='Other terminal stays within0..largest modeled supply; external drive/backfeed and transients excluded'))
  if len(feeds)>1:add('BUDGET_CROSS_RAIL_PASSIVE','不同电源轨之间存在本参考预算不覆盖的电阻通路。','error')
 # Reverse dependency order: downstream loads propagate exactly once upstream.
 pending={cid:(c,kind) for cid,(c,kind) in stage_expected.items() if cid in stages and stages[cid]['kind']==kind and ((kind=='buck' and stages[cid].get('efficiency_lower_bound') is not None and 0<stages[cid]['efficiency_lower_bound']<=1) or (kind!='buck' and stages[cid].get('efficiency_lower_bound') is None))};limit=gate.current_limit(s)
 for _ in range(len(pending)+1):
  if not pending:break
  progress=False
  for cid,(c,kind) in list(pending.items()):
   vi=net(c,'IN');vo=net(c,'OUT')
   if any(other!=cid and net(oc,'IN')==vo for other,(oc,_) in pending.items()):continue
   entry=stages[cid];part=valid.get(cid);iv=rails.get(vi);ov=rails.get(vo)
   if not iv or not ov or vi not in demand or vo not in demand or iv['min_v']<=0 or not part:
    add('BUDGET_STAGE_RAIL_UNKNOWN',c['ref']+' 缺少可用的输入/输出范围或当前实物来源。');del pending[cid];progress=True;continue
   if vi==vo:add('BUDGET_RAIL_CYCLE','电源级输入输出重合。','error');del pending[cid];progress=True;continue
   output=demand[vo];f=part['facts'];overhead=entry['input_overhead_ma']/1000;capacity=f.get('rated_output_current_a')
   if kind=='buck':incoming=ov['max_v']*output/(iv['min_v']*entry['efficiency_lower_bound'])+overhead
   else:incoming=output+overhead
   if kind=='load_switch':capacity=limit['min_a'] if limit else None
   if capacity is None:add('BUDGET_CAPACITY_UNKNOWN',c['ref']+' 缺少可用的所绑型号输出容量/限流范围。')
   elif output>capacity:add('BUDGET_STAGE_CAPACITY',c['ref']+' 的声明输出需求超过当前额定容量或条件化最低限流阈值。','error',reference=c['ref'])
   demand[vi]+=incoming
   stage_rows.append(dict(reference=c['ref'],kind=kind,mpn=part['mpn'],input_net=power_nets[vi]['name'],output_net=power_nets[vo]['name'],declared_output_reference_a=output,output_capacity_reference_a=capacity,declared_input_reference_a=incoming,efficiency_assumption=entry['efficiency_lower_bound'],input_overhead_assumption_a=overhead,current_limit=limit if kind=='load_switch' else None,capacity_screen='unknown' if capacity is None else 'within_declared_budget' if output<=capacity else 'fail',part_evidence_hash=part['definition_hash']))
   del pending[cid];progress=True
  if not progress:break
 if pending:add('BUDGET_RAIL_CYCLE','电源级连接成环，无法形成有向供电预算。','error')
 # The voltage solver resolves these paths outward from explicit sources,
 # before legacy regulator stages. Roll up load current in reverse order.
 for path in reversed(paths):
  vi,vo=path['input_net_id'],path['output_net_id'];part=valid.get(path['component_id'])
  if vi not in demand or vo not in demand or not part:
   add('BUDGET_STAGE_RAIL_UNKNOWN',path['reference']+' 缺少有来源的输入、输出或负载范围。');continue
  output=demand[vo];capacity=path['path_current_bound_a']
  if output>capacity:
   add('BUDGET_STAGE_CAPACITY',path['reference']+' 的声明负载超过该串联供电路径的电流参考上界。','error',reference=path['reference'])
  demand[vi]+=output
  stage_rows.append(dict(reference=path['reference'],kind='pass_switch',mpn=part['mpn'],input_net=power_nets[vi]['name'],output_net=power_nets[vo]['name'],declared_output_reference_a=output,declared_input_reference_a=output,output_capacity_reference_a=capacity,capacity_screen='fail' if output>capacity else 'within_declared_budget',model_id=path['model_id'],part_evidence_hash=part['definition_hash']))
 source_nid=assigned.get((ctx.get('positive_pin',{}).get('component_id'),ctx.get('positive_pin',{}).get('pin_id')))
 if ctx and source_nid not in power_nets:add('BUDGET_SOURCE_RAIL_UNKNOWN','当前声明的入口无法追踪到有效电源网络。','error')
 downstream={row['output_net'] for row in stage_rows}
 if paths and any(nid!=source_nid and current>0 and power_nets[nid]['name'] not in downstream for nid,current in demand.items()):
  add('BUDGET_RAIL_LOAD_UNACCOUNTED','有电源网络负载未沿已核对的路径回算到输入。')
 reference=demand.get(source_nid) if ctx else None
 complete=bool(ctx and policy) and set(supplied)==set(wanted) and set(stages)==set(stage_expected) and set(reserves)==set(power_nets) and len(stage_rows)==len(stage_expected)+len(paths) and all(r['status']=='declared' for r in load_rows) and len(valid)==len(s['components'])
 if reference is not None and reference>ctx['available_current_ma']/1000:add('BUDGET_SOURCE_CAPACITY','声明的入口可用电流小于这组声明条件推算的输入需求。','error')
 # Check the programmed limit as well as the normal DC load: the USB gate
 # enables at 1.5A advertisement, so a higher declared source cannot waive it.
 if reference is not None and limit:
  for stage in stage_rows:
   if stage['kind']=='load_switch' and stage['input_net']==power_nets[source_nid]['name']:
    direct=reference-stage['declared_input_reference_a']
    peak=limit['max_a']+stage['input_overhead_assumption_a']+max(0.,direct)
    stage['limited_source_reference_a']=peak
    stage['limited_source_capacity_a']=min(1.5,ctx['available_current_ma']/1000)
    if peak>stage['limited_source_capacity_a']:add('BUDGET_PROGRAMMED_LIMIT_SOURCE_CAPACITY','限流上界加入口侧声明负载超过声明电源或互锁最低 1.5A 模式额度。','error',reference=stage['reference'])
 for code,msg in [('BUDGET_ASSUMPTIONS_UNVERIFIED','负载、效率下界、入口开销和余量均为声明，未证明是实际最坏值；典型效率没有被当作保证下界。'),('BUDGET_UNMODELED_PHYSICS','仅覆盖稳态直流与非负逻辑电压假设：电容泄漏/浪涌、信号负载、外接UART及反灌、铜线压降、温升、动态CC/启动和实际全部负载未完成资格验证。')]:add(code,msg)
 errors=any(f['severity']=='error' for f in findings)
 unknown=any(f['code'] not in ['BUDGET_ASSUMPTIONS_UNVERIFIED','BUDGET_UNMODELED_PHYSICS'] for f in findings)
 coverage=dict(required_supply_domains=len(wanted),declared_supply_domains=len(supplied),required_stages=len(stage_expected),declared_stages=len(stages),required_rail_reserves=len(power_nets),declared_rail_reserves=len(reserves),valid_part_bindings=len(valid),all_actual_loads_verified=False,origin=policy.get('origin'),spec_sha256=spec_hash(s))
 if paths:
  coverage.update(required_pass_paths=len(paths),resolved_pass_paths=sum(row['kind']=='pass_switch' for row in stage_rows))
 return dict(project_id=s['id'],revision=s['revision'],verdict='fail' if errors else 'incomplete',budget_verdict='fail' if errors else 'conditional' if complete and not unknown else 'incomplete',coverage=coverage,findings=findings,rails=[dict(net_id=nid,name=n['name'],supply_reference=rails.get(nid),declared_dc_reference_a=demand.get(nid)) for nid,n in power_nets.items()],loads=load_rows,stages=stage_rows,passive_loads=passives,declared_dc_reference_input_a=reference if complete else None,actual_input_current_upper_bound_a=None,physical_qualified=False,manufacturing_approved=False,evidence_hash=parts.rules_hash(),qualification='conditional_declared_reference_budget_not_actual_current_or_power_qualification')
