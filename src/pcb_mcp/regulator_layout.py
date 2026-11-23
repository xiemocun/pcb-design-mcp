"""Source-driven local power-cap paths on actual front copper, no loop-L proof."""
import math
from .power_stage_roles import PWM_BUCK
from .power_paths import selected_path

def evaluate(s,actual,stage):
 findings=[];rows=[];comps=s['components'];fps=actual['components'];a={(p['component_id'],p['pin_id']):n['name'] for n in s['nets'].values() for p in n['pins']};allnodes=actual.get('direct_contacts',{}).get('nodes',{})
 def pp(c,role):return [p for p in fps[c['ref']]['pads'] if p['number'] in {n for q in c['pins'] if q['name']==role or q['number']==role for n in q['physical_numbers']}]
 def add(code,msg,c):findings.append(dict(code=code,severity='error',message=msg,entity_ids=[c['id']]))
 # Keep only actual front tracks/pads; a TH pad cannot jump to an omitted back track.
 nodes={u:dict(x,contacts=[]) for u,x in allnodes.items() if x.get('kind')=='pad' or (x.get('kind')=='track' and x.get('layer')=='F.Cu')}
 for u,x in nodes.items():x['contacts']=[v for v in allnodes[u]['contacts'] if v in nodes]
 def ids(c,role):
  nums={p['number'] for p in pp(c,role)};return {u for u,x in nodes.items() if x.get('reference')==c['ref'] and x.get('number') in nums}
 for c in comps.values():
  if c['library_id']!=PWM_BUCK:continue
  for role in ['IN','OUT']:
   targets=pp(c,role);ground=pp(c,'GND');nn={p['net'] for p in targets};gn={p['net'] for p in ground};options=[]
   for cap in comps.values():
    if cap['library_id']!='Device:C':continue
    f=s.get('part_bindings',{}).get(cap['id'],{}).get('facts',{})
    if f.get('capacitance_f',0)<4.7e-6:continue
    pads=fps[cap['ref']]['pads']
    if {p['net'] for p in pads}!=nn|gn:continue
    distance=min(math.dist(p['at_mm'],t['at_mm']) for p in pads for t in targets if p['net']==t['net']);options.append((distance,cap['ref'],cap))
   if not options:add('PWM_LOCAL_CAP_MISSING','未找到实际电源/地焊盘对应的陶瓷电容。',c);continue
   distance,_,cap=min(options,key=lambda x:(x[0],x[1]));row=dict(check='PWM_local_power_cap_front_paths',reference=c['ref'],role=role,capacitor=cap['ref'],pad_distance_mm=distance,stage=stage,physical_qualified=False);rows.append(row)
   if stage!='routed':continue
   for cr,sr in [('1',role),('2','GND')]:
    try:
     from .copper import layer_thicknesses
     row[sr+'_front_track_reference']=selected_path(nodes,ids(c,sr),ids(cap,cr),s['pcb_copper_policy']['copper_thickness_um'],layer_thicknesses(s,s['pcb_copper_policy']))
    except (ValueError,KeyError) as error:add('PWM_LOCAL_CAP_FRONT_PATH','关键电源电容未建立全正面铜线连接：'+str(error),c)
 return dict(findings=findings,measurements=rows)
