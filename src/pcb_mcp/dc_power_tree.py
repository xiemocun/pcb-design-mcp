"""Source-linked, circuit-bound declared DC scenarios.

The graph checks current accounting and ratings. Efficiencies, rail envelopes,
external operating modes and loads remain named engineering assumptions; this
does not simulate control loops, charge termination, acoustics or temperature.
"""
import hashlib,json,math
from typing import Annotated,Literal
from pydantic import Field,model_validator
from .models import Strict

Positive=Annotated[float,Field(gt=0,allow_inf_nan=False)]
Nonnegative=Annotated[float,Field(ge=0,allow_inf_nan=False)]

class DCRail(Strict):
    name:str
    minimum_v:Nonnegative
    nominal_v:Nonnegative
    maximum_v:Positive
    basis:Annotated[str,Field(min_length=10)]
    @model_validator(mode='after')
    def ordered(self):
        if not self.minimum_v<=self.nominal_v<=self.maximum_v:raise ValueError('DC_RAIL_ORDER')
        return self

class DCLoad(Strict):
    reference:str
    pin:str
    current_a:Nonnegative
    reason:Annotated[str,Field(min_length=10)]

class DCConversion(Strict):
    reference:str
    kind:Literal['buck','boost','linear','power_path']
    input_net:str
    output_net:str
    input_pins:list[str]
    output_pins:list[str]
    input_range_v:Annotated[list[Positive],Field(min_length=2,max_length=2)]
    max_output_a:Positive|None=None
    max_input_a:Positive|None=None
    efficiency:Annotated[float,Field(gt=0,le=1,allow_inf_nan=False)]|None=None
    overhead_a:Nonnegative=0.
    path_drop_budget_v:Nonnegative|None=None
    reason:Annotated[str,Field(min_length=10)]
    @model_validator(mode='after')
    def converter(self):
        if (self.kind in ['buck','boost'])!=(self.efficiency is not None):raise ValueError('DC_EFFICIENCY_ROLE')
        if (self.kind=='power_path')!=(self.path_drop_budget_v is not None):raise ValueError('DC_PATH_DROP_ROLE')
        if self.max_input_a is None and self.max_output_a is None:raise ValueError('DC_STAGE_CAPACITY_REQUIRED')
        if self.input_range_v[0]>self.input_range_v[1]:raise ValueError('DC_INPUT_RANGE_ORDER')
        return self

class DCSource(Strict):
    net:str
    reference:str
    pin:str
    capacity_a:Positive
    reason:Annotated[str,Field(min_length=10)]

class DCScenario(Strict):
    name:str
    condition:Annotated[str,Field(min_length=10)]
    sources:list[DCSource]
    stages:list[DCConversion]
    load_scale:Literal[0,1]=1
    extra_loads_a:dict[str,Nonnegative]={}

class DCProgram(Strict):
    name:str
    resistor_reference:str
    factor_min:Positive
    factor_max:Positive
    limit_a:Positive
    reason:Annotated[str,Field(min_length=10)]

class DCPowerTree(Strict):
    circuit_sha256:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    rails:Annotated[list[DCRail],Field(min_length=1,max_length=100)]
    loads:Annotated[list[DCLoad],Field(min_length=1,max_length=100)]
    stage_references:Annotated[list[str],Field(min_length=1,max_length=30)]
    storage_references:list[str]=[]
    scenarios:Annotated[list[DCScenario],Field(min_length=1,max_length=10)]
    reserves_a:dict[str,Nonnegative]
    programs:list[DCProgram]=[]
    resistor_temperature_span_c:Annotated[float,Field(ge=0,le=250,allow_inf_nan=False)]
    conditions:Annotated[str,Field(min_length=30,max_length=5000)]


