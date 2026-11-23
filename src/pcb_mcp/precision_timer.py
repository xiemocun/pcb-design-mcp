"""Source-bound LTC6992-1 fixed-duty resistor network (manufacturer Fig9).

Intervals include R tolerance/TCR and MOD/DIV leakage. They describe the
datasheet timing model, not arbitrary output loading, board parasitics or startup.
"""
import itertools,math
from . import parts
LIBRARY='PCB_MCP:LTC6992xS6-1'

def resistor_bounds(f):
 r=f['value_ohm'];t=f['tolerance_fraction'];d=f['temperature_coefficient_ppm_per_k']*1e-6*(f['temperature_range_c'][1]-f['temperature_range_c'][0])
 return [r*(1-t)*(1-d),r*(1+t)*(1+d)]

def intervals(f,rt,rb,dt,db,supply):
 """Enumerate independent parameter endpoints, preserving KCL correlation."""
 rs=[resistor_bounds(r) for r in [rt,rb,dt,db]];vmin,vmax=supply
 div=[];imp=[]
 for a,b,v,i in itertools.product(rs[2],rs[3],[vmin,vmax],[-f['div_input_leakage_max_a'],f['div_input_leakage_max_a']]):
  th=a*b/(a+b);imp.append(th);div.append(b/(a+b)-i*th/v)
 center=lambda code:(code+.5)/16
 codes=[code for code in range(1,15) if center(code)-f['div_code_ratio_half_width']<=min(div) and max(div)<=center(code)+f['div_code_ratio_half_width']]
 if len(codes)!=1 or max(imp)>f['div_max_thevenin_ohm']:raise ValueError('PRECISION_TIMER_DIVCODE_MARGIN')
 code=codes[0];pol=int(code>=8);ndiv=4**(code if not pol else 15-code)
 series=[a+b for a,b in itertools.product(rs[0],rs[1])]
 if min(series)<f['rset_range_ohm'][0] or max(series)>f['rset_range_ohm'][1]:raise ValueError('PRECISION_TIMER_RSET_RANGE')
 frequencies=[];duties=[];ratios=[];currents=[]
 for a,b,v,i in itertools.product(rs[0],rs[1],f['set_voltage_range_v'],[-f['mod_input_leakage_max_a'],f['mod_input_leakage_max_a']]):
  vm=(v/a-i)/(1/a+1/b);iset=(v-vm)/a;q=vm/v;ratios.append(q);currents.append(iset)
  base=f['frequency_factor_hz_ohm']*(iset/v)/ndiv;d=q/.8-.125;d=1-d if pol else d
  frequencies.extend(base*(1+sign*f['frequency_error_fraction_fulltemp']) for sign in [-1,1])
  duties.extend(d+sign*f['duty_error_fraction_fulltemp'] for sign in [-1,1])
 if min(ratios)<f['qualified_mod_ratio_range'][0] or max(ratios)>f['qualified_mod_ratio_range'][1]:raise ValueError('PRECISION_TIMER_MOD_RANGE')
 nominal_r=rt['value_ohm']+rb['value_ohm'];nominal_d=rb['value_ohm']/nominal_r/.8-.125
 return dict(divcode=code,polarity=pol,ndiv=ndiv,div_ratio_interval=[min(div),max(div)],div_allowed_interval=[center(code)-f['div_code_ratio_half_width'],center(code)+f['div_code_ratio_half_width']],div_thevenin_max_ohm=max(imp),rset_interval_ohm=[min(series),max(series)],mod_ratio_interval=[min(ratios),max(ratios)],iset_interval_a=[min(currents),max(currents)],nominal_frequency_hz=f['frequency_factor_hz_ohm']/ndiv/nominal_r,nominal_high_duty=1-nominal_d if pol else nominal_d,frequency_reference_interval_hz=[min(frequencies),max(frequencies)],high_duty_reference_interval=[min(duties),max(duties)],basis='manufacturer Fig9 plus full-temperature IC error, resistor initial tolerance and conservative full-family TCR span, MOD/DIV leakage; source test-load conditions retained',full_function_qualified=False)

