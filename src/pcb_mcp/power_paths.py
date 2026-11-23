"""Conservative selected-whole-track path reference, not solved branch voltages.

Direct KiCad contacts form the graph. Every selected straight track is charged
its entire R, including beyond a mid-segment junction. Pads/vias/package remain
ideal. This bounds selected track portions, not complete physical resistance.
"""
import heapq,math
from .pin_roles import ground_names


def selected_path(nodes,sources,targets,thickness_um,layer_thickness_um=None):
    if not sources or not targets:raise ValueError('Missing physical source or target pads')
    if not math.isfinite(thickness_um) or thickness_um<=0:raise ValueError('Invalid thickness')
    def resistance(row):
        if row['kind'] in ['pad','via']:return 0.
        if row['kind']!='track':raise ValueError('Unsupported conductor in path graph')
        length,width=row['length_mm'],row['width_mm']
        if not math.isfinite(length) or length<0 or not math.isfinite(width) or width<=0:raise ValueError('Invalid track dimensions')
        thickness=thickness_um
        if layer_thickness_um is not None:
            layer=row.get('layer')
            if layer not in layer_thickness_um:raise ValueError('Path track uses an undeclared copper layer')
            thickness=layer_thickness_um[layer]
            if not math.isfinite(thickness) or thickness<=0:raise ValueError('Invalid layer thickness')
        return 1.72e-8*length*1e6/(width*thickness)
    net={nodes[u]['net'] for u in sources|targets}
    if len(net)!=1:raise ValueError('Path endpoints must share one actual net')
    net=next(iter(net));distance={u:0. for u in sources};previous={};queue=[(0.,u) for u in sources];heapq.heapify(queue)
    while queue:
        cost,u=heapq.heappop(queue)
        if cost!=distance[u]:continue
        if u in targets:
            path=[u]
            while u in previous:u=previous[u];path.append(u)
            path.reverse()
            return dict(track_reference_ohm=cost,items=path,source=path[0],destination=path[-1],selected_track_length_mm=sum(nodes[v].get('length_mm',0.) for v in path),ideal_via_count=sum(nodes[v]['kind']=='via' for v in path))
        for v in nodes[u]['contacts']:
            if v not in nodes or nodes[v]['net']!=net:raise ValueError('Invalid direct contact net')
            q=cost+resistance(nodes[v])
            if q<distance.get(v,float('inf')):distance[v]=q;previous[v]=u;heapq.heappush(queue,(q,v))
    raise ValueError('No actual direct-contact path')


def loop_legs(state, loop, rules):
    """Bind every declared leg to two real pins on one ruled net."""
    assignments = {(p['component_id'], p['pin_id']): n for n in state['nets'].values() for p in n['pins']}
    components = state['components']
    legs = loop['legs']
    for leg, following in zip(legs, legs[1:] + legs[:1]):
        if leg['target']['component_id'] != following['source']['component_id']:
            raise ValueError('COPPER_LOOP_OPEN_COMPONENT_SEQUENCE')

    used_pins, used_nets = set(), set()
    bound = []
    kinds = set()
    for leg in legs:
        if leg['source']['component_id'] == leg['target']['component_id']:
            raise ValueError('COPPER_LOOP_LEG_REQUIRES_DISTINCT_COMPONENTS')
        terminals = []
        nets = []
        for name in ('source', 'target'):
            endpoint = leg[name]
            component = components.get(endpoint['component_id'])
            key = (endpoint['component_id'], endpoint['pin_id'])
            if key in used_pins:
                raise ValueError('COPPER_LOOP_REPEATED_PIN')
            used_pins.add(key)
            pin = next((p for p in component['pins'] if p['pin_id'] == key[1]), None) if component else None
            if pin is None or key not in assignments:
                raise ValueError('COPPER_LOOP_PIN_NOT_CONNECTED')
            terminals.append(dict(ref=component['ref'], numbers=pin.get('physical_numbers', [pin['number']])))
            nets.append(assignments[key])
        net, target_net = nets
        if net['id'] != target_net['id'] or net['id'] not in rules:
            raise ValueError('COPPER_LOOP_LEG_NEEDS_ONE_RULED_NET')
        if net['id'] in used_nets:
            raise ValueError('COPPER_LOOP_REPEATED_NET')
        used_nets.add(net['id'])
        kinds.add(net['kind'])
        bound.append((net, terminals))
    if not {'power', 'ground'} <= kinds:
        raise ValueError('COPPER_LOOP_REQUIRES_POWER_AND_GROUND')
    return bound


