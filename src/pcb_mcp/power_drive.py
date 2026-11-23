"""ERC annotations for source-fed resistive power nets; no voltage proof."""
import math
from .electrical import number


def resistive_drive(state):
    nets=state['nets'];components=state['components']
    assigned={(p['component_id'],p['pin_id']):n for n,row in nets.items() for p in row['pins']}
    reached={s['net_id'] for s in state.get('power_sources',{}).values()}
    for c in components.values():
        reached.update(assigned[(c['id'],p['pin_id'])] for p in c['pins']
                       if p['electrical_type']=='power_out' and (c['id'],p['pin_id']) in assigned)
    reached={n for n in reached if nets[n]['kind']=='power'}
    links=[]
    for c in components.values():
        binding=state.get('part_bindings',{}).get(c['id'],{});value=number(c['value'])
        if c['library_id']!='Device:R' or binding.get('kind')!='resistor' or value is None or not math.isfinite(value) or value<=0:continue
        if binding.get('facts',{}).get('value_ohm')!=value or len(c['pins'])!=2:continue
        pair={assigned.get((c['id'],p['pin_id'])) for p in c['pins']}
        if len(pair)!=2 or None in pair or any(nets[n]['kind']!='power' for n in pair):continue
        links.append((c['ref'],pair))
    before=set(reached)
    while True:
        previous=set(reached)
        for _,pair in links:
            if reached & pair:reached.update(pair)
        if reached==previous:break
    return reached-before,[dict(reference=ref,net_ids=sorted(pair)) for ref,pair in links if pair<=reached]