def circuit_hash(s):
    assignment={(p['component_id'],p['pin_id']):n['name'] for n in s['nets'].values() for p in n['pins']}
    rows=[]
    for c in sorted(s['components'].values(),key=lambda c:c['ref']):
        p=s.get('part_bindings',{}).get(c['id'],{});f=s.get('footprint_bindings',{}).get(c['id'],{})
        rows.append(dict(ref=c['ref'],symbol=c['library_id'],value=c['value'],part=p.get('definition_hash'),footprint=f.get('definition_hash'),pins=sorted((n,assignment.get((c['id'],pin['pin_id']))) for pin in c['pins'] for n in pin['physical_numbers'])))
    value=dict(components=rows,nets=sorted((n['name'],n['kind']) for n in s['nets'].values()))
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def evaluate(s,model,model_id):
    from . import parts,power_budget
    tree=DCPowerTree.model_validate(model).model_dump();findings=[];byref={c['ref']:c for c in s['components'].values()}
    def issue(code,message):findings.append(dict(code=code,severity='error',message=message))
    if tree['circuit_sha256']!=circuit_hash(s):issue('DC_TREE_CIRCUIT_CHANGED','The bound parts, values, pins or nets differ from this source-linked scenario model.')
    catalog={p['candidate_id']:p for p in parts.catalog()};bound={}
    for ref,c in byref.items():
        p=s.get('part_bindings',{}).get(c['id'])
        if not p or catalog.get(p['candidate_id'])!=p or p['evidence_status']!='available' or not parts.binding_valid(s,c,p):issue('DC_TREE_PART_SOURCE',ref+' lacks a current exact source binding.')
        else:bound[ref]=p
    nets={n['name']:n for n in s['nets'].values()};assignment={(p['component_id'],p['pin_id']):n['name'] for n in s['nets'].values() for p in n['pins']}
    def pin_net(ref,number):
        c=byref.get(ref,{})
        p=next((p for p in c.get('pins',[]) if number in p['physical_numbers']),None)
        return assignment.get((c.get('id'),p['pin_id'])) if p else None
    rails={r['name']:r for r in tree['rails']};power={n['name'] for n in s['nets'].values() if n['kind']=='power'}
    if len(rails)!=len(tree['rails']) or set(rails)!=power or set(tree['reserves_a'])!=power:issue('DC_TREE_RAIL_COVERAGE','Each power rail requires exactly one voltage interval and reserve.')
    expected,_=power_budget.expected(s);expected={(c['ref'],tuple(p['physical_numbers'])) for c,p in expected.values()}
    actual=[];loads=[]
    for row in tree['loads']:
        ref,pin=row['reference'],row['pin'];c=byref.get(ref,{})
        physical=next((tuple(p['physical_numbers']) for p in c.get('pins',[]) if pin in p['physical_numbers']),())
        actual.append((ref,physical));net=pin_net(ref,pin);part=bound.get(ref,{});f=part.get('facts',{});r=rails.get(net)
        if not r or f.get('supply_min_v') is None or f.get('supply_max_v') is None:issue('DC_TREE_LOAD_SOURCE',ref+' supply identity/range is missing.')
        elif r['minimum_v']<f['supply_min_v'] or r['maximum_v']>f['supply_max_v']:issue('DC_TREE_LOAD_VOLTAGE',ref+' supply interval exceeds the bound part operating range.')
        loads.append(dict(row,net=net))
    if set(actual)!=expected or len(actual)!=len(set(actual)):issue('DC_TREE_LOAD_COVERAGE','Every source-bound load supply pin must be accounted for exactly once.')
    covered={r['reference'] for r in loads}|set(tree['stage_references'])|set(tree['storage_references'])
    for ref in tree['storage_references']:
        if bound.get(ref,{}).get('kind')!='inductor':issue('DC_TREE_STORAGE_ROLE',ref+' is not a source-bound inductor.')
    for ref in tree['stage_references']:
        if bound.get(ref,{}).get('kind') not in ['regulator','boost_converter','power_path_charger','buck_converter','charger']:issue('DC_TREE_CONVERTER_ROLE',ref+' is not a source-bound power converter/path.')
    for ref,p in bound.items():
        if p['kind'] not in ['resistor','capacitor','connector'] and ref not in covered:issue('DC_TREE_ACTIVE_UNMODELED',ref+' is not represented in this DC graph.')
    passive=[];rvalues={};vmax=max((r['maximum_v'] for r in rails.values()),default=0.)
    for ref,c in byref.items():
        p=bound.get(ref,{});f=p.get('facts',{})
        ns={assignment.get((c['id'],pin['pin_id'])) for pin in c['pins']};feeds=ns&power
        if p.get('kind')=='resistor':
            required=['value_ohm','tolerance_fraction','temperature_coefficient_ppm_per_k']
            if any(k not in f for k in required):issue('DC_TREE_RESISTOR_SOURCE',ref+' resistor value/tolerance/TCR unavailable.');continue
            error=f['tolerance_fraction']+f['temperature_coefficient_ppm_per_k']*1e-6*tree['resistor_temperature_span_c']
            lo,hi=f['value_ohm']*(1-error),f['value_ohm']*(1+error)
            if lo<=0:issue('DC_TREE_RESISTOR_RANGE',ref+' has no positive resistance bound.');continue
            rvalues[ref]=(lo,hi)
            if len(feeds)>1:issue('DC_TREE_CROSS_RAIL_RESISTOR',ref+' crosses two independently modeled rails.')
            for net in feeds:passive.append(dict(reference=ref,net=net,current_a=vmax/lo,resistance_min_ohm=lo,voltage_assumption_v=vmax))
            if feeds and f.get('rated_power_w',f.get('power_w',0))<vmax*vmax/lo:issue('DC_TREE_RESISTOR_POWER',ref+' resistor dissipation exceeds its source rating.')
        if p.get('kind')=='capacitor' and feeds:
            rating=f.get('rated_voltage_v',f.get('voltage_rating_v'))
            if rating is None or rating<max(rails[n]['maximum_v'] for n in feeds if n in rails):issue('DC_TREE_CAP_VOLTAGE',ref+' capacitor voltage rating is absent or insufficient.')
    programs=[]
    for p in tree['programs']:
        r=rvalues.get(p['resistor_reference'])
        if not r:issue('DC_TREE_PROGRAM_RESISTOR',p['name']+' source resistor unavailable.');continue
        lo,hi=p['factor_min']/r[1],p['factor_max']/r[0]
        programs.append(dict(p,minimum_a=lo,maximum_a=hi))
        if p['factor_min']>p['factor_max'] or hi>p['limit_a']:issue('DC_TREE_PROGRAM_LIMIT',p['name']+' programmed upper current exceeds the declared source/device limit.')
    cases=[]
    for case in tree['scenarios']:
        demand={n:tree['reserves_a'].get(n,0.)*case['load_scale'] for n in rails};stage_rows=[]
        for row in loads+passive:
            if row['net'] in demand:demand[row['net']]+=row['current_a']*case['load_scale']
        for net,current in case['extra_loads_a'].items():
            if net not in demand:issue('DC_TREE_EXTRA_LOAD_NET',net+' is not a modeled rail.')
            else:demand[net]+=current
        stages=case['stages'];refs=[x['reference'] for x in stages]
        if len(refs)!=len(set(refs)) or (case['load_scale'] and set(refs)!=set(tree['stage_references'])):issue('DC_TREE_STAGE_COVERAGE',case['name']+' stage references are missing or duplicated.')
        outputs=[x['output_net'] for x in stages]
        if len(outputs)!=len(set(outputs)):issue('DC_TREE_MULTIPLE_DRIVERS',case['name']+' contains two stages driving the same rail; current sharing is not modeled.')
        pending=list(stages)
        for _ in range(len(pending)+1):
            progressed=False
            for st in pending[:]:
                ref=st['reference'];vi,vo=st['input_net'],st['output_net']
                if any(x is not st and x['input_net']==vo for x in pending):continue
                if vi==vo or vi not in rails or vo not in rails:issue('DC_TREE_STAGE_NET',ref+' has invalid power ports.');pending.remove(st);continue
                if not st['input_pins'] or not st['output_pins'] or any(pin_net(ref,p)!=vi for p in st['input_pins']) or any(pin_net(ref,p)!=vo for p in st['output_pins']):issue('DC_TREE_STAGE_PIN',ref+' power port pins do not match the physical circuit.')
                iv,ov=rails[vi],rails[vo]
                if iv['minimum_v']<=0:issue('DC_TREE_STAGE_ZERO_INPUT',ref+' has no positive input voltage bound.');pending.remove(st);continue
                if iv['minimum_v']<st['input_range_v'][0] or iv['maximum_v']>st['input_range_v'][1]:issue('DC_TREE_STAGE_VOLTAGE',ref+' input range exceeds the source-linked scenario rating.')
                if st['kind']=='buck' and iv['minimum_v']<=ov['maximum_v']:issue('DC_TREE_BUCK_HEADROOM',ref+' lacks DC buck headroom.')
                output=demand[vo]
                incoming=output*ov['maximum_v']/(iv['minimum_v']*st['efficiency'])+st['overhead_a'] if st['efficiency'] else output+st['overhead_a']
                if st['max_output_a'] is not None and output>st['max_output_a']:issue('DC_TREE_STAGE_OUTPUT_CAPACITY',ref+' declared output load exceeds its source-linked scenario capacity.')
                if st['max_input_a'] is not None and incoming>st['max_input_a']:issue('DC_TREE_STAGE_INPUT_CAPACITY',ref+' declared input load exceeds the source-linked scenario screen.')
                if st['path_drop_budget_v'] is not None and iv['minimum_v']-st['path_drop_budget_v']<ov['minimum_v']:issue('DC_TREE_PATH_HEADROOM',ref+' path drop budget does not preserve the declared lower output voltage.')
                loss=output*ov['maximum_v']*(1/st['efficiency']-1)+st['overhead_a']*iv['maximum_v'] if st['efficiency'] else max(0.,iv['maximum_v']-ov['minimum_v'])*output+st['overhead_a']*iv['maximum_v']
                if st['path_drop_budget_v'] is not None:loss=output*st['path_drop_budget_v']+st['overhead_a']*iv['maximum_v']
                demand[vi]+=incoming;stage_rows.append(dict(st,output_demand_a=output,input_demand_a=incoming,conversion_loss_reference_w=loss));pending.remove(st);progressed=True
            if not pending:break
            if not progressed:break
        if pending:issue('DC_TREE_CYCLE',case['name']+' has a dependency cycle.')
        source_rows=[];source_nets=set()
        for source in case['sources']:
            net=source['net'];source_nets.add(net)
            if net not in rails or pin_net(source['reference'],source['pin'])!=net or bound.get(source['reference'],{}).get('kind')!='connector':issue('DC_TREE_SOURCE_PIN',case['name']+' must use a real connector source boundary.')
            current=demand.get(net,0.)
            if current>source['capacity_a']:issue('DC_TREE_SOURCE_CAPACITY',case['name']+' '+net+' exceeds available source current.')
            source_rows.append(dict(source,demand_a=current,margin_a=source['capacity_a']-current))
        produced={st['output_net'] for st in stages}
        for net,current in demand.items():
            # Internal bias-only capacitor rails may have zero DC draw.
            if current and net not in source_nets|produced:issue('DC_TREE_UNSUPPLIED_RAIL',case['name']+' does not supply '+net)
        cases.append(dict(name=case['name'],condition=case['condition'],sources=source_rows,stages=stage_rows,rail_demand_a=demand))
    findings.append(dict(code='DC_TREE_DECLARED_ASSUMPTIONS',severity='warning',message=tree['conditions']))
    findings.append(dict(code='DC_TREE_DYNAMIC_THERMAL_UNQUALIFIED',severity='warning',message='DC accounting does not prove startup, dropout under transients, PFM extrema, effective capacitance, battery protection, actual thermal resistance, EMI or full-duplex acoustics.'))
    return dict(model_id=model_id,circuit_sha256=circuit_hash(s),findings=findings,rails=rails,loads=loads,passive_loads=passive,programs=programs,scenarios=cases,physical_qualified=False)


