"""Topology-neutral requirements, module ports and deterministic composition.

Port declarations are checked for consistency, not treated as device models.
The calling AI supplies modules and alternatives; this layer never guesses them.
"""
from decimal import Decimal
import hashlib
import json
import math
import re
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .models import Strict
from pcb_mcp_client.plan import Component, Endpoint, NC, Setting, Plan, select_part

Key = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_]{0,63}$')]
UNITS = {
    'V': ('voltage', 1.), 'mV': ('voltage', .001), 'uV': ('voltage', 1e-6),
    'A': ('current', 1.), 'mA': ('current', .001), 'uA': ('current', 1e-6), 'nA': ('current', 1e-9),
    's': ('time', 1.), 'ms': ('time', .001), 'us': ('time', 1e-6), 'ns': ('time', 1e-9),
    'Hz': ('frequency', 1.), 'kHz': ('frequency', 1e3), 'MHz': ('frequency', 1e6),
    'Ohm': ('resistance', 1.), 'kOhm': ('resistance', 1e3), 'MOhm': ('resistance', 1e6),
    'F': ('capacitance', 1.), 'uF': ('capacitance', 1e-6), 'nF': ('capacitance', 1e-9), 'pF': ('capacitance', 1e-12),
    'H': ('inductance', 1.), 'mH': ('inductance', .001), 'uH': ('inductance', 1e-6),
    'W': ('power', 1.), 'mW': ('power', .001), 'mm': ('length', .001), 'm': ('length', 1.),
    'degC': ('temperature', 1.), '1': ('ratio', 1.), '%': ('ratio', .01), 'dB': ('decibels', 1.),
    'frame': ('frame_count', 1.), 'word': ('word_count', 1.),
}

MEASUREMENT_DIMENSIONS = {
    'voltage': 'voltage', 'noise_voltage_rms': 'voltage', 'current': 'current', 'power': 'power',
    'gain': 'ratio', 'logic': 'ratio', 'duty_cycle': 'ratio', 'ratio': 'ratio', 'thd_plus_n': 'ratio',
    'gain_db': 'decibels', 'frequency': 'frequency', 'ac_crossing_frequency': 'frequency',
    'delay': 'time', 'settling_time': 'time', 'i2c_frames': 'frame_count', 'i2s_words': 'word_count',
}


class Quantity(Strict):
    value: Annotated[float, Field(allow_inf_nan=False)]
    unit: str

    @model_validator(mode='after')
    def known(self):
        if self.unit not in UNITS: raise ValueError('UNIT_NOT_SUPPORTED: ' + self.unit)
        return self

    def normalized(self, dimension):
        kind, scale = UNITS[self.unit]
        if kind != dimension: raise ValueError('QUANTITY_DIMENSION_MISMATCH')
        # Convert decimal units before rounding to the contract's base-unit float.
        value = float(Decimal(str(self.value)) * Decimal(str(scale)))
        if not math.isfinite(value): raise ValueError('NONFINITE_NORMALIZED_QUANTITY')
        return value


class Interval(Strict):
    minimum: Annotated[float, Field(allow_inf_nan=False)]
    maximum: Annotated[float, Field(allow_inf_nan=False)]
    unit: str

    @model_validator(mode='after')
    def bounds(self):
        if self.minimum > self.maximum: raise ValueError('REVERSED_INTERVAL')
        if self.unit not in UNITS: raise ValueError('UNIT_NOT_SUPPORTED: ' + self.unit)
        return self

    def normalized(self, dimension):
        return (Quantity(value=self.minimum, unit=self.unit).normalized(dimension),
                Quantity(value=self.maximum, unit=self.unit).normalized(dimension))


class Requirement(Strict):
    id: Key
    description: Annotated[str, Field(min_length=5, max_length=2000)]
    metric: Key
    target: Annotated[list[Key], Field(min_length=2, max_length=2)]
    allowed: Interval
    conditions: dict[str, Interval] = {}


class Intent(Strict):
    description: Annotated[str, Field(min_length=5, max_length=10000)]
    requirements: Annotated[list[Requirement], Field(min_length=1, max_length=100)]

    @model_validator(mode='after')
    def unique(self):
        if len({r.id for r in self.requirements}) != len(self.requirements): raise ValueError('DUPLICATE_REQUIREMENT')
        return self


