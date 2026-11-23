"""Source-bound nominal feedback equation. This is NOT a converter power model.

Like the existing nominal rail checks, this reports a setting, not a regulated
operating guarantee. Input supply, enable, switch/inductor, current, compensation,
PFM/PWM transitions, startup and thermal checks remain separately required.
"""
from . import parts,verification_models
from .electrical import number

def evaluate(state):
    candidates={p['candidate_id']:p for p in parts.catalog()};models={};findings=[];rows=[]
    for key,(model,_) in verification_models.packages().items():
        if model['engine']=='feedback_voltage':models.setdefault(model['part_definition_hash'],[]).append((key,model))
    assignment={(p['component_id'],p['pin_id']):n['id'] for n in state['nets'].values() for p in n['pins']}
    def bound(c):
        p=state['part_bindings'].get(c['id'])
        return p if p and candidates.get(p['candidate_id'])==p and p['evidence_status']=='available' and parts.binding_valid(state,c,p) else None
    def issue(c,code,message):findings.append(dict(code=code,severity='error',message=message,entity_ids=[c['id']]))
    for c in state['components'].values():
        p=bound(c)
        if p is None or p['definition_hash'] not in models:continue
        matching=models[p['definition_hash']]
        if len(matching)!=1:issue(c,'FEEDBACK_MODEL_AMBIGUOUS','Exactly one nominal feedback model is required');continue
        key,model=matching[0];f=model['feedback_voltage']
        def net(number):
            pin=next((x for x in c['pins'] if number in x['physical_numbers']),None)
            return assignment.get((c['id'],pin['pin_id'])) if pin else None
        vi,vo,fb,gnd=[net(f[role+'_pin']) for role in ['input','output','feedback','ground']]
        if None in [vi,vo,fb,gnd] or len({vi,vo,fb,gnd})!=4 or state['nets'][gnd]['kind']!='ground':issue(c,'FEEDBACK_MODEL_CONNECTION','Input/output/feedback/ground must be distinct connected nets');continue
        def resistors(a,b):return [r for r in state['components'].values() if r['library_id']=='Device:R' and {assignment.get((r['id'],pin['pin_id'])) for pin in r['pins']}=={a,b}]
        top,bottom=resistors(vo,fb),resistors(fb,gnd)
        if len(top)!=1 or len(bottom)!=1:issue(c,'FEEDBACK_DIVIDER_REQUIRED','Exactly one source-bound upper and lower resistor required');continue
        t,b=bound(top[0]),bound(bottom[0])
        if t is None or b is None:issue(c,'FEEDBACK_DIVIDER_SOURCE','Divider identities and values must be current');continue
        allowed={c['id'],top[0]['id'],bottom[0]['id']}
        feedforward=[]
        for cap in state['components'].values():
            if cap['library_id']!='Device:C':continue
            endpoints={assignment.get((cap['id'],pin['pin_id'])) for pin in cap['pins']}
            if endpoints!={vo,fb}:continue
            cp=bound(cap)
            if cp is None or cp['kind']!='capacitor':continue
            facts=cp['facts']
            if facts.get('voltage_rating_v',0)<f['output_max_v'] or facts.get('capacitance_f',0)<=0:continue
            allowed.add(cap['id']);feedforward.append(dict(reference=cap['ref'],capacitance_f=facts['capacitance_f']))
        if any(x['component_id'] not in allowed for x in state['nets'][fb]['pins']):issue(c,'FEEDBACK_LOAD_UNMODELED','Additional feedback load is not covered by this equation');continue
        voltage=f['reference_nominal_v']*(1+t['facts']['value_ohm']/b['facts']['value_ohm'])
        if not f['output_min_v']<=voltage<=f['output_max_v']:issue(c,'FEEDBACK_SETTING_RANGE','Calculated nominal setting is outside the source model range');continue
        rows.append(dict(component_id=c['id'],model_id=key,input_net_id=vi,output_net_id=vo,ground_net_id=gnd,nominal_v=voltage,divider_refs=[top[0]['ref'],bottom[0]['ref']],feedforward_capacitors=feedforward,feedforward_dc_model='Ideal open circuit at DC; no phase margin, leakage or transient guarantee',conditions=model['conditions'],qualification='nominal_setting_only_not_power_delivery_or_voltage_envelope'))
    return dict(findings=findings,rows=rows)
