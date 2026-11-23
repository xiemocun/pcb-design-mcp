"""Source-bounded, unloaded static logic current; no switching-current claim.

Icc upper bounds are source data at exact published test inputs. A lower bound
is derived explicitly from nonnegative steady-state dissipation and bounded
input power; it is not a manufacturer's unstated minimum-Icc specification.
"""
import hashlib,math
from pathlib import Path


def runtime_hash():
    root=Path(__file__).parent
    return hashlib.sha256(b''.join((root/name).read_bytes() for name in ['digital_power.py','digital_truth.py','verification_models.py'])).hexdigest()


def reference(state,scenario,models,records,mapping,logic):
    from .functional import Terminal,resolve_terminal
    def net(ref,pin):return resolve_terminal(mapping,Terminal(ref=ref,number=pin))
    voltages=logic['net_voltage_intervals'];bounds={nid:[0.,0.] for nid in state['nets']};components=[];findings=[];evidence=[]
    def missing(code,ref):findings.append(dict(code=code,reference=ref));return False
    def add(nid,low,high):bounds[nid][0]+=low;bounds[nid][1]+=high
    active={ref:model for ref,(model,_) in models.items() if model['engine']=='digital_truth'}
    for component in state['components'].values():
        ref=component['ref'];part=records[ref]
        if part['kind']=='connector':continue
        if part['kind']=='capacitor':
            nodes=[mapping.get((ref,p['number'])) for p in component['pins']]
            if len(nodes)!=2 or any(n not in voltages for n in nodes):missing('DIGITAL_CAPACITOR_VOLTAGE_UNCOVERED',ref);continue
            p,n=[voltages[x] for x in nodes];maximum=max(abs(p[0]-n[1]),abs(p[1]-n[0]));rating=part['facts'].get('voltage_rating_v')
            if rating is None:missing('DIGITAL_CAPACITOR_RATING_UNCOVERED',ref);continue
            if maximum>rating:raise ValueError('SIMULATED_PASSIVE_VOLTAGE_EXCEEDED: '+ref)
            components.append(dict(reference=ref,maximum_voltage_v=maximum,maximum_dissipation_w=0.,scope='ideal capacitor open at DC; leakage/startup not modeled'));continue
        if ref not in active:missing('DIGITAL_STATIC_MODEL_UNCOVERED',ref);continue
        model=active[ref];current=model.get('static_supply_current');truth=model['truth']
        if not current:missing('DIGITAL_QUIESCENT_CURRENT_MODEL_MISSING',ref);continue
        ppin,gpin=truth['supply'];pn,gn=net(ref,ppin),net(ref,gpin)
        if pn not in voltages or gn not in voltages:missing('DIGITAL_STATIC_SUPPLY_UNDRIVEN',ref);continue
        p,g=voltages[pn],voltages[gn];supply=p[0]-g[0]
        if p[0]!=p[1] or g[0]!=g[1] or supply<=0:missing('DIGITAL_STATIC_FIXED_BIAS_REQUIRED',ref);continue
        inputs=[];valid=True
        for pin in truth['inputs']:
            nid=net(ref,pin)
            if nid not in voltages:valid=missing('DIGITAL_STATIC_INPUT_UNDRIVEN',ref);continue
            low,high=voltages[nid];level=low-g[0]
            if low!=high or not any(math.isclose(level,x,rel_tol=0,abs_tol=1e-12) for x in current['input_test_levels_v']):valid=missing('DIGITAL_QUIESCENT_INPUT_CONDITION_UNCOVERED',ref)
            inputs.append((pin,nid,level))
        for pin in truth['outputs']:
            output=net(ref,pin)
            for other,other_model in active.items():
                if any(net(other,input_pin)==output and other_model['truth']['input_leakage_max_a']>0 for input_pin in other_model['truth']['inputs']):valid=missing('DIGITAL_QUIESCENT_UNLOADED_OUTPUT_REQUIRED',ref)
                if net(other,other_model['truth']['supply'][0])==output:valid=missing('DIGITAL_QUIESCENT_UNLOADED_OUTPUT_REQUIRED',ref)
        if not valid:continue
        leakage=truth['input_leakage_max_a'];lower=-sum(max(0.,level)*leakage for _,_,level in inputs)/supply;upper=current['maximum_a']
        terminal={ppin:[lower,upper]};add(pn,lower,upper)
        for pin,nid,level in inputs:terminal[pin]=[-leakage,leakage];add(nid,-leakage,leakage)
        for pin in truth['outputs']:terminal[pin]=[0.,0.]
        terminal[gpin]=[-sum(v[1] for v in terminal.values()),-sum(v[0] for v in terminal.values())]
        add(gn,*terminal[gpin])
        power=supply*upper+sum(max(0.,level)*leakage for _,_,level in inputs)
        for limit in model.get('voltage_limits',[]):
            a,b=voltages[net(ref,limit['positive'])],voltages[net(ref,limit['negative'])]
            if a[0]-b[1]<limit['minimum_v']-1e-12 or a[1]-b[0]>limit['maximum_v']+1e-12:raise ValueError('SIMULATED_MODEL_VOLTAGE_LIMIT: '+ref)
        if model.get('power_limit_w') is not None and power>model['power_limit_w']:raise ValueError('SIMULATED_MODEL_POWER_LIMIT: '+ref)
        if any(max(abs(v) for v in terminal[pin])>limit+1e-12 for pin,limit in model.get('terminal_current_limits_a',{}).items()):raise ValueError('SIMULATED_MODEL_CURRENT_LIMIT: '+ref)
        components.append(dict(reference=ref,supply_current_interval_a=[lower,upper],terminal_current_intervals_a=terminal,maximum_dissipation_w=power,scope='Published unloaded static Icc upper bound; lower bound from declared nonnegative steady-state dissipation and bounded input power, not published minimum Icc or thermal qualification'))
        evidence.append(dict(reference=ref,model_id=models[ref][1].name,part_definition_hash=part['definition_hash'],conditions=model['conditions'],static_supply_conditions=current['conditions']))
    complete=not findings and bool(scenario.drives);sources=[]
    if complete:
        # Every non-ground net must have a measured driver or an unloaded output.
        for i,drive in enumerate(scenario.drives):
            pn=net(drive.positive.ref,drive.positive.number);gn=net(drive.negative.ref,drive.negative.number)
            sources.append(dict(net_id=pn,ground_net_id=gn,drive_kind='voltage',min_v=drive.dc_v,max_v=drive.dc_v,min_current_a=bounds[pn][0],max_current_a=bounds[pn][1],pulse=None,analysis_scope='datasheet_bounded_static_truth'))
        # Whole-board nonnegative steady-state dissipation tightens only the
        # lower bound. Other independently driven inputs may backfeed a supply.
        for index,row in enumerate(sources):
            if row['min_v']>0:
                other_power=sum(max(0.,other['max_v']*other['max_current_a']) for j,other in enumerate(sources) if j!=index)
                row['min_current_a']=max(row['min_current_a'],-other_power/row['min_v'])
    return dict(sources=sources,component_stress=components,static_supply_reference_complete=complete,stress_reference_complete=False,temperature_c=scenario.temperature_c,findings=findings,models=evidence,scope='Source-bounded unloaded static logic and ideal-DC-capacitor reference; declared nonnegative static dissipation; no switching, dynamic load, capacitance leakage or physical thermal qualification')