class Port(Strict):
    name: Key
    kind: Literal['power', 'signal', 'reference']
    direction: Literal['input', 'output', 'bidirectional', 'passive']
    pins: Annotated[list[Endpoint], Field(min_length=1, max_length=100)]
    reference: Key | None = None
    drive_voltage: Interval | None = None
    accept_voltage: Interval | None = None
    available_current: Quantity | None = None
    required_current: Quantity | None = None
    # Explicit declarations; generic verification later binds these to actual models.
    basis: Annotated[str, Field(min_length=5, max_length=2000)]

    @model_validator(mode='after')
    def quantities(self):
        for v in (self.drive_voltage, self.accept_voltage):
            if v: v.normalized('voltage')
        for v in (self.available_current, self.required_current):
            if v and v.normalized('current') < 0: raise ValueError('NEGATIVE_PORT_CURRENT')
        if self.drive_voltage is not None and self.direction not in ('output', 'bidirectional'):
            raise ValueError('DRIVE_VOLTAGE_REQUIRES_DRIVER')
        if self.available_current is not None and self.direction not in ('output', 'bidirectional'):
            raise ValueError('AVAILABLE_CURRENT_REQUIRES_DRIVER')
        return self


class LocalNet(Strict):
    name: Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9_]{0,39}$')]
    kind: Literal['power', 'ground', 'signal']
    pins: Annotated[list[Endpoint], Field(min_length=1, max_length=200)]


class Module(Strict):
    id: Key
    function: Annotated[str, Field(min_length=3, max_length=1000)]
    components: Annotated[list[Component], Field(min_length=1, max_length=100)]
    nets: Annotated[list[LocalNet], Field(max_length=100)] = []
    no_connects: Annotated[list[NC], Field(max_length=200)] = []
    ports: Annotated[list[Port], Field(min_length=1, max_length=100)]

    @model_validator(mode='after')
    def graph(self):
        refs = {c.ref for c in self.components}
        if len(refs) != len(self.components): raise ValueError('DUPLICATE_LOCAL_REFERENCE')
        if len({p.name for p in self.ports}) != len(self.ports): raise ValueError('DUPLICATE_PORT')
        if len({n.name for n in self.nets}) != len(self.nets): raise ValueError('DUPLICATE_LOCAL_NET')
        assigned, exposed = set(), set()
        for net in self.nets:
            for pin in net.pins:
                key = (pin.ref, pin.number)
                if key in assigned: raise ValueError('LOCAL_PIN_ASSIGNED_TWICE')
                assigned.add(key)
        for port in self.ports:
            if port.reference and not any(p.name == port.reference and p.kind == 'reference' for p in self.ports):
                raise ValueError('UNKNOWN_REFERENCE_PORT')
            for pin in port.pins:
                key = (pin.ref, pin.number)
                if key in exposed: raise ValueError('PIN_EXPOSED_TWICE')
                exposed.add(key)
        nc = [(n.pin.ref, n.pin.number) for n in self.no_connects]
        if len(nc) != len(set(nc)) or set(nc) & (assigned | exposed): raise ValueError('CONFLICTING_LOCAL_NO_CONNECT')
        if any(ref not in refs for ref, _ in assigned | exposed | set(nc)): raise ValueError('UNKNOWN_LOCAL_COMPONENT')
        return self


class Link(Strict):
    ports: Annotated[list[Annotated[list[Key], Field(min_length=2, max_length=2)]], Field(min_length=2, max_length=100)]


class Proposal(Strict):
    version: Literal[1] = 1
    name: Annotated[str, Field(min_length=1, max_length=100)]
    intent: Intent
    modules: Annotated[list[Module], Field(min_length=1, max_length=100)]
    connections: Annotated[list[Link], Field(max_length=200)] = []
    settings: Annotated[list[Setting], Field(max_length=100)]

    @model_validator(mode='after')
    def references(self):
        if len({m.id for m in self.modules}) != len(self.modules): raise ValueError('DUPLICATE_MODULE')
        ports = {(m.id, p.name) for m in self.modules for p in m.ports}
        if any(tuple(p) not in ports for link in self.connections for p in link.ports): raise ValueError('UNKNOWN_LINK_PORT')
        if any(tuple(r.target) not in ports for r in self.intent.requirements): raise ValueError('UNKNOWN_REQUIREMENT_TARGET')
        return self


