"""Actual-network linear DC analysis; ideal reference, never physical qualification.

Positive resistor networks have fractional-linear node voltages in each
conductance and affine dependence on ideal source voltages. Exhaustive corners
therefore bound node voltages and source currents. Resistor power uses the
conservative voltage-difference bound squared divided by minimum resistance;
corner-only power extrema would not be safe.
"""
import hashlib
import itertools
import json
import math
import numpy as np
from . import parts


def eligible(s):
    bindings=s.get('part_bindings',{})
    return bool(s['components']) and all(
        bindings.get(c['id'],{}).get('kind') in ('resistor','capacitor','connector')
        and (c['library_id'] in ('Device:R','Device:C','Device:C_Polarized')
             or c['library_id'].startswith('Connector_Generic:'))
        for c in s['components'].values())


def solve(nodes,ground,resistors,sources):
    """MNA with explicit source orientation; rejects floating/singular networks."""
    names=sorted(set(nodes)-{ground});index={n:i for i,n in enumerate(names)}
    size=len(names)+len(sources)
    a=np.zeros((size,size));b=np.zeros(size)
    for p,n,r in resistors:
        if not math.isfinite(r) or r<=0:raise ValueError('NONPOSITIVE_RESISTANCE')
        for x,y,sign in ((p,p,1),(n,n,1),(p,n,-1),(n,p,-1)):
            if x!=ground and y!=ground:a[index[x],index[y]]+=sign/r
    for k,(p,n,v) in enumerate(sources):
        col=len(names)+k
        for node,sign in ((p,1),(n,-1)):
            if node!=ground:a[index[node],col]+=sign;a[col,index[node]]+=sign
        b[col]=v
    try:x=np.linalg.solve(a,b)
    except np.linalg.LinAlgError as exc:raise ValueError('FLOATING_OR_CONFLICTING_DC_NETWORK') from exc
    if not np.all(np.isfinite(x)) or not np.allclose(a@x,b,rtol=1e-8,atol=1e-10):raise ValueError('DC_NUMERICAL_RESIDUAL')
    return dict({ground:0.},**{n:float(x[i]) for n,i in index.items()}),[-float(v) for v in x[len(names):]]


