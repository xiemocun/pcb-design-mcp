"""Bounded physical pad necks, not a whole-net width waiver.

Every neck is tested at the FULL declared net current, including parallel IC
pins. Total neck length is treated in series for a conservative geometric
reference. This does not qualify endpoint temperatures, transients, vias,
plating tolerances or actual PCB heat spreading.
"""
RHO_100C=1.72e-8*(1+.00393*80)
K_REFERENCE=300.  # Declared conservative copper-reference assumption, W/(m K).

def definitions(s):
    assignment={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']};out=[]
    for rule in s.get('pcb_copper_policy',{}).get('rules',[]):
        for row in rule.get('pad_escapes',[]):
            c=s['components'].get(row['component_id'])
            if c is None:raise ValueError('SHORT_ESCAPE_COMPONENT_MISSING')
            if len(set(row['pins']))!=len(row['pins']):raise ValueError('SHORT_ESCAPE_DUPLICATE_PIN')
            if row['min_width_mm']<s['board_constraints']['track_width_mm'] or row['min_width_mm']>=rule['min_width_mm']:raise ValueError('SHORT_ESCAPE_WIDTH_RANGE')
            for number in row['pins']:
                pin=next((p for p in c['pins'] if number in p['physical_numbers']),None)
                if pin is None or assignment.get((c['id'],pin['pin_id']))!=rule['net_id']:raise ValueError('SHORT_ESCAPE_NET_PIN_MISMATCH')
            if rule.get('escape_profile','none')!='none':raise ValueError('SHORT_ESCAPE_PROFILE_OVERLAP')
            if rule.get('return_model','tracks')!='tracks':raise ValueError('SHORT_ESCAPE_PLANE_ROLE')
            out.append(dict(row,ref=c['ref'],pads=row['pins'],net_id=rule['net_id'],current_a=rule['design_current_a'],model='finite_pad_neck_full_net_current'))
    owners=[(d['net_id'],d['component_id']) for d in out]
    if len(owners)!=len(set(owners)):raise ValueError('SHORT_ESCAPE_DUPLICATE_COMPONENT')
    keys=[(d['net_id'],d['component_id'],p) for d in out for p in d['pads']]
    if len(keys)!=len(set(keys)):raise ValueError('SHORT_ESCAPE_DUPLICATE_WINDOW')
    return out

def area_name(d,p):return 'MCP_NECK_'+d['component_id']+'_'+p

def allowed(d,t,actual,net):
    if t['kind']!='track' or t['layer']!='F.Cu' or t['width_mm']+1e-6<d['min_width_mm']:return False
    box=t['bbox_mm'];r=d['half_window_mm']
    return any(p['number'] in d['pads'] and p['net']==net and box[0]>=p['at_mm'][0]-r-1e-6 and box[2]<=p['at_mm'][0]+r+1e-6 and box[1]>=p['at_mm'][1]-r-1e-6 and box[3]<=p['at_mm'][1]+r+1e-6 for p in actual['components'].get(d['ref'],{}).get('pads',[]))

def reference(d,tracks,thickness_um,rise_limit_c):
    length=sum(t['length_mm'] for t in tracks)/1000
    width=min((t['width_mm'] for t in tracks),default=d['min_width_mm'])/1000
    area=width*thickness_um*1e-6;current=d['current_a']
    resistance=RHO_100C*length/area
    # Solve k*A*T'' = -I^2*rho/A for a uniform strip with both ends
    # held at the SAME reference temperature: max rise = I^2*rho*L^2/(8*k*A^2).
    # Summing disconnected/parallel necks in series only enlarges this screen.
    rise=current*current*RHO_100C*length*length/(8*K_REFERENCE*area*area)
    valid=length*1000<=d['max_total_length_mm']+1e-6 and rise<=rise_limit_c
    return dict(definition=d,tracks=[t['uuid'] for t in tracks],total_length_mm=length*1000,resistance_reference_ohm=resistance,drop_reference_v=current*resistance,dissipation_reference_w=current*current*resistance,rise_above_fixed_endpoints_reference_c=rise,rise_limit_c=rise_limit_c,passed=valid,assumptions=dict(resistivity_ohm_m=RHO_100C,resistivity_temperature_c=100.,thermal_conductivity_w_m_k=K_REFERENCE,copper_thickness_um=thickness_um,entire_net_current_on_each_neck=True,all_necks_summed_in_series=True,fixed_endpoint_temperatures=True,endpoint_temperature_verified=False),physical_qualified=False)