def compose(proposal):
    """Flatten module data without any function, MPN or topology dispatch."""
    parent, ports, references, counters, local_names, local_kinds = {}, {}, {}, {}, [], {}
    def node(module, pin):
        key = (module, pin.ref, pin.number)
        parent.setdefault(key, key)
        return key
    def find(key):
        if parent[key] != key: parent[key] = find(parent[key])
        return parent[key]
    def union(keys):
        roots = [find(k) for k in keys]
        for key in roots: parent[key] = min(roots)
    components, no_connects = [], []
    for module in proposal.modules:
        for c in module.components:
            prefix = re.match('[A-Z]+', c.ref)[0][:5]
            counters[prefix] = counters.get(prefix, 0) + 1
            ref = prefix + str(counters[prefix])
            references[module.id, c.ref] = ref
            components.append(dict(c.model_dump(), ref=ref))
        for net in module.nets:
            keys = [node(module.id, p) for p in net.pins]
            union(keys)
            local_names.append((keys[0], net.name))
            for key in keys: local_kinds.setdefault(key, set()).add(net.kind)
        for port in module.ports:
            keys = [node(module.id, p) for p in port.pins]
            union(keys)
            ports[module.id, port.name] = (port, keys[0])
        for nc in module.no_connects:
            no_connects.append(dict(pin=dict(ref=references[module.id, nc.pin.ref], number=nc.pin.number), reason=nc.reason))
    for link in proposal.connections:
        union([ports[tuple(p)][1] for p in link.ports])
    groups = {}
    for key in parent: groups.setdefault(find(key), []).append(key)
    names, used, nets, findings, checks = {}, set(), [], [], []
    def add(code, net, detail, severity='error'):
        findings.append(dict(code=code, net=net, severity=severity, detail=detail))
    for root, nodes in sorted(groups.items()):
        group_ports = [(key, port) for key, (port, point) in ports.items() if find(point) == root]
        kinds = {p.kind for _, p in group_ports} | {('reference' if k == 'ground' else k) for n in nodes for k in local_kinds.get(n, set())}
        name = next((value for point, value in local_names if find(point) == root), 'N' + str(len(nets) + 1))
        if name in used: name = name[:32] + '_' + str(len(nets) + 1)
        used.add(name); names[root] = name
        if len(kinds) > 1: add('INTERFACE_KIND_MISMATCH', name, sorted(kinds))
        if len(nodes) < 2: add('UNCONNECTED_MODULE_PORT', name, [list(n) for n in nodes])
        kind = 'ground' if 'reference' in kinds else ('power' if 'power' in kinds else 'signal')
        nets.append(dict(name=name, kind=kind, pins=[dict(ref=references[m, ref], number=number) for m, ref, number in sorted(nodes)]))
        drivers = [(key, p) for key, p in group_ports if p.direction == 'output']
        loads = [(key, p) for key, p in group_ports if p.direction == 'input']
        if len(drivers) > 1: add('MULTIPLE_PORT_DRIVERS', name, [list(k) for k, _ in drivers])
        if any(p.direction == 'bidirectional' for _, p in group_ports):
            add('BIDIRECTIONAL_PROTOCOL_MODEL_REQUIRED', name, 'Requires a protocol/drive-mode validator.', 'incomplete')
        if not drivers and loads: add('PORT_DRIVER_UNDECLARED', name, [list(k) for k, _ in loads], 'incomplete')
        for source_key, source in drivers:
            current = 0.
            complete_current = True
            for load_key, load in loads:
                if source.drive_voltage and load.accept_voltage:
                    lo, hi = source.drive_voltage.normalized('voltage')
                    accepted_lo, accepted_hi = load.accept_voltage.normalized('voltage')
                    passed = lo >= accepted_lo and hi <= accepted_hi
                    checks.append(dict(check='port_voltage', source=list(source_key), load=list(load_key), passed=passed, driven_v=[lo,hi], accepted_v=[accepted_lo,accepted_hi]))
                    if not passed: add('PORT_VOLTAGE_OUTSIDE_RANGE', name, checks[-1])
                else: add('PORT_VOLTAGE_UNDECLARED', name, dict(source=list(source_key), load=list(load_key)), 'incomplete')
                if source.reference and load.reference:
                    a = ports[source_key[0], source.reference][1]
                    b = ports[load_key[0], load.reference][1]
                    if find(a) != find(b): add('REFERENCE_DOMAINS_DISCONNECTED', name, dict(source=list(source_key), load=list(load_key)))
                else: add('REFERENCE_DOMAIN_UNDECLARED', name, dict(source=list(source_key), load=list(load_key)), 'incomplete')
                if load.required_current: current += load.required_current.normalized('current')
                else: complete_current = False
            if source.available_current is not None and complete_current:
                available = source.available_current.normalized('current')
                checks.append(dict(check='port_current_budget', source=list(source_key), required_a=current, available_a=available, passed=current <= available))
                if current > available: add('PORT_CURRENT_BUDGET_EXCEEDED', name, checks[-1])
            elif loads: add('PORT_CURRENT_UNDECLARED', name, list(source_key), 'incomplete')
    def translate(value):
        if isinstance(value, list): return [translate(v) for v in value]
        if not isinstance(value, dict): return value
        special = [k for k in value if k.startswith('$')]
        if special:
            if len(value) != 1: raise ValueError('MIXED_PROPOSAL_REFERENCE')
            op, args = next(iter(value.items()))
            if not isinstance(args, list): raise ValueError('INVALID_PROPOSAL_REFERENCE')
            if op == '$module_pin' and len(args) == 3:
                m, ref, number = args
                if (m, ref, number) not in parent: raise ValueError('UNKNOWN_MODULE_PIN')
                return {'$pin': [references[m, ref], number]}
            if op == '$module_component' and len(args) == 2: return {'$component': references[tuple(args)]}
            if op in ('$port_pin', '$port_net') and len(args) == 2:
                port, point = ports[tuple(args)]
                if op == '$port_net': return {'$net': names[find(point)]}
                return {'$pin': [references[point[:2]], point[2]]}
            raise ValueError('UNKNOWN_PROPOSAL_REFERENCE')
        return {k: translate(v) for k, v in value.items()}
    requirement = dict(natural_language=proposal.intent.description, formal_requirements=[r.model_dump() for r in proposal.intent.requirements])
    requirement_json = json.dumps(requirement, ensure_ascii=False, indent=2) + '\n'
    digest = hashlib.sha256(requirement_json.encode()).hexdigest()
    settings = [dict(tool=s.tool, args=translate(s.args)) for s in proposal.settings]
    errors = any(f['severity']=='error' for f in findings)
    compiled = None
    if not errors:
        compiled = Plan.model_validate(dict(name=proposal.name, requirement_sha256=digest, components=components, nets=nets,
                                            no_connects=no_connects, settings=settings)).model_dump(mode='json')
    return dict(verdict='fail' if errors else ('incomplete' if findings else 'pass'), findings=findings, checks=checks,
                scope='declared_module_interface_consistency_not_function_validation',
                plan=compiled, requirement_json=requirement_json, requirement_sha256=digest,
                reference_map={m+':'+ref: value for (m,ref),value in references.items()},
                port_nets={m+':'+p:names[find(point)] for (m,p),(_,point) in ports.items()},
                functional_requirements_verified=False, physical_verified=False)


