"""Source-bound single-supply follower topology and explicit external boundaries.

The nominal unity transfer is an ideal closed-loop reference, not SPICE, a
measured waveform or a guaranteed error/bandwidth bound for all declarations.
"""
import math
from . import parts,power_integrity,analog_protection
from .electrical import number

def analyze(s):
 comps=s['components'];a={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']};catalog={p['candidate_id']:p for p in parts.catalog()};rows=[];findings=[]
 selected=[c for c in comps.values() if c['library_id']=='Amplifier_Operational:TLV9001IDCK']
 if not selected:return dict(findings=[],amplifiers=[])
 requirements={r['key']:r['value'] for r in s.get('requirements',[])};rails=power_integrity.evaluate(s)['rails'];ctx=s.get('power_envelope',{})
 def net(c,num):return next((a.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==num),None)
 def bound(c):
  p=s.get('part_bindings',{}).get(c['id'])
  return p if p and catalog.get(p['candidate_id'])==p and p['evidence_status']=='available' and parts.binding_valid(s,c,p) else None
 def add(code,msg,c,severity='error'):findings.append(dict(code=code,severity=severity,message=msg,entity_ids=[c['id']]))
 def port(ref,node,ground):
  c=next((c for c in comps.values() if c['ref']==ref),None)
  return c if c and bound(c) and c['library_id']=='Connector_Generic:Conn_01x02' and net(c,'1')==node and net(c,'2')==ground else None
 for c in selected:
  b=bound(c)
  if not b or b['facts'].get('circuit_model')!='unity_voltage_follower':add('ANALOG_SOURCE_REQUIRED','运放需要已读取且有效的真实型号/封装/引脚来源。',c);continue
  f=b['facts'];t=f['terminal_numbers'];ip,im,out,v,g=[net(c,t[n]) for n in ['noninverting','inverting','output','positive_supply','negative_supply']]
  if None in [ip,im,out,v,g] or im!=out or len({ip,out,v,g})!=4:add('ANALOG_NEGATIVE_FEEDBACK_REQUIRED','缓冲器需OUT直连反相输入；正相输入、电源、地及输出保持独立。',c);continue
  if not ctx or g!=ctx.get('ground_net_id') or v not in rails or rails.get(g,{}).get('nominal_v')!=0.:add('ANALOG_SINGLE_SUPPLY_REQUIRED','仅支持明确共地、已声明电压范围的单电源。',c);continue
  if rails[v]['min_v']<f['supply_min_v'] or rails[v]['max_v']>f['supply_max_v']:add('ANALOG_SUPPLY_RANGE','声明供电范围超出该型号推荐工作范围。',c);continue
  keys=['analog_input_min_v','analog_input_max_v','analog_source_max_ohm','analog_external_load_min_ohm','analog_external_cap_max_pf']
  if any(k not in requirements or isinstance(requirements[k],bool) or not isinstance(requirements[k],(int,float)) or not math.isfinite(requirements[k]) for k in keys):add('ANALOG_BOUNDARY_REQUIRED','需声明输入电压、信号源阻抗及外接电阻/电容负载范围。',c);continue
  lo,hi,rs,rl,cl=[requirements[k] for k in keys]
  if not lo<hi or not 0<=rs<=1000. or rl<f['reference_load_min_ohm'] or not 0<=cl<=f['reference_cap_load_max_f']*1e12 or ctx['ambient_c']!=25.:add('ANALOG_BOUNDARY_OUT_OF_SCOPE','当前参考范围要求25°C、源阻抗≤1k、外接电阻≥100k、电容≤100pF。',c);continue
  if lo<rails[g]['nominal_v']+f['reference_rail_headroom_policy_v'] or hi>rails[v]['min_v']-f['reference_rail_headroom_policy_v']:add('ANALOG_RAIL_HEADROOM','声明输入/理想输出范围超出保留0.25V轨边裕量的参考范围。',c);continue
  protection=analog_protection.analyze(s,c)
  if protection['findings']:findings.extend(protection['findings']);continue
  p_in=port(requirements.get('analog_input_connector'),protection['external_net'],g);p_out=port(requirements.get('analog_output_connector'),out,g)
  if not p_in or not p_out or p_in['id']==p_out['id']:add('ANALOG_PORT_MAPPING','输入/输出需独立的已绑定双脚接口，1=信号、2=共地。',c);continue
  loads=[x for x in comps.values() if x['library_id']=='Device:R' and {net(x,'1'),net(x,'2')}=={out,g}]
  if len(loads)!=1 or not bound(loads[0]):add('ANALOG_LOAD_REFERENCE','此参考需要唯一且来源明确的板载输出对地负载。',c);continue
  lf=bound(loads[0])['facts'];drift=lf['temperature_coefficient_ppm_per_k']*1e-6*(lf['temperature_range_c'][1]-lf['temperature_range_c'][0]);rmin=lf['value_ohm']*(1-lf['tolerance_fraction'])*(1-drift);effective_load=1/(1/rmin+1/rl)
  if effective_load<10000.:add('ANALOG_OUTPUT_LOAD','板载及外接负载并联后低于10k参考负载条件。',c);continue
  expected={c['id'],*protection['input_component_ids']}
  if any(p['component_id'] not in expected for p in s['nets'][ip]['pins']):add('ANALOG_INPUT_LOAD_UNSUPPORTED','正相输入节点含未建模负载或驱动。',c);continue
  if any(p['component_id'] not in {c['id'],p_out['id'],loads[0]['id']} for p in s['nets'][out]['pins']):add('ANALOG_OUTPUT_LOAD_UNSUPPORTED','输出节点含参考模型以外的负载。',c);continue
  decaps=[x for x in comps.values() if x['library_id']=='Device:C' and {net(x,'1'),net(x,'2')}=={v,g} and (number(x['value']) or 0.)>=100e-9*.999]
  if not decaps or any(not bound(x) for x in decaps):add('ANALOG_BYPASS_REQUIRED','运放需来源绑定的至少100nF电源对地旁路。',c);continue
  cases=sorted({lo,(lo+hi)/2,hi})
  rows.append(dict(reference=c['ref'],mpn=b['mpn'],input_connector=p_in['ref'],output_connector=p_out['ref'],feedback_kind='direct_negative_unity',input_range_v=[lo,hi],source_impedance_assumption_max_ohm=rs,external_load_assumption_min_ohm=rl,external_cap_load_assumption_max_pf=cl,effective_resistive_load_reference_min_ohm=effective_load,onboard_load=loads[0]['ref'],nominal_transfer=dict(gain=1.,offset_v=0.,basis='ideal_unity_negative_feedback_reference_not_simulation'),dc_reference_cases=[dict(input_v=x,ideal_output_v=x) for x in cases],datasheet_offset_max_at5v25c_v=f['offset_max_v_at5v25c'],bias_current_typical_only_a=f['input_bias_typical_a_at5v'],guaranteed_total_tracking_error_v=None,dynamic_or_stability_qualified=False,physical_verified=False))
  rows[-1]['input_protection']=protection
 return dict(findings=findings,amplifiers=rows)