def reference(s):
    from .verification_models import packages
    identities={p['definition_hash'] for p in s.get('part_bindings',{}).values()}
    selected=[(key,m['dc_power_tree']) for key,(m,_) in packages().items() if m['engine']=='dc_power_tree' and m['part_definition_hash'] in identities]
    if not selected:return None
    matches=[x for x in selected if x[1]['circuit_sha256']==circuit_hash(s)]
    if len(matches)!=1:
        return dict(findings=[dict(code='DC_TREE_MODEL_STALE_OR_AMBIGUOUS',severity='error',message='Exactly one source-linked model must match the complete current circuit.')],rails={},loads=[],passive_loads=[],scenarios=[])
    return evaluate(s,matches[0][1],matches[0][0])


def budget_report(s,bom,r):
    from . import parts
    findings=list(r['findings'])+[dict(f) for f in bom['findings'] if f['severity']=='error']
    failed=any(f['severity']=='error' for f in findings)
    return dict(project_id=s['id'],revision=s['revision'],verdict='fail' if failed else 'incomplete',budget_verdict='fail' if failed else 'conditional',coverage=dict(circuit_sha256=circuit_hash(s),scenario_count=len(r['scenarios']),all_actual_loads_verified=False,model_id=r.get('model_id')),findings=findings,rails=list(r['rails'].values()),loads=r['loads'],stages=r['scenarios'],passive_loads=r['passive_loads'],declared_dc_reference_input_a=None,actual_input_current_upper_bound_a=None,physical_qualified=False,manufacturing_approved=False,evidence_hash=parts.rules_hash(),qualification='source_linked_circuit_bound_declared_DC_scenarios_not_physical_qualification')