class DesignContractService:
    def design_compose(self, request):
        from . import parts, backend, footprints
        from .models import Requirements,PowerSource,PowerEnvelope,PassiveRatings,PowerBudget,BoardConstraints,CopperRules,ConstraintSet
        from pcb_mcp_client.plan import check_pin_coverage,resolve
        with self.locked():
            result = compose(request)
            if result['plan'] is not None:
                plan = Plan.model_validate(result['plan'])
                records = parts.catalog()
                symbols = {s['library_id']:s for s in backend.catalog()}
                resolved = {}
                for index,c in enumerate(plan.components,1):
                    part = select_part(records,c.mpn,c.part_definition_hash)
                    if part['evidence_status']!='available': raise ValueError('PART_EVIDENCE_UNAVAILABLE')
                    symbol=symbols[part['symbol_id']]
                    component=dict(symbol,id=f'{index:032x}',component_id=f'{index:032x}',value=self.value(symbol['symbol'],c.value or symbol['symbol'],'schematic_experiment'))
                    fp=footprints.load(*footprints.entries(part['footprint_id'],part['footprint_id'].split(':',1)[0])[0])
                    if not parts.binding_valid({'footprint_bindings':{component['id']:fp}},component,part):
                        raise ValueError('PART_BINDING_MISMATCH: '+c.ref)
                    resolved[c.ref] = component
                check_pin_coverage(plan,resolved)
                net_ids={n.name:f'{i:032x}' for i,n in enumerate(plan.nets,1)}
                schemas=dict(requirements_set=Requirements,power_source_set=PowerSource,power_envelope_set=PowerEnvelope,
                             passive_ratings_set=PassiveRatings,power_budget_set=PowerBudget,board_constraints_set=BoardConstraints,
                             pcb_net_rules_set=CopperRules,constraint_set=ConstraintSet)
                for setting in plan.settings:
                    schemas[setting.tool].model_validate(dict(project_id='0'*32,expected_revision=0,idempotency_key='compose-preflight',**resolve(setting.args,resolved,net_ids)))
                result['real_parts_and_pin_coverage_verified'] = True
            return result