def analyze(s):
 selected=[c for c in s['components'].values() if c['library_id']==LIBRARY]
 if not selected:return dict(findings=[],timers=[])
 catalog={p['candidate_id']:p for p in parts.catalog()};a={(p['component_id'],p['pin_id']):nid for nid,n in s['nets'].items() for p in n['pins']};findings=[];rows=[]
 def net(c,n):return next((a.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==str(n)),None)
 def binding(c):
  p=s.get('part_bindings',{}).get(c['id'])
  return p if p and p==catalog.get(p['candidate_id']) and p['evidence_status']=='available' and parts.binding_valid(s,c,p) else None
 def passive(lib,x,y):return [c for c in s['components'].values() if c['library_id']==lib and {net(c,p['number']) for p in c['pins']}=={x,y}]
 def add(code,msg,c):findings.append(dict(code=code,severity='error',message=msg,entity_ids=[c['id']]))
 for c in selected:
  p=binding(c)
  if not p or p['facts'].get('circuit_model')!='ltc6992_resistor_pwm':add('PRECISION_TIMER_SOURCE','需要当前有效的LTC6992-1来源、引脚和封装绑定。',c);continue
  f=p['facts'];m,g,t,d,v,o=[net(c,n) for n in [1,2,3,4,5,6]]
  if None in [m,g,t,d,v,o] or len({m,g,t,d,v,o})!=6:add('PRECISION_TIMER_CONNECTION','MOD/SET/DIV/OUT/电源/地须独立，按实际S6引脚连接。',c);continue
  paths=[passive('Device:R',t,m),passive('Device:R',m,g),passive('Device:R',v,d),passive('Device:R',d,g)]
  caps=passive('Device:C',v,g)
  if any(len(xs)!=1 for xs in paths) or not caps:add('PRECISION_TIMER_RESISTOR_NETWORK','固定占空比模式需要SET→电阻→MOD→电阻→地及电源→电阻→DIV→电阻→地和旁路。',c);continue
  resistors=[xs[0] for xs in paths];bounds=[binding(x) for x in resistors]
  if any(x is None for x in bounds) or any(binding(x) is None for x in caps):add('PRECISION_TIMER_PASSIVE_SOURCE','设定电阻和旁路必须绑定真实型号。',c);continue
  if not any(binding(x)['facts']['capacitance_f']>=100e-9*.999 for x in caps):add('PRECISION_TIMER_BYPASS','需要至少标称100nF来源明确的电源旁路。',c);continue
  allowed={t:{c['id'],resistors[0]['id']},m:{c['id'],resistors[0]['id'],resistors[1]['id']},d:{c['id'],resistors[2]['id'],resistors[3]['id']}}
  if any({x['component_id'] for x in s['nets'][n]['pins']}!=ids for n,ids in allowed.items()):add('PRECISION_TIMER_EXTRA_LOAD','设定节点存在未建模负载、旁路或驱动。',c);continue
  env=s.get('power_envelope',{});positive=env.get('positive_pin',{})
  if not env or env.get('ground_net_id')!=g or a.get((positive.get('component_id'),positive.get('pin_id')))!=v or env.get('min_v',0)<f['supply_min_v'] or env.get('max_v',math.inf)>f['supply_max_v'] or not f['temperature_range_c'][0]<=env.get('ambient_c',math.inf)<=f['temperature_range_c'][1]:add('PRECISION_TIMER_SUPPLY','当前参考需要器件供电直接来自已声明范围内的外部电源及同一地。',c);continue
  try:r=intervals(f,*[b['facts'] for b in bounds],[env['min_v'],env['max_v']])
  except ValueError as exc:add(str(exc),'电阻及漏电的完整范围不能稳定选择分频码或已超出来源模型范围。',c);continue
  req={x['key']:x['value'] for x in s.get('requirements',[])}
  for lo_key,hi_key,interval in [('pulse_frequency_min_hz','pulse_frequency_max_hz',r['frequency_reference_interval_hz']),('pulse_duty_min','pulse_duty_max',r['high_duty_reference_interval'])]:
   lo,hi=req.get(lo_key),req.get(hi_key)
   if all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in [lo,hi]) and not lo<=interval[0]<=interval[1]<=hi:add('PRECISION_TIMER_INTERVAL_MISMATCH',f'来源条件下的参考范围{interval}超出要求[{lo}, {hi}]。',c)
  r.update(reference=c['ref'],mpn=p['mpn'],setting_resistors=[x['ref'] for x in resistors],nominal_high_seconds=r['nominal_high_duty']/r['nominal_frequency_hz'],nominal_low_seconds=(1-r['nominal_high_duty'])/r['nominal_frequency_hz'],physical_verified=False,source_test_load=dict(resistance_ohm=f['datasheet_test_load_ohm'],capacitance_pf=f['datasheet_test_capacitance_pf']),qualification='conditional_source_model_not_full_board_function',unresolved=['Output timing table test load 5k/5pF is not the full declared external load interval','Output voltage extrema specified at discrete supplies only','Startup typical only; no guaranteed maximum','PCB coupling, ground return and source-recommended shielding/ground-plane qualification not established'])
  rows.append(r)
 return dict(findings=findings,timers=rows)