def analyze(s,max_corners=256):
    if not eligible(s):return None
    findings=[];resistors=[];sources=[];capacitors=[]
    def add(code,message,severity='warning'):findings.append(dict(code=code,message=message,severity=severity))
    current={p['candidate_id']:p for p in parts.catalog()}
    assignment={(p['component_id'],p['pin_id']):n['id'] for n in s['nets'].values() for p in n['pins']}
    ctx=s.get('power_envelope',{})
    for c in s['components'].values():
        p=s['part_bindings'][c['id']];f=p['facts']
        if current.get(p['candidate_id'])!=p or p['evidence_status']!='available' or not parts.binding_valid(s,c,p):
            add('DC_PART_BINDING_STALE',c['ref']+' identity, source or package is stale.','error');continue
        if p['kind']=='connector':continue
        ns=[assignment.get((c['id'],pin['pin_id'])) for pin in c['pins']]
        if len(ns)!=2 or None in ns:add('DC_PASSIVE_CONNECTIVITY',c['ref']+' requires two connected physical terminals.','error');continue
        if p['kind']=='capacitor':
            capacitors.append(dict(reference=c['ref'],nets=ns));continue
        value=f['value_ohm'];tol=f.get('tolerance_fraction');tcr=f.get('temperature_coefficient_ppm_per_k');span=f.get('temperature_range_c')
        lo=hi=value
        if tol is None or tcr is None or not span:
            add('DC_RESISTOR_INTERVAL_MISSING',c['ref']+' lacks tolerance or temperature evidence; nominal only.')
        else:
            # Full source-specified temperature span avoids inventing a reference temperature.
            delta=abs(tcr)*1e-6*(span[1]-span[0]);lo=value*(1-tol)*(1-delta);hi=value*(1+tol)*(1+delta)
            if ctx and not span[0]<=ctx['ambient_c']<=span[1]:add('DC_TEMPERATURE_OUT_OF_RANGE',c['ref']+' ambient outside source range.','error')
        if not 0<lo<=hi or not math.isfinite(hi):add('DC_RESISTOR_INTERVAL_INVALID',c['ref']+' invalid positive resistance interval.','error');continue
        resistors.append(dict(reference=c['ref'],component_id=c['id'],nets=ns,nominal=value,minimum=lo,maximum=hi,facts=f,part_hash=p['definition_hash']))
    for source in s.get('power_sources',{}).values():
        p=source['boundary_pin'];nid=assignment.get((p['component_id'],p['pin_id']))
        if nid!=source['net_id'] or source['ground_net_id'] not in s['nets']:
            add('SUPPLY_ENVELOPE_BOUNDARY_STALE','Source boundary no longer matches actual nets.','error');continue
        matched=bool(ctx and ctx['positive_pin']==p and ctx['ground_net_id']==source['ground_net_id'] and ctx['nominal_v']==source['nominal_v'])
        if not matched:add('DC_SOURCE_INTERVAL_MISSING','Source has nominal voltage only; input range and capacity are unverified.')
        sources.append(dict(nets=[nid,source['ground_net_id']],nominal=source['nominal_v'],minimum=ctx['min_v'] if matched else source['nominal_v'],maximum=ctx['max_v'] if matched else source['nominal_v'],capacity_a=ctx['available_current_ma']/1000 if matched else None))
    if ctx and not any(x['capacity_a'] is not None for x in sources):add('SUPPLY_ENVELOPE_BOUNDARY_STALE','Envelope is not bound to an actual source.','error')
    if not sources:add('DC_SOURCE_MISSING','An explicit source boundary is required.','error')
    if capacitors:add('DC_CAPACITOR_IDEALIZATION','Capacitors are open at DC; source-specific leakage and startup remain unmodeled.')
    if s.get('power_budget_policy',{}).get('loads') or s.get('power_budget_policy',{}).get('stages') or s.get('power_budget_policy',{}).get('reserves'):
        add('DC_EXTRA_LOAD_POLICY_UNMODELED','Additional load declarations require explicit network models; cannot ignore existing budget policy.','error')
    result=dict(findings=findings,rails={},resistors=[],source_currents=[],corners=0,nominal_voltages={},model='positive_resistor_network_ideal_sources_dc',physical_qualified=False)
    if any(f['severity']=='error' for f in findings):result['verdict']='fail';return result
    axes=[sorted(set([x['minimum'],x['maximum']])) for x in resistors+sources]
    count=math.prod(len(x) for x in axes)
    if count>max_corners:
        add('DC_CORNER_RESOURCE_LIMIT',f'{count} corners exceed declared limit {max_corners}; no sampled pass.');result['verdict']='incomplete';return result
    def run(values):
        nr=len(resistors)
        return solve(s['nets'],sources[0]['nets'][1],[(r['nets'][0],r['nets'][1],v) for r,v in zip(resistors,values[:nr])],[(r['nets'][0],r['nets'][1],v) for r,v in zip(sources,values[nr:])])
    try:
        nominal,nominal_currents=run([x['nominal'] for x in resistors+sources])
        samples=[run(values) for values in itertools.product(*axes)]
    except ValueError as exc:
        add(str(exc),'DC solver could not uniquely solve the actual network.','error');result['verdict']='fail';return result
    result['corners']=count;result['nominal_voltages']=nominal
    result['rails']={nid:dict(min_v=min(v[nid] for v,_ in samples),max_v=max(v[nid] for v,_ in samples),nominal_v=nominal[nid],basis='exhaustive_linear_DC_parameter_corners') for nid in s['nets']}
    for i,source in enumerate(sources):
        lower=min(a[i] for _,a in samples);upper=max(a[i] for _,a in samples)
        result['source_currents'].append(dict(net_id=source['nets'][0],minimum_a=lower,maximum_a=upper,nominal_a=nominal_currents[i]))
        if source['capacity_a'] is not None and upper>source['capacity_a']+1e-12:add('BUDGET_SOURCE_CAPACITY','Computed source current exceeds declared source capacity.','error')
        if lower<-1e-12:add('DC_SOURCE_REVERSE_CURRENT','Source must absorb current; sink capability is not declared.')
    for r in resistors:
        p,n=r['nets'];voltage=max(abs(v[p]-v[n]) for v,_ in samples);power=voltage**2/r['minimum'];f=r['facts']
        result['resistors'].append(dict(reference=r['reference'],component_id=r['component_id'],maximum_voltage_v=voltage,conservative_power_w=power,maximum_current_a=voltage/r['minimum'],resistance_interval_ohm=[r['minimum'],r['maximum']],part_definition_hash=r['part_hash']))
        if f.get('voltage_rating_v') is None or f.get('rated_power_w') is None:add('DC_PASSIVE_RATING_MISSING',r['reference']+' lacks voltage or power rating.')
        else:
            if voltage>f['voltage_rating_v']:add('DC_RESISTOR_VOLTAGE_EXCEEDED',r['reference']+' exceeds voltage rating.','error')
            if power>f['rated_power_w']:add('DC_RESISTOR_POWER_EXCEEDED',r['reference']+' exceeds reference power rating.','error')
    result['verdict']='fail' if any(f['severity']=='error' for f in findings) else 'incomplete' if findings else 'pass'
    return result


def budget_report(s,bom,analysis):
    from .editing import spec_hash
    findings=list(analysis['findings'])+[dict(f,code='BUDGET_BOM_CONFLICT') for f in bom['findings'] if f['severity']=='error']
    errors=any(f['severity']=='error' for f in findings)
    conditional=analysis['verdict']=='pass' and not errors
    findings.append(dict(code='DC_REFERENCE_PHYSICS_UNQUALIFIED',severity='warning',message='Ideal steady-state model only; actual thermal, parasitic, leakage and transient behavior are not qualified.'))
    return dict(project_id=s['id'],revision=s['revision'],verdict='fail' if errors else 'incomplete',budget_verdict='fail' if errors else 'conditional' if conditional else 'incomplete',coverage=dict(model=analysis['model'],spec_sha256=spec_hash(s),corners=analysis['corners'],all_actual_loads_verified=False),findings=findings,rails=[dict(net_id=nid,**row) for nid,row in analysis['rails'].items()],loads=[],stages=[],passive_loads=analysis['resistors'],declared_dc_reference_input_a=sum(max(0,x['maximum_a']) for x in analysis['source_currents']) if conditional else None,actual_input_current_upper_bound_a=None,physical_qualified=False,manufacturing_approved=False,evidence_hash=hashlib.sha256(json.dumps(analysis,sort_keys=True).encode()).hexdigest(),qualification='source_bound_linear_DC_reference_not_physical_qualification')