def evaluate_loops(state, actual, verified):
    from .copper import layer_thicknesses

    policy = state.get('pcb_copper_policy', {})
    loops = policy.get('dc_loops', [])
    if not loops:
        return []
    rules = {r['net_id']: r for r in policy['rules']}
    nodes = actual.get('direct_contacts', {}).get('nodes', {})
    thicknesses = layer_thicknesses(state, policy)
    results = []
    for loop in loops:
        row = dict(name=loop['name'], maximum_track_drop_v=loop['maximum_track_drop_v'], legs=[],
                   status='incomplete', scope='Selected whole straight tracks at 20C and declared net currents. Pads, plated barrels, zones and inter-pin component behavior are excluded.')
        results.append(row)
        if not verified:
            row['reason'] = 'COPPER_LOOP_NATIVE_CONNECTIVITY_UNVERIFIED'
            continue
        try:
            for net, endpoints in loop_legs(state, loop, rules):
                pads = []
                for endpoint in endpoints:
                    ids = {uid for uid, item in nodes.items() if item['kind'] == 'pad'
                           and item['reference'] == endpoint['ref'] and item['number'] in endpoint['numbers']}
                    if len(ids) != 1 or any(nodes[uid]['net'] != net['name'] for uid in ids):
                        raise ValueError('COPPER_LOOP_NATIVE_PAD_OR_NET_MISMATCH')
                    pads.append(ids)
                path = selected_path(nodes, *pads, policy['copper_thickness_um'], thicknesses)
                current = rules[net['id']]['design_current_a']
                row['legs'].append(dict(net=net['name'], source=endpoints[0], target=endpoints[1],
                                        design_current_a=current, drop_reference_v=path['track_reference_ohm']*current,
                                        path=path))
            total = sum(leg['drop_reference_v'] for leg in row['legs'])
            row.update(track_drop_reference_v=total, status='pass' if total <= loop['maximum_track_drop_v'] else 'fail')
            if row['status'] == 'fail':
                row['reason'] = 'COPPER_LOOP_TRACK_DROP_EXCEEDS_BUDGET'
        except (KeyError, ValueError) as error:
            row.update(status='fail', reason=str(error))
    return results


def evaluate(s,actual,rails):
    nodes=actual.get('direct_contacts',{}).get('nodes',{});policy=s.get('pcb_copper_policy',{})
    if not nodes or not policy:return []
    comps=s['components'];assignment={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    rules={r['net_id']:r for r in policy.get('rules',[])};rows=[]
    def pins(c,name):return [p for p in c['pins'] if p['name']==name]
    def net(c,name):return next((assignment.get((c['id'],p['pin_id'])) for p in pins(c,name)),None)
    def gp(c):return [p for name in ground_names(s,c) for p in pins(c,name)]
    def gn(c):return next((assignment.get((c['id'],p['pin_id'])) for p in gp(c)),None)
    def pads(c,pp):
        numbers={n for p in pp for n in p.get('physical_numbers',[p['number']])}
        return {u for u,x in nodes.items() if x['kind']=='pad' and x['reference']==c['ref'] and x['number'] in numbers}
    stages=[comps[x['component_id']] for x in s.get('power_budget_policy',{}).get('stages',[]) if x['component_id'] in comps]
    targets=list(s.get('power_budget_policy',{}).get('loads',[]))
    for c in stages:
        for pin in pins(c,'IN'):targets.append(dict(pin=dict(component_id=c['id'],pin_id=pin['pin_id']),scope='stage_input_terminal'))
    for load in targets:
        c=comps[load['pin']['component_id']];pin=next(p for p in c['pins'] if p['pin_id']==load['pin']['pin_id']);nid=assignment.get((c['id'],pin['pin_id']));gnd=gn(c)
        source=[(sc,pins(sc,'OUT')) for sc in stages if net(sc,'OUT')==nid]
        if not source:
            for boundary in s.get('power_sources',{}).values():
                if boundary['net_id']!=nid:continue
                sc=comps[boundary['boundary_pin']['component_id']]
                source.append((sc,[p for p in sc['pins'] if p['pin_id']==boundary['boundary_pin']['pin_id']]))
        row=dict(reference=c['ref'],supply_pin=pin['name'],status='incomplete',physical_qualified=False)
        rows.append(row)
        if len(source)!=1 or gnd not in rules or nid not in rules or nid not in rails:
            row['reason']='No unique stage, declared current rules or source voltage';continue
        sc,source_pins=source[0]
        if gn(sc)!=gnd:row['reason']='Source/load ground topology unsupported';continue
        try:
            from .copper import layer_thicknesses
            thicknesses=layer_thicknesses(s,policy)
            feed=selected_path(nodes,pads(sc,source_pins),pads(c,[pin]),policy['copper_thickness_um'],thicknesses)
            ground=selected_path(nodes,pads(sc,gp(sc)),pads(c,gp(c)),policy['copper_thickness_um'],thicknesses)
        except (ValueError,KeyError) as error:row['reason']=str(error);continue
        fi=rules[nid]['design_current_a'];gi=rules[gnd]['design_current_a'];drop=feed['track_reference_ohm']*fi+ground['track_reference_ohm']*gi
        bound=s.get('part_bindings',{}).get(c['id'],{}).get('facts',{}).get('supply_min_v');lower=rails[nid]['min_v']-drop
        row.update(source_reference=sc['ref'],feed=feed,ground_return=ground,feed_current_assumption_a=fi,ground_current_assumption_a=gi,source_lower_reference_v=rails[nid]['min_v'],track_drop_reference_v=drop,load_lower_reference_v=lower,required_min_v=bound,margin_reference_v=lower-bound if bound is not None else None,status='within_reference' if bound is not None and lower>=bound else 'margin_not_established' if bound is not None else 'incomplete',scope='selected whole straight-track R at20C; declared per-net currents; ideal pads/vias/package; no mesh/branch-current or ripple/transient solution')
    return rows
