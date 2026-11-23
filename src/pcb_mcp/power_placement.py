"""Bounded placement improvement from actual KiCad geometry, no trace coordinates.

Moves a buck and its nearest local supply capacitors as a rigid group. A finite
grid search minimizes load-side distance, retaining edge/body/RF separation.
This is a reference-family placement heuristic, not general automatic placement.
"""
import math,re


def improve(board,k,state):
    comps=state['components'];fps={f.GetReference():f for f in board.GetFootprints()}
    stages=state.get('power_budget_policy',{}).get('stages',[])
    buck=[comps[x['component_id']] for x in stages if x['kind']=='buck']
    hosts=[c for c in comps.values() if c['library_id']=='RF_Module:ESP32-WROOM-32E']
    if len(buck)!=1 or len(hosts)!=1:return []
    source=buck[0];host=hosts[0];sf=fps[source['ref']];hf=fps[host['ref']]
    def pad(c,name):
        pin=next(p for p in c['pins'] if p['name']==name)
        numbers=pin.get('physical_numbers',re.findall(r'[A-Za-z0-9]+',pin['number']) if pin['number'].startswith('[') else [pin['number']])
        return next(p for p in fps[c['ref']].Pads() if p.GetNumber() in numbers)
    def xy(p):return k.ToMM(p.x),k.ToMM(p.y)
    def rect(f):
        q=f.GetBoundingBox(False,False)
        return [k.ToMM(q.GetX()),k.ToMM(q.GetY()),k.ToMM(q.GetRight()),k.ToMM(q.GetBottom())]
    def overlap(a,b):return a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]
    output=pad(source,'OUT');target=pad(host,'VDD');entry=pad(source,'IN')
    # Capacitor ownership is based on the nearest actual supply pad on its net,
    # including converter outputs and loads, not references or a BOM count.
    supply=[]
    for c in comps.values():
        for name in ['VDD','VCC','VCCA','VCCB','IN','OUT']:
            if any(p['name']==name for p in c['pins']):
                try:supply.append((c['id'],pad(c,name)))
                except StopIteration:continue
    group=[sf];decoupling=[]
    if source['library_id']=='PCB_MCP:TPSM82821A_SIL':
        feedback=pad(source,'FB').GetNetCode()
        for c in comps.values():
            if c['library_id'] in ['Device:R','Device:C'] and any(p.GetNetCode()==feedback for p in fps[c['ref']].Pads()):group.append(fps[c['ref']])
    for c in comps.values():
        if c['library_id'] not in ['Device:C','Device:C_Polarized']:continue
        f=fps[c['ref']];options=[]
        for p in f.Pads():
            if not p.GetNetname() or p.GetNetname() not in {entry.GetNetname(),output.GetNetname()}:continue
            owners=[(math.dist(xy(p.GetPosition()),xy(q.GetPosition())),cid) for cid,q in supply if q.GetNetCode()==p.GetNetCode()]
            if owners:options.append(min(owners))
        if options and min(options)[1]==source['id']:group.append(f)
        elif options and min(options)[1]==host['id']:
            pp=list(f.Pads());supply_pads=[p for p in pp if p.GetNetCode()==target.GetNetCode()]
            grounds=[p for p in hf.Pads() if p.GetNetname()=='GND']
            if len(pp)!=2 or len(supply_pads)!=1 or not grounds:continue
            def cap_score():
                v=next(p for p in f.Pads() if p.GetNetCode()==target.GetNetCode())
                g=next(p for p in f.Pads() if p.GetNetname()=='GND')
                return 2*math.dist(xy(v.GetPosition()),xy(target.GetPosition()))+min(math.dist(xy(g.GetPosition()),xy(p.GetPosition())) for p in grounds)
            before=cap_score();angle=f.GetOrientationDegrees();f.SetOrientationDegrees(angle+180.);after=cap_score()
            if after<before-1e-6:decoupling.append(dict(policy='native_supply_pad_facing_load',reference=c['ref'],from_angle_deg=angle,to_angle_deg=f.GetOrientationDegrees(),weighted_pad_distance_before_mm=before,weighted_pad_distance_after_mm=after))
            else:f.SetOrientationDegrees(angle)
    original={f.GetReference():xy(f.GetPosition()) for f in group}
    boxes=[rect(f) for f in group];blocked=[]
    grouprefs=set(original)
    for f in board.GetFootprints():
        if f.GetReference() not in grouprefs:
            a=rect(f);blocked.append([a[0]-1,a[1]-1,a[2]+1,a[3]+1])
        for z in f.Zones():
            if z.GetIsRuleArea() and (z.GetDoNotAllowFootprints() or z.GetDoNotAllowTracks()):
                q=z.GetBoundingBox();blocked.append([k.ToMM(q.GetX()),k.ToMM(q.GetY()),k.ToMM(q.GetRight()),k.ToMM(q.GetBottom())])
    c=state['board_constraints'];w,h,m=c['width_mm'],c['height_mm'],c['edge_margin_mm']
    start=xy(sf.GetPosition());out=xy(output.GetPosition());goal=xy(target.GetPosition())
    # Input source connection is a secondary term; low-voltage output headroom
    # makes proximity to the MCU the primary placement objective.
    upstream=[p for f in board.GetFootprints() if f not in group for p in f.Pads() if p.GetNetCode()==entry.GetNetCode()]
    def score(dx,dy):
        output_distance=math.dist((out[0]+dx,out[1]+dy),goal)
        incoming=min((math.dist((xy(entry.GetPosition())[0]+dx,xy(entry.GetPosition())[1]+dy),xy(p.GetPosition())) for p in upstream),default=0.)
        return 3*output_distance+incoming,output_distance,dx*dx+dy*dy,dx,dy
    candidates=[]
    for xi in range(math.ceil(2*m),math.floor(2*(w-m))+1):
        for yi in range(math.ceil(2*m),math.floor(2*(h-m))+1):
            dx,dy=xi/2-start[0],yi/2-start[1]
            moved=[[a[0]+dx,a[1]+dy,a[2]+dx,a[3]+dy] for a in boxes]
            if all(a[0]>=m and a[1]>=m and a[2]<=w-m and a[3]<=h-m and not any(overlap(a,b) for b in blocked) for a in moved):candidates.append(score(dx,dy))
    if not candidates:raise ValueError('POWER_GROUP_PLACEMENT_CAPACITY')
    best=min(candidates);dx,dy=best[-2:]
    if best[0]>=score(0.,0.)[0]-1e-6:return decoupling
    for f in group:
        x,y=original[f.GetReference()];f.SetPosition(k.VECTOR2I(k.FromMM(x+dx),k.FromMM(y+dy)))
    return decoupling+[dict(policy='bounded_native_geometry_buck_group_search',references=sorted(grouprefs),from_mm=original,to_mm={f.GetReference():xy(f.GetPosition()) for f in group},output_pad_distance_before_mm=math.dist(out,goal),output_pad_distance_after_mm=best[1],body_separation_mm=1.,trace_coordinates_generated=False)]
