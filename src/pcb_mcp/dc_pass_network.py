"""Conditional DC voltage envelopes through exact-source series pass paths.

This propagates an explicit source's full current capacity as a conservative
path current, not a claim about actual load current or automatic enable state.
No amplification, feedback supplies, parallel drivers or cycles are inferred.
"""
def propagate(state,rails,models):
    ctx=state.get('power_envelope',{})
    if not ctx:return {},[]
    span=ctx.get('ambient_range_c') or [ctx['ambient_c'],ctx['ambient_c']]
    current=ctx['available_current_ma']/1000
    assigned={(p['component_id'],p['pin_id']):n['id'] for n in state['nets'].values() for p in n['pins']}
    def net(c,number):return next((assigned.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==number),None)
    derived={};rows=[];pending={cid:row for cid,row in models.items() if row[2].kind=='pass_switch'}
    for _ in range(len(pending)):
        progressed=False
        for cid,(key,model,path) in list(pending.items()):
            c=state['components'][cid];p=net(c,path.input_pin);n=net(c,path.output_pin)
            source=derived.get(p,rails.get(p))
            if not source:continue
            if net(c,path.ground_pin)!=ctx['ground_net_id'] or n is None or p==n or n==ctx['ground_net_id']:raise ValueError('DC_PASS_CONNECTION_INVALID: '+c['ref'])
            if n in rails or n in derived:raise ValueError('DC_PASS_PARALLEL_DRIVER: '+c['ref'])
            if not path.supply_range_v[0]<=source['min_v']<=source['max_v']<=path.supply_range_v[1]:raise ValueError('DC_PASS_SUPPLY_OUTSIDE_SCOPE: '+c['ref'])
            if not model['temperature_range_c'][0]<=span[0]<=span[1]<=model['temperature_range_c'][1]:raise ValueError('DC_PASS_TEMPERATURE_OUTSIDE_SCOPE: '+c['ref'])
            if current>path.maximum_continuous_current_a.minimum(span):raise ValueError('DC_PASS_SOURCE_CAPACITY_EXCEEDS_PATH: '+c['ref'])
            allowed={path.output_pin}
            if path.discharge_pin is not None:allowed.add(path.discharge_pin)
            for e in state['nets'][n]['pins']:
                other=state['components'][e['component_id']];pin=next(x for x in other['pins'] if x['pin_id']==e['pin_id'])
                if other['id']==cid and pin['number'] in allowed:continue
                if pin.get('electrical_type') in ('power_out','output','bidirectional','tri_state','open_collector','open_emitter'):
                    raise ValueError('DC_PASS_OUTPUT_HAS_ANOTHER_DRIVER: '+c['ref'])
                if other['id'] in models:
                    m=models[other['id']][2]
                    if m.kind=='pass_switch' and pin['number']==m.output_pin:raise ValueError('DC_PASS_OUTPUT_HAS_ANOTHER_DRIVER: '+c['ref'])
            drop=current*path.maximum_on_resistance_ohm
            if source['min_v']<=drop:raise ValueError('DC_PASS_NO_POSITIVE_OUTPUT_BOUND: '+c['ref'])
            derived[n]=dict(min_v=source['min_v']-drop,nominal_v=source['nominal_v'],max_v=source['max_v'],basis='conditional_all_series_paths_enabled_DC; source_capacity_times_source_bound_Ron',source_component=cid)
            rows.append(dict(component_id=cid,reference=c['ref'],model_id=key,input_net_id=p,output_net_id=n,path_current_bound_a=current,maximum_DC_drop_v=drop,temperature_range_c=span,enable_verified=False,off_leakage_verified=False,transient_verified=False))
            del pending[cid];progressed=True
        if not progressed:break
    return derived,rows
