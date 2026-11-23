"""Actual feedback topology and source-bound DC interval, not transient proof."""
from . import parts
from .electrical import number
from .power_stage_roles import PWM_BUCK

def analyze(s):
 a={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']};rows=[];findings=[];current={p['candidate_id']:p for p in parts.catalog()}
 def net(c,name):return next((a.get((c['id'],p['pin_id'])) for p in c['pins'] if p['name']==name),None)
 def find(lib,x,y):return [c for c in s['components'].values() if c['library_id']==lib and None not in (x,y) and x!=y and {a.get((c['id'],p['pin_id'])) for p in c['pins']}=={x,y}]
 def add(code,msg,c,severity='error'):findings.append(dict(code=code,severity=severity,message=msg,entity_ids=[c['id']]))
 def bound(c):
  p=s.get('part_bindings',{}).get(c['id'])
  return p if p and current.get(p['candidate_id'])==p and p['evidence_status']=='available' and parts.binding_valid(s,c,p) else None
 for c in s['components'].values():
  if c['library_id']!=PWM_BUCK:continue
  record=bound(c)
  if not record or record['facts'].get('mode')!='forced_PWM':add('PWM_REGULATOR_PART_REQUIRED','需有效绑定来源明确的强制PWM型号。',c);continue
  f=record['facts'];v,o,g,fb=[net(c,n) for n in ['IN','OUT','GND','FB']]
  if None in (v,o,g,fb) or len({v,o,g,fb})!=4 or net(c,'EN')!=v:add('PWM_REGULATOR_CONNECTION','输入、输出、地、反馈需独立，EN需接输入电源。',c);continue
  pg=next(p for p in c['pins'] if p['name']=='PG')
  if (c['id'],pg['pin_id']) in a or s.get('pin_intents',{}).get(c['id']+':'+pg['pin_id'],{}).get('intent')!='no_connect':add('PWM_REGULATOR_PG_UNUSED','本实现要求未用的PG开漏输出明确悬空。',c)
  if {p['number']:p['name'] for p in c['pins']}!=f['pin_mapping']:add('PWM_REGULATOR_PIN_SOURCE','引脚分组与原厂封装角色不符。',c);continue
  top,bot,ff=find('Device:R',o,fb),find('Device:R',fb,g),find('Device:C',o,fb)
  if len(top)!=1 or len(bot)!=1 or len(ff)!=1:add('PWM_REGULATOR_FEEDBACK','需要唯一上/下分压电阻与跨上电阻的前馈电容。',c);continue
  rb=[bound(x) for x in top+bot+ff]
  if any(not p for p in rb):add('PWM_REGULATOR_PASSIVE_SOURCE','反馈元件需有效的型号、数值和封装绑定。',c);continue
  allowed={c['id'],top[0]['id'],bot[0]['id'],ff[0]['id']}
  if any(p['component_id'] not in allowed for p in s['nets'][fb]['pins']):add('PWM_REGULATOR_FB_LOAD','反馈节点有未建模负载，不能套用反馈漏电区间。',c);continue
  topf,botf,cff=[p['facts'] for p in rb];t,b=topf['value_ohm'],botf['value_ohm']
  if b>f['feedback_bottom_max_ohm'] or abs(cff['capacitance_f']-f['feedforward_time_s']/b)>1e-6*cff['capacitance_f'] or cff['dielectric']!='C0G':add('PWM_REGULATOR_COMPENSATION','分压下电阻必须≤100k，C0G前馈电容按12us/Rbottom选定。',c);continue
  def interval(p):
   tc=p['temperature_coefficient_ppm_per_k']*1e-6*(p['temperature_range_c'][1]-p['temperature_range_c'][0]);tol=p['tolerance_fraction'];return p['value_ohm']*(1-tol)*(1-tc),p['value_ohm']*(1+tol)*(1+tc)
  tl,th=interval(topf);bl,bh=interval(botf);leak=f['feedback_leakage_max_a']*th
  lo=f['feedback_min_v']*(1+tl/bh)-leak;hi=f['feedback_max_v']*(1+th/bl)+leak;nom=f['feedback_nominal_v']*(1+t/b)
  if lo<.6 or hi>4.:add('PWM_REGULATOR_OUTPUT_RANGE','反馈极限超过转换器可调输出范围。',c);continue
  caps={}
  for role,nid,minimum in [('IN',v,f['nominal_input_capacitance_min_f']),('OUT',o,f['nominal_output_capacitance_range_f'][0])]:
   selected=find('Device:C',nid,g);values=[number(x['value']) for x in selected];total=sum(x or 0. for x in values)
   if any(not x for x in values) or total<minimum*.999:add('PWM_REGULATOR_CAPACITANCE','缺少所需标称陶瓷电源电容。',c)
   if any(not bound(x) or bound(x)['facts'].get('dielectric') not in ['X5R','X7R'] for x in selected):add('PWM_REGULATOR_CAPACITOR_SOURCE','电源陶瓷电容需要来源明确的X5R/X7R绑定。',c)
   upper=sum((number(x['value']) or 0.)*(1+(bound(x) or {}).get('facts',{}).get('tolerance_fraction',0)) for x in selected)
   if role=='OUT' and upper>f['nominal_output_capacitance_range_f'][1]*1.001:add('PWM_REGULATOR_OUTPUT_CAPACITANCE_MAX','输出标称电容及其正容差超过47uF应用上界。',c)
   caps[role]=dict(references=[x['ref'] for x in selected],nominal_f=total,upper_with_initial_tolerance_f=upper,effective_lower_f=None)
  add('PWM_CAPACITANCE_EFFECTIVE_UNVERIFIED','已选标称陶瓷电容；直流偏压、温度/老化后的有效输入≥3uF、输出≥5uF尚缺完整下界证据。',c,'warning')
  rows.append(dict(component_id=c['id'],reference=c['ref'],input_net_id=v,output_net_id=o,ground_net_id=g,feedback_net_id=fb,top_resistor=top[0]['ref'],bottom_resistor=bot[0]['ref'],feedforward_capacitor=ff[0]['ref'],output_interval_v=dict(min_v=lo,nominal_v=nom,max_v=hi,basis='forced_PWM_VFB_minmax_plus_bound_resistor_tolerance_full_TCR_span_and_FB_leakage; conditional_on_effective_C_and_valid_layout; no_ripple_transient_thermal_bound',source_component=c['id']),divider_resistance_intervals_ohm=[(tl,th),(bl,bh)],capacitors=caps,physical_qualified=False))
 return dict(findings=findings,regulators=rows)
