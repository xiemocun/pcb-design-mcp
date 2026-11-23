"""Source-bounded static combinational logic, with actual voltage margins."""
import itertools
import math


def validate_model(model):
    truth=model['truth']
    required={'inputs','outputs','supply','rows','vil_max_v','vih_min_v','vol_max_v','voh_min_v','input_leakage_max_a','output_current_max_a'}
    if set(truth)!=required:raise ValueError('DIGITAL_MODEL_FIELDS')
    ins=truth['inputs'];outs=truth['outputs'];supply=truth['supply']
    if not 1<=len(ins)<=12 or not 1<=len(outs)<=8 or len(supply)!=2:raise ValueError('DIGITAL_MODEL_PORT_COUNT')
    pins=ins+outs+supply
    if len(set(pins))!=len(pins) or set(pins)!=set(model['pins']):raise ValueError('DIGITAL_MODEL_PHYSICAL_PIN_COVERAGE')
    rows={''.join(row) for row in itertools.product('01',repeat=len(ins))}
    if set(truth['rows'])!=rows or any(len(v)!=len(outs) or set(v)-set('01') for v in truth['rows'].values()):raise ValueError('DIGITAL_TRUTH_TABLE_INCOMPLETE')
    for key in required-{'inputs','outputs','supply','rows'}:
        if not isinstance(truth[key],(int,float)) or not math.isfinite(truth[key]) or truth[key]<0:raise ValueError('DIGITAL_THRESHOLD_INVALID')
    if truth['vil_max_v']>=truth['vih_min_v'] or truth['vol_max_v']>=truth['voh_min_v']:raise ValueError('DIGITAL_THRESHOLD_ORDER')
    if not model.get('supply_range_v'):raise ValueError('DIGITAL_SUPPLY_RANGE_REQUIRED')


def evaluate(s,scenario,models,records,mapping,requirements):
    from .functional import Terminal,resolve_terminal
    def net(ref,num):return resolve_terminal(mapping,Terminal(ref=ref,number=num))
    driven={};owners={};pending={};checks=[]
    def drive(nid,low,high,owner):
        if nid in driven:
            if owners[nid]==owner:return
            raise ValueError('DIGITAL_MULTIPLE_DRIVERS: '+nid)
        driven[nid]=(low,high);owners[nid]=owner
    ground=None
    for i,d in enumerate(scenario.drives):
        p=resolve_terminal(mapping,d.positive);n=resolve_terminal(mapping,d.negative)
        if ground is None:ground=n;drive(n,0.,0.,'reference')
        if n!=ground:raise ValueError('DIGITAL_REFERENCE_DOMAINS_NOT_COMMON')
        if not s['components'][next(cid for cid,c in s['components'].items() if c['ref']==d.positive.ref)]['library_id'].startswith('Connector_Generic:'):raise ValueError('DIGITAL_DRIVE_REQUIRES_EXTERNAL_BOUNDARY')
        drive(p,d.dc_v,d.dc_v,'external_'+str(i))
    if ground is None:raise ValueError('DIGITAL_DRIVES_REQUIRED')
    for c in s['components'].values():
        part=records[c['ref']]
        if part['kind']=='connector' and c['library_id'].startswith('Connector_Generic:'):continue
        if part['kind']=='capacitor' and c['library_id'] in ('Device:C','Device:C_Polarized'):
            checks.append(dict(reference=c['ref'],scope='ideal_DC_open_capacitor_no_dynamic_claim'));continue
        if c['ref'] not in models or models[c['ref']][0]['engine']!='digital_truth':raise ValueError('DIGITAL_COMPONENT_MODEL_REQUIRED: '+c['ref'])
        model=models[c['ref']][0];validate_model(model)
        if not model['temperature_range_c'][0]<=scenario.temperature_c<=model['temperature_range_c'][1]:raise ValueError('DIGITAL_MODEL_TEMPERATURE_SCOPE')
        pending[c['ref']]=model
    # Inputs contribute source-specific load to the actual output net.
    loads={}
    for ref,model in pending.items():
        truth=model['truth']
        for pin in truth['inputs']:loads[net(ref,pin)]=loads.get(net(ref,pin),0.)+truth['input_leakage_max_a']
    while pending:
        progress=False
        for ref,model in list(pending.items()):
            t=model['truth'];supply=net(ref,t['supply'][0]);reference=net(ref,t['supply'][1])
            inputs=[net(ref,pin) for pin in t['inputs']]
            if any(nid not in driven for nid in [supply,reference]+inputs):continue
            if reference!=ground:raise ValueError('DIGITAL_REFERENCE_MISMATCH')
            vlo,vhi=driven[supply]
            if vlo<model['supply_range_v'][0] or vhi>model['supply_range_v'][1]:raise ValueError('DIGITAL_SUPPLY_OUTSIDE_MODEL')
            bits='';margins=[]
            for nid in inputs:
                lo,hi=driven[nid]
                if lo<0 or hi>vhi:raise ValueError('DIGITAL_INPUT_OUTSIDE_RAILS')
                if hi<=t['vil_max_v']:bits+='0';margins.append(t['vil_max_v']-hi)
                elif lo>=t['vih_min_v']:bits+='1';margins.append(lo-t['vih_min_v'])
                else:raise ValueError('DIGITAL_INPUT_THRESHOLD_AMBIGUOUS')
            result=t['rows'][bits]
            for pin,bit in zip(t['outputs'],result):
                nid=net(ref,pin)
                if loads.get(nid,0.)>t['output_current_max_a']:raise ValueError('DIGITAL_OUTPUT_LOAD_EXCEEDS_MODEL')
                low,high=(0.,t['vol_max_v']) if bit=='0' else (t['voh_min_v'],vhi)
                drive(nid,low,high,ref+':'+pin)
            checks.append(dict(reference=ref,input_bits=bits,output_bits=result,noise_margins_v=margins,model_id=models[ref][1].name))
            del pending[ref];progress=True
        if not progress:raise ValueError('DIGITAL_UNDRIVEN_INPUT_OR_FEEDBACK_REQUIRES_STATE_MODEL')
    measurements=[]
    for r in requirements:
        p=resolve_terminal(mapping,r.positive);n=resolve_terminal(mapping,r.negative)
        if p not in driven or n!=ground:raise ValueError('DIGITAL_MEASUREMENT_UNDRIVEN')
        lo,hi=driven[p]
        if r.quantity=='voltage':
            measurements.append(dict(requirement=r.id,scenario=scenario.id,minimum=lo,maximum=hi,voltage_interval_v=[lo,hi],passed=r.minimum<=lo and hi<=r.maximum,scope='source_bounded_static_voltage_interval_no_timing_claim'))
            continue
        # Binary observation uses the producing model's guaranteed output state.
        producer=owners[p].split(':')[0]
        if producer not in models:raise ValueError('DIGITAL_OUTPUT_MODEL_REQUIRED')
        t=models[producer][0]['truth']
        if hi<=t['vol_max_v']:bit=0.
        elif lo>=t['voh_min_v']:bit=1.
        else:raise ValueError('DIGITAL_OUTPUT_AMBIGUOUS')
        measurements.append(dict(requirement=r.id,scenario=scenario.id,minimum=bit,maximum=bit,voltage_interval_v=[lo,hi],passed=r.minimum<=bit<=r.maximum,scope='source_bounded_static_truth_and_noise_margin_no_timing_claim'))
    return dict(measurements=measurements,checks=checks,net_voltage_intervals=driven)
