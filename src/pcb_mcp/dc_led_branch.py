"""DC LED stress bounds from exact-part temperature ratings and actual nets.

Supports a series resistor/LED branch to ground from an explicit supply or an
exact-part rated passive high-side switch. Switching, brightness and inductive
transients require separate functional evidence and are not qualified here.
"""
from . import parts


def evaluate(state,component,models):
    key,model,limits=models[component['id']]
    if limits.kind!='led':raise ValueError('LED_DC_MODEL_KIND_MISMATCH')
    ctx=state.get('power_envelope',{})
    if not ctx:raise ValueError('LED_BRANCH_ENVELOPE_REQUIRED')
    span=ctx.get('ambient_range_c') or [ctx['ambient_c'],ctx['ambient_c']]
    if span[0]<model['temperature_range_c'][0] or span[1]>model['temperature_range_c'][1]:
        raise ValueError('LED_BRANCH_TEMPERATURE_UNSUPPORTED')
    assigned={(p['component_id'],p['pin_id']):n['id'] for n in state['nets'].values() for p in n['pins']}
    def net(c,number):return next((assigned.get((c['id'],p['pin_id'])) for p in c['pins'] if p['number']==number),None)
    ground=ctx['ground_net_id'];anode=net(component,limits.anode_pin);cathode=net(component,limits.cathode_pin)
    if ground is None or cathode!=ground or anode is None or anode==ground:
        raise ValueError('LED_DC_BRANCH_POLARITY_OR_TOPOLOGY')
    sources={s['net_id'] for s in state.get('power_sources',{}).values()
             if s['boundary_pin']==ctx['positive_pin'] and s['ground_net_id']==ground
             and s['nominal_v']==ctx['nominal_v']
             and assigned.get((ctx['positive_pin']['component_id'],ctx['positive_pin']['pin_id']))==s['net_id']}
    # Rated switches only transmit the explicit input voltage; they do not
    # create a new source or prove a fan starts or a control signal is correct.
    feeds=dict.fromkeys(sources,None)
    for cid,(switch_key,switch_model,switch) in models.items():
        if switch.kind!='pass_switch':continue
        c=state['components'][cid]
        if net(c,switch.input_pin) not in sources or net(c,switch.ground_pin)!=ground:continue
        if not switch.supply_range_v[0]<=ctx['min_v']<=ctx['max_v']<=switch.supply_range_v[1]:continue
        if span[0]<switch_model['temperature_range_c'][0] or span[1]>switch_model['temperature_range_c'][1]:continue
        switch.maximum_continuous_current_a.minimum(span)
        out=net(c,switch.output_pin)
        if out is not None and out!=ground and out not in feeds:
            feeds[out]=(c,switch_key,switch)
    resistors={c['id']:c for c in state['components'].values() if c['library_id']=='Device:R'}
    node=anode;used=[];seen=set()
    while node not in feeds:
        # No alternative load or active driver may share an interior branch.
        if len(state['nets'][node]['pins'])!=2:raise ValueError('LED_DC_BRANCH_HAS_TAP')
        choices=[(c,{net(c,'1'),net(c,'2')}) for cid,c in resistors.items()
                 if cid not in seen and node in {net(c,'1'),net(c,'2')}]
        if len(choices)!=1:raise ValueError('LED_BRANCH_UNSUPPORTED')
        c,ends=choices[0]
        if None in ends or len(ends)!=2:raise ValueError('LED_BRANCH_UNSUPPORTED')
        seen.add(c['id']);used.append(c);node=next(iter(ends-{node}))
    if not used:raise ValueError('LED_DC_CURRENT_LIMIT_RESISTOR_REQUIRED')
    # A second output can raise the feed above its supposed passive input
    # bound. Do not silently choose one of several parallel switch outputs.
    selected=feeds[node]
    allowed=set()
    if node in sources:allowed.add((ctx['positive_pin']['component_id'],ctx['positive_pin']['pin_id']))
    if selected is not None:
        c,_,switch=selected
        allowed.add((c['id'],next(p['pin_id'] for p in c['pins'] if p['number']==switch.output_pin)))
        # A source-qualified discharge terminal only sinks this same output.
        # It is not an arbitrary exception for a second active supply driver.
        if switch.discharge_pin is not None and net(c,switch.discharge_pin)==node:
            allowed.add((c['id'],next(p['pin_id'] for p in c['pins'] if p['number']==switch.discharge_pin)))
    for endpoint in state['nets'][node]['pins']:
        identity=(endpoint['component_id'],endpoint['pin_id'])
        if identity in allowed:continue
        c=state['components'][endpoint['component_id']]
        pin=next(p for p in c['pins'] if p['pin_id']==endpoint['pin_id'])
        if pin.get('electrical_type') in ('output','power_out','bidirectional','tri_state','open_collector','open_emitter'):
            raise ValueError('LED_DC_FEED_HAS_ANOTHER_DRIVER')
        if c['id'] in models:
            other=models[c['id']][2]
            if other.kind=='pass_switch' and pin['number']==other.output_pin:
                raise ValueError('LED_DC_FEED_HAS_ANOTHER_DRIVER')
    total=0.;maximum=[]
    for c in used:
        part=state.get('part_bindings',{}).get(c['id'])
        if not part:raise ValueError('LED_BRANCH_PART_REQUIRED')
        f=part['facts'];tr=f['temperature_range_c']
        if span[0]<tr[0] or span[1]>tr[1]:raise ValueError('LED_RESISTOR_TEMPERATURE_OUTSIDE_SCOPE')
        # Full specified resistor temperature span is conservative wrt TCR,
        # including self-heating; rated-power temperature qualification remains
        # an explicit source requirement, not inferred from nominal resistance.
        fraction=f['temperature_coefficient_ppm_per_k']*1e-6*(tr[1]-tr[0])
        low=f['value_ohm']*(1-f['tolerance_fraction'])*(1-fraction)
        high=f['value_ohm']*(1+f['tolerance_fraction'])*(1+fraction)
        if low<=0:raise ValueError('LED_DC_RESISTOR_BOUND_INVALID')
        total+=low;maximum.append((c,part,high))
    current=ctx['max_v']/total;power=ctx['max_v']**2/(4*total)
    current_limit=.5*limits.maximum_forward_current_a.minimum(span)
    power_limit=.5*limits.maximum_power_w.minimum(span)
    if current>current_limit:raise ValueError('LED_BRANCH_CURRENT_MARGIN')
    if power>power_limit:raise ValueError('LED_BRANCH_POWER_MARGIN')
    for c,part,high in maximum:
        if current**2*high>.5*part['facts']['rated_power_w']:raise ValueError('LED_BRANCH_RESISTOR_POWER')
    switch=feeds[node]
    if switch is not None and current>switch[2].maximum_continuous_current_a.minimum(span):
        raise ValueError('LED_DC_SWITCH_BRANCH_CAPACITY')
    return dict(reference=component['ref'],model_id=key,temperature_range_c=list(span),
        feed='explicit_source' if switch is None else switch[0]['ref'],
        switch_model_id=None if switch is None else switch[1],
        series_resistors=[c['ref'] for c in used],minimum_series_reference_ohm=total,
        dc_current_upper_reference_a=current,led_power_upper_reference_w=power,
        current_policy_limit_a=current_limit,power_policy_limit_w=power_limit,
        basis='Source-linked piecewise DC ratings; all interior temperature breakpoints included; passive high-side feed; forward Vf>=0. No reverse/transient/brightness/control/fan qualification.')
