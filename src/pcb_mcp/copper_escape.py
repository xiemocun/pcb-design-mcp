"""Finite server-owned QFN ground fanout; stdlib-only for native Python 3.9.

This geometry exception is not a physical current/thermal qualification.
Only the three controller ground/mode pads can use it, and every narrowed
segment must stay within a pad window and a component-wide length limit.
"""
MIN_WIDTH_MM=.2
HALF_WINDOW_MM=.85
MAX_TOTAL_LENGTH_MM=2.5


def definitions(s):
    rules=s.get('pcb_copper_policy',{}).get('rules',[]);out=[]
    pin_net={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    for rule in rules:
        if rule.get('escape_profile','none')=='none':
            if rule.get('escape_design_current_a') is not None:raise ValueError('A controller branch budget requires an explicit managed escape profile')
            continue
        if rule.get('escape_profile')!='tusb320_gpio_ground_v1':raise ValueError('Unknown escape profile')
        if s['nets'].get(rule['net_id'],{}).get('kind')!='ground' or rule['min_width_mm']<=MIN_WIDTH_MM:raise ValueError('Escape requires a ground backbone wider than 0.20mm')
        controllers=[c for c in s['components'].values() if c['library_id']=='PCB_MCP:TUSB320LAI_GPIO']
        if len(controllers)!=1:raise ValueError('Escape requires exactly one source-correct TUSB320LAI GPIO controller')
        c=controllers[0];pins={p['name']:p for p in c['pins']}
        if not all(pin_net.get((c['id'],pins[name]['pin_id']))==rule['net_id'] for name in ['GND','PORT','EN_N']):raise ValueError('Controller ground/mode pins must match the declared ground net')
        vdd=pin_net.get((c['id'],pins['VDD']['pin_id']));supply=next((r for r in rules if r['net_id']==vdd),None)
        if supply is None or supply['design_current_a']>.1:raise ValueError('Escape requires a declared auxiliary-net budget no greater than 100mA; it is not a physical proof')
        if rule.get('escape_design_current_a') is None:raise ValueError('Escape requires a separate unverified total controller ground-current budget, including CC and GPIO domains')
        out.append(dict(component_id=c['id'],ref=c['ref'],net_id=rule['net_id'],pads=['3','10','11'],branch_current_a=rule['escape_design_current_a'],min_width_mm=MIN_WIDTH_MM,half_window_mm=HALF_WINDOW_MM,max_total_length_mm=MAX_TOTAL_LENGTH_MM))
    return out


def area_name(definition,pad):return ('MCP_NECK_' if definition.get('model')=='finite_pad_neck_full_net_current' else 'MCP_ESCAPE_')+definition['component_id']+'_'+pad


def all_definitions(s):
    try:from .short_escapes import definitions as short_definitions
    except ImportError:from short_escapes import definitions as short_definitions
    return definitions(s)+short_definitions(s)


def allowed_segment(d,t,actual,netname):
    if t['kind']!='track' or t['layer']!='F.Cu' or t['width_mm']+1e-6<d['min_width_mm']:return False
    b=t['bbox_mm'];radius=d['half_window_mm']
    return any(p['number'] in d['pads'] and p['net']==netname
               and b[0]>=p['at_mm'][0]-radius-1e-6 and b[2]<=p['at_mm'][0]+radius+1e-6
               and b[1]>=p['at_mm'][1]-radius-1e-6 and b[3]<=p['at_mm'][1]+radius+1e-6
               for p in actual['components'].get(d['ref'],{}).get('pads',[]))
