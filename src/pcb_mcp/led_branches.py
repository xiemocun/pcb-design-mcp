"""DC stress bound for a resistor/LED chain into a source-modelled sink.

Keeps the original three-part LED checker intact. This model accounts for all
resistors in a simple series path; unknown output roles remain unsupported.
"""
from . import parts

def evaluate(service,s):
 findings=[];rows=[];bom=service.bom_check_state(s);findings.extend(f for f in bom['findings'] if f['severity']=='error');ctx=s.get('power_envelope',{});comps=s['components'];a={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
 def add(code,msg):findings.append(dict(code=code,severity='error',message=msg))
 def net(c,num):return next((a.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==num),None)
 try:
  from .dc_limits import bound_models
  models=bound_models(s)
 except ValueError as exc:
  add('LED_DC_MODEL_INVALID',str(exc));models={}
 if not ctx:add('LED_BRANCH_ENVELOPE_REQUIRED','受控 LED 支路需要明确电源范围。')
 legacy_leds=[c for c in comps.values() if c['library_id']=='Device:LED' and (c['id'] not in models or models[c['id']][2].kind!='led')]
 if ctx and legacy_leds and tuple(ctx.get('ambient_range_c') or [ctx['ambient_c'],ctx['ambient_c']])!=(25.,25.):
  add('LED_BRANCH_TEMPERATURE_UNSUPPORTED','LED 额定值引用25°C表格；尚无其它温度的降额模型。')
 source_nets={p['net_id'] for p in s.get('power_sources',{}).values() if p['boundary_pin']==ctx.get('positive_pin') and p['ground_net_id']==ctx.get('ground_net_id')}
 resistors={c['id']:c for c in comps.values() if c['library_id']=='Device:R'}
 for led in comps.values():
  if led['id'] in models and models[led['id']][2].kind=='led':
   try:
    from .dc_led_branch import evaluate as rated_evaluate
    rows.append(rated_evaluate(s,led,models))
   except (ValueError,KeyError,TypeError) as exc:add('LED_DC_RATING_OR_TOPOLOGY_INVALID',str(exc))
   continue
  if led['library_id']!='Device:LED':continue
  anode,cathode=net(led,'2'),net(led,'1');drivers=[]
  for c in comps.values():
   binding=s.get('part_bindings',{}).get(c['id'],{});f=binding.get('facts',{})
   for number in f.get('output_sink_pin_numbers',[]):
    if net(c,number)==cathode:drivers.append((c,f))
  if len(drivers)!=1:add('LED_SINK_MODEL_REQUIRED',led['ref']+' 阴极需要唯一有来源的受控下拉输出。');continue
  # Walk a non-branching resistor-only chain back to the declared positive rail.
  node=anode;used=[];seen=set()
  while node not in source_nets:
   choices=[(c,{net(c,'1'),net(c,'2')}) for cid,c in resistors.items() if cid not in seen and node in {net(c,'1'),net(c,'2')}]
   if len(choices)!=1:break
   c,ends=choices[0]
   if None in ends or len(ends)!=2:break
   seen.add(c['id']);used.append(c);node=next(n for n in ends if n!=node)
   # Each intermediate net may only contain the two adjacent chain terminals.
   expected=2
   if len(s['nets'][next(iter(ends-{node}))]['pins'])!=expected:break
  if node not in source_nets or not used:add('LED_BRANCH_UNSUPPORTED',led['ref']+' 未形成无分支限流电阻链。');continue
  lp=s.get('part_bindings',{}).get(led['id']);rp=[s.get('part_bindings',{}).get(c['id']) for c in used]
  if not lp or any(not x for x in rp):add('LED_BRANCH_PART_REQUIRED','LED 支路缺少来源绑定。');continue
  mins=[]
  for p in rp:
   f=p['facts'];span=f['temperature_range_c'][1]-f['temperature_range_c'][0];mins.append(f['value_ohm']*(1-f['tolerance_fraction'])*(1-f['temperature_coefficient_ppm_per_k']*1e-6*span))
  if not ctx or any(x<=0 for x in mins):continue
  total=sum(mins);vmax=ctx['max_v'];imax=vmax/total;df=lp['facts'];sink=drivers[0][1]['sink_reference_current_a_at5v']
  row=dict(reference=led['ref'],sink=drivers[0][0]['ref'],series_resistors=[c['ref'] for c in used],minimum_series_reference_ohm=total,dc_current_upper_reference_a=imax,led_power_upper_reference_w=vmax*vmax/(4*total),basis='steady forward LED and modelled0..supply sink; Vf conservatively0; resistor tolerance/full TCR envelope; no brightness/transient guarantee')
  if imax>.5*df['dc_current_abs_max_a'] or imax>sink:add('LED_BRANCH_CURRENT_MARGIN','LED 支路超过降额电流或所选下拉输出的参考负载条件。')
  if row['led_power_upper_reference_w']>.5*df['power_abs_max_w']:add('LED_BRANCH_POWER_MARGIN','LED 支路的功耗参考超过降额策略。')
  for p in rp:
   f=p['facts'];upper_r=f['value_ohm']*(1+f['tolerance_fraction'])*(1+f['temperature_coefficient_ppm_per_k']*1e-6*(f['temperature_range_c'][1]-f['temperature_range_c'][0]))
   if imax*imax*upper_r>.5*f['rated_power_w']:add('LED_BRANCH_RESISTOR_POWER','LED 限流电阻的功耗参考超过降额策略。')
  rows.append(row)
 if not rows:add('LED_BRANCH_PROFILE_UNSUPPORTED','未找到受支持的受控 LED 支路。')
 return dict(revision=s['revision'],verdict='fail' if any(f['severity']=='error' for f in findings) else 'pass',findings=findings,calculations=dict(led_branches=rows),evidence_hash=parts.rules_hash(),coverage=[dict(check_id='source_linked_resistor_LED_sink_DC_reference',status='checked' if rows else 'unsupported'),dict(check_id='physical_brightness_and_switching',status='not_qualified')],qualification='conditional_DC_branch_stress_reference_not_functional_or_production_qualification')
