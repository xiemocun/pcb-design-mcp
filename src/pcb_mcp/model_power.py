"""Current/stress reference from retained, current, source-bound solver runs.

This supplements unknown generic profiles for prototype review only. It never
replaces a known fixed-family error or establishes an actual current bound.
"""
import json,math
from . import parts,registry
from .editing import spec_hash


def _source_matches(run,row,source):
    """Only a fixed voltage or one complete ramp into a nominal plateau.

    Peaks and reverse current still include the entire trace, including startup.
    Repeated power cycling cannot stand in for a regulated nominal supply.
    """
    if row.get('non_sinking_supply') is not None or row.get('drive_kind','voltage')!='voltage' or row['net_id']!=source['net_id'] or row['ground_net_id']!=source['ground_net_id']:return False
    if any(row.get(field) is not None for field in ('pwl','sine','series_resistance_ohm','series_connection')):return False
    nominal=source['nominal_v'];pulse=row['pulse']
    if run['engine'] in ('spice_op','digital_truth'):return pulse is None and row['min_v']==nominal==row['max_v']
    duration=run['electrical_reference'].get('duration_s')
    if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0:return False
    if pulse is None:return row['min_v']==nominal==row['max_v']==row.get('start_v')==row.get('end_v')
    if len(pulse)!=7 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in pulse):return False
    low,high,delay,rise,fall,width,period=pulse
    return (low==0 and high==nominal and nominal>0 and delay>=0 and rise>0 and fall>0 and width>0
            and period>duration and delay+rise<duration<=delay+rise+width
            and row['min_v']==row.get('start_v')==0 and row['max_v']==row.get('end_v')==nominal)


def read_report(folder, entry):
    key=entry['run_id']
    if len(key)!=32 or any(c not in '0123456789abcdef' for c in key):raise ValueError('INVALID_FUNCTIONAL_INDEX')
    raw=(folder/'functional_runs'/key/'report.json').read_bytes()
    if registry.sha(raw)!=entry['report_sha256']:raise ValueError('FUNCTIONAL_REPORT_CHANGED')
    return json.loads(raw)


def runtime_matches(run):
    if run['engine']=='linear_dc':return True
    from . import digital_power,spice
    solver=run.get('solver',{})
    if not solver.get('runtime_sha256'):return False
    try:
        current=(digital_power.runtime_hash() if run['engine']=='digital_truth'
                 else spice.runtime_hash(spice.runtime_identity(solver.get('dialect','native'))))
    except (OSError,ValueError):
        return False
    return solver['runtime_sha256']==current


