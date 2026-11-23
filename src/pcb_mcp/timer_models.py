"""Source-selected 555 astable connections and nominal timing reference."""
import math
from .electrical import number

def requirement_findings(s,rows):
 """Compare declared pulse bounds to the nominal model, never qualify hardware.

 A shared declaration is unambiguous only for one timer. Absence remains
 uncovered; partial, nonnumeric or ambiguous declarations cannot silently pass.
 """
 keys=('pulse_frequency_min_hz','pulse_frequency_max_hz','pulse_duty_min','pulse_duty_max')
 req={r['key']:r['value'] for r in s.get('requirements',[])}
 declared=any(k in req for k in keys)
 status=dict(status='not_declared',basis='nominal_rc_reference_only',full_function_qualified=False,
             not_checked=['threshold_spread','effective_capacitance','loaded_output_levels','startup','physical_waveforms'])
 if not declared:return [],status
 def error(code,message):return [dict(code=code,severity='error',message=message,entity_ids=[])]
 status['status']='fail'
 if any(k not in req or isinstance(req[k],bool) or not isinstance(req[k],(int,float)) or not math.isfinite(req[k]) for k in keys):
  return error('TIMER_REQUIREMENT_INVALID','频率上下限及高电平占空比上下限须同时给出有限数值，单位为Hz和0至1比例。'),status
 flo,fhi,dlo,dhi=(req[k] for k in keys)
 if not 0<flo<=fhi or not 0<dlo<=dhi<1:
  return error('TIMER_REQUIREMENT_INVALID','频率须为正且上下限有序；高电平占空比须在0与1之间且上下限有序。'),status
 if len([c for c in s['components'].values() if c['library_id'] in ['Timer:TLC555xD','PCB_MCP:LTC6992xS6-1']])!=1 or len(rows)!=1:
  return error('TIMER_REQUIREMENT_TARGET_UNRESOLVED','全局脉冲要求需唯一且拓扑有效的定时器；多定时器不能猜测要求对应哪个输出。'),status
 row=rows[0];checks=[];findings=[]
 for name,value,lo,hi in [('frequency_hz',row['nominal_frequency_hz'],flo,fhi),('high_duty',row['nominal_high_duty'],dlo,dhi)]:
  passed=lo<=value<=hi;checks.append(dict(quantity=name,value=value,min=lo,max=hi,verdict='pass' if passed else 'fail'))
  if not passed:findings+=error('TIMER_NOMINAL_REQUIREMENT_MISMATCH',f"{row['reference']} 标称 {name}={value:.9g} 不在要求 [{lo:.9g}, {hi:.9g}] 内。")
 status.update(status='fail' if findings else 'nominal_reference_pass',reference=row['reference'],checks=checks)
 return findings,status

def analyze(s):
 a={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']};findings=[];rows=[]
 def net(c,num):return next((a.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==num),None)
 def add(code,msg,c):findings.append(dict(code=code,severity='error',message=msg,entity_ids=[c['id']]))
 def passive(lib,x,y):return [c for c in s['components'].values() if c['library_id']==lib and x and y and x!=y and {a.get((c['id'],p['pin_id'])) for p in c['pins']}=={x,y}]
 for c in s['components'].values():
  if c['library_id']!='Timer:TLC555xD':continue
  binding=s.get('part_bindings',{}).get(c['id']);f=(binding or {}).get('facts',{})
  if f.get('circuit_model')!='555_astable':add('TIMER_PART_REQUIRED','555 需要有来源的实际型号和封装绑定。',c);continue
  v,g,t,d,o,cv=[net(c,n) for n in ['8','1','2','7','3','5']]
  if None in [v,g,t,d,o,cv] or len({v,g,t,d,o,cv})!=6 or net(c,'6')!=t or net(c,'4')!=v:add('TIMER_ASTABLE_CONNECTION','555 要求RESET接电源、TRIG/THRES同接独立定时节点；电源、地、放电、控制和输出互不短接。',c);continue
  ra=passive('Device:R',v,d);rb=passive('Device:R',d,t);caps=passive('Device:C',t,g);bypass=passive('Device:C',v,g);control=passive('Device:C',cv,g)
  if len(ra)!=1 or len(rb)!=1 or len(caps)!=1 or not bypass or not control:add('TIMER_ASTABLE_PASSIVES','555 缺少唯一的充电电阻、放电电阻、定时电容，或电源/控制旁路。',c);continue
  ra0,rb0,c0=[number(x['value']) for x in [ra[0],rb[0],caps[0]]]
  if not all(x and x>0 for x in [ra0,rb0,c0]):add('TIMER_PARAMETER','555 定时元件参数无效。',c);continue
  high=math.log(2)*(ra0+rb0)*c0;low=math.log(2)*rb0*c0
  rows.append(dict(reference=c['ref'],charging_resistor=ra[0]['ref'],discharge_resistor=rb[0]['ref'],timing_capacitor=caps[0]['ref'],nominal_high_seconds=high,nominal_low_seconds=low,nominal_frequency_hz=1/(high+low),nominal_high_duty=high/(high+low),basis='datasheet nominal1/3 and2/3 thresholds; excludes threshold spread, effective capacitance/leakage and physical waveforms',physical_verified=False))
 from .precision_timer import analyze as precision_analyze
 precision=precision_analyze(s);findings.extend(precision['findings']);rows.extend(precision['timers'])
 extra,requirements=requirement_findings(s,rows);findings.extend(extra)
 return dict(findings=findings,timers=rows,requirements=requirements)
