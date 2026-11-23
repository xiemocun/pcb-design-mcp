"""Declared pin-to-pin geometry limits on actual native direct copper contacts.

Whole contacted straight tracks are counted conservatively. Pads are ideal nodes;
zones, package internals, inductance, delay and impedance are not solved here.
"""
import heapq
import math


def shortest_path(nodes,sources,targets,max_vias):
    if not sources or not targets:raise ValueError('PATH_PHYSICAL_PADS_MISSING')
    nets={nodes[u]['net'] for u in sources|targets}
    if len(nets)!=1:raise ValueError('PATH_ACTUAL_NET_MISMATCH')
    net=next(iter(nets));distance={};previous={};queue=[]
    for source in sorted(sources):
        distance[source,0]=0.;heapq.heappush(queue,(0.,source,0))
    while queue:
        cost,u,via_count=heapq.heappop(queue);state=(u,via_count)
        if cost!=distance[state]:continue
        if u in targets:
            items=[u]
            while state in previous:
                state=previous[state];items.append(state[0])
            return dict(length_upper_bound_mm=cost,via_count=via_count,items=list(reversed(items)))
        for v in sorted(nodes[u]['contacts']):
            if v not in nodes or nodes[v]['net']!=net:raise ValueError('PATH_NATIVE_CONTACT_INVALID')
            item=nodes[v];kind=item['kind']
            if kind not in ('pad','track','via'):raise ValueError('PATH_CONDUCTOR_UNSUPPORTED')
            length=item.get('length_mm',0.) if kind=='track' else 0.
            if type(length) not in (int,float) or not math.isfinite(length) or length<0:raise ValueError('PATH_LENGTH_INVALID')
            count=via_count+int(kind=='via')
            if count>max_vias:continue
            next_state=(v,count);candidate=cost+length
            if candidate<distance.get(next_state,float('inf')):
                distance[next_state]=candidate;previous[next_state]=(u,via_count);heapq.heappush(queue,(candidate,v,count))
    raise ValueError('PATH_NOT_FOUND_WITHIN_VIA_LIMIT')


def evaluate(state,actual,stage):
    measurements=[];findings=[];nodes=actual.get('direct_contacts',{}).get('nodes',{})
    for index,rule in enumerate(state['board_constraints'].get('path_constraints',[])):
        row=dict(check='declared_copper_path',index=index,max_length_mm=rule['max_length_mm'],max_vias=rule['max_vias'],scope='whole-track length upper bound and actual vias; no zone shortcut or timing/impedance proof')
        measurements.append(row)
        if stage!='routed':row.update(status='deferred_until_routed');continue
        try:
            def pads(endpoint):
                component=state['components'][endpoint['component_id']]
                pin=next(p for p in component['pins'] if p['pin_id']==endpoint['pin_id'])
                numbers=set(pin.get('physical_numbers',[pin['number']]))
                return {u for u,item in nodes.items() if item['kind']=='pad' and item['reference']==component['ref'] and item['number'] in numbers}
            result=shortest_path(nodes,pads(rule['source']),pads(rule['target']),rule['max_vias'])
            row.update(result,status='pass' if result['length_upper_bound_mm']<=rule['max_length_mm']+1e-6 else 'fail')
        except (KeyError,ValueError,StopIteration) as error:row.update(status='fail',reason=str(error))
        if row['status']!='pass':findings.append(dict(code='DECLARED_COPPER_PATH_FAILED',severity='error',message='Actual copper does not satisfy declared length/via limit: '+str(index),entity_ids=[rule['source']['component_id'],rule['target']['component_id']]))
    return dict(measurements=measurements,findings=findings)