def reference(s,replacement_evidence=None):
    ctx=s.get('power_envelope',{})
    if not ctx or not ctx['min_v']==ctx['nominal_v']==ctx['max_v']:return None
    if replacement_evidence is None:
        folder=registry.root()/s['id'];index_path=folder/'functional_index.json'
        if not index_path.is_file():return None
        reports=((entry,read_report(folder,entry)) for entry in reversed(json.loads(index_path.read_text()))
                 if entry.get('scope')!='replacement')
    else:
        reports=(replacement_evidence,)
    fixed={p['symbol_id'] for p in parts.reference_catalog() if p['kind'] not in ('resistor','capacitor','connector')}
    fixed_present=any(c['library_id'] in fixed for c in s['components'].values())
    expected=spec_hash(s);backend=None
    for entry,report in reports:
        key=entry['run_id']
        proposed = report.get('replacement') is not None or report.get('replacements') is not None
        if proposed != (replacement_evidence is not None):continue
        if report['spec_sha256']!=expected or report['revision']!=s['revision'] or report['verdict']!='pass':continue
        if backend is None:backend=parts.rules_hash()
        if report.get('backend_sha256')!=backend:continue
        from .verification_models import packages
        available=packages()
        if any(model.get('model_id') not in available for run in report['runs'] for model in run.get('models',[]) if model.get('model_id')):continue
        if fixed_present and not all(r['engine']=='digital_truth' and r.get('electrical_reference',{}).get('static_supply_reference_complete') for r in report['runs']):continue
        runs=[r for r in report['runs'] if (r['engine'] in ('spice_op','spice_transient') and r.get('electrical_reference',{}).get('stress_reference_complete')) or (r['engine']=='digital_truth' and r.get('electrical_reference',{}).get('static_supply_reference_complete'))]
        # Replacement evidence has already had all of its runtimes checked.
        if replacement_evidence is None and any(not runtime_matches(r) for r in runs):continue
        if not runs:continue
        findings=[];rows=[];complete=True
        for source in s.get('power_sources',{}).values():
            matching=[]
            for run in runs:
                ref=run['electrical_reference']
                if ref['temperature_c']!=ctx['ambient_c']:continue
                matches=[r for r in ref['sources'] if _source_matches(run,r,source)]
                if len(matches)==1:matching.append(matches[0])
            if len(matching)!=len(runs):complete=False;break
            maximum=max(r['max_current_a'] for r in matching);minimum=min(r['min_current_a'] for r in matching)
            rows.append(dict(net_id=source['net_id'],nominal_v=source['nominal_v'],maximum_reference_a=maximum,minimum_reference_a=minimum))
            if source['boundary_pin']==ctx['positive_pin'] and source['ground_net_id']==ctx['ground_net_id']:
                if maximum>ctx['available_current_ma']/1000:findings.append(dict(code='BUDGET_SOURCE_CAPACITY',severity='error',message='Source-bound SPICE reference exceeds declared source capacity.'))
            else:complete=False
            if minimum<-1e-12:
                capacity=ctx.get('available_sink_current_ma')
                if capacity is None or not ctx.get('sink_capacity_basis'):
                    findings.append(dict(code='MODEL_SOURCE_SINK_CAPACITY_UNKNOWN',severity='error',message='Model requires source sink capability which has not been declared.'))
                elif -minimum>capacity/1000:
                    findings.append(dict(code='BUDGET_SOURCE_SINK_CAPACITY',severity='error',message='Source-bound SPICE reference exceeds explicitly declared source sink capacity.'))
                rows[-1].update(declared_sink_capacity_a=None if capacity is None else capacity/1000,sink_capacity_basis=ctx.get('sink_capacity_basis'))
        if not complete or not rows:continue
        return dict(run_id=key,report_sha256=entry['report_sha256'],rows=rows,findings=findings,scenarios=[r['scenario'] for r in runs],component_stress=[dict(scenario=r['scenario'],components=r['electrical_reference']['component_stress']) for r in runs],backend_sha256=backend)
    return None


def budget(s,bom,ref):
    findings=ref['findings']+[dict(f,code='BUDGET_BOM_CONFLICT') for f in bom['findings'] if f['severity']=='error']
    errors=any(f['severity']=='error' for f in findings)
    findings.append(dict(code='SOURCE_MODEL_REFERENCE_ONLY',severity='warning',message='Current and stress checked only at frozen model operating points and complete sampled startup traces, when present; no process, parasitic, continuous range, thermal or physical current bound.'))
    return dict(project_id=s['id'],revision=s['revision'],verdict='fail' if errors else 'incomplete',budget_verdict='fail' if errors else 'conditional',coverage=dict(spec_sha256=spec_hash(s),functional_run=ref['run_id'],report_sha256=ref['report_sha256'],scenarios=ref['scenarios'],all_actual_loads_verified=False),findings=findings,rails=ref['rows'],loads=[],stages=[],passive_loads=ref['component_stress'],declared_dc_reference_input_a=sum(max(0,r['maximum_reference_a']) for r in ref['rows']),actual_input_current_upper_bound_a=None,physical_qualified=False,manufacturing_approved=False,evidence_hash=ref['backend_sha256'],qualification='source_bound_nominal_model_budget_reference_not_physical_or_worst_case_proof')
