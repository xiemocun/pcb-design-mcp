"""Add source-bound parts and connections to a delivered circuit."""
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from .plan import Component, Endpoint, NC, Net, NetName, Ref, Strict, check_pin_coverage, resolve
from .revision import Hash, Placement, RevisionPlan, board_with_placements


MODEL_BINDINGS = TypeAdapter(dict[Ref, Hash])


class NetAttachment(Strict):
    name: NetName
    pins: Annotated[list[Endpoint], Field(min_length=1, max_length=200)]


class CircuitExtension(RevisionPlan):
    mode: Literal['circuit_extension']
    components: Annotated[list[Component], Field(min_length=1, max_length=100)]
    attachments: Annotated[list[NetAttachment], Field(min_length=1, max_length=100)]
    nets: Annotated[list[Net], Field(max_length=100)] = []
    no_connects: Annotated[list[NC], Field(max_length=200)] = []
    placements: Annotated[list[Placement], Field(min_length=1, max_length=100)]

    @model_validator(mode='after')
    def graph(self):
        refs = {component.ref for component in self.components}
        placement_refs = {row.ref for row in self.placements}
        if (len(refs) != len(self.components) or refs != placement_refs
                or len(placement_refs) != len(self.placements)):
            raise ValueError('EXTENSION_PLACEMENTS_MUST_COVER_NEW_COMPONENTS')
        names = [net.name for net in [*self.attachments, *self.nets]]
        if len(names) != len(set(names)):
            raise ValueError('DUPLICATE_EXTENSION_NET')
        pins = [pin for net in [*self.attachments, *self.nets] for pin in net.pins]
        pins.extend(row.pin for row in self.no_connects)
        if any(pin.ref not in refs for pin in pins):
            raise ValueError('EXTENSION_CAN_CONNECT_ONLY_NEW_COMPONENT_PINS')
        if len({(pin.ref, pin.number) for pin in pins}) != len(pins):
            raise ValueError('PIN_ASSIGNED_TWICE')
        return self


def added_model_bindings(plan, requested):
    bindings = MODEL_BINDINGS.validate_python(requested)
    if bindings.keys() - {component.ref for component in plan.components}:
        raise ValueError('EXTENSION_MODELS_MUST_BIND_NEW_COMPONENTS')
    return bindings


def preflight(plan, original, symbols, base_positions):
    old_refs = {component['ref'] for component in original['components'].values()}
    if old_refs & symbols.keys() or old_refs != base_positions.keys():
        raise ValueError('EXTENSION_COMPONENT_REFERENCE_CONFLICT')
    if len(old_refs) + len(symbols) > 128 or len(original['nets']) + len(plan.nets) > 100:
        raise ValueError('EXTENSION_PROJECT_LIMIT')
    nets = {net['name']: net for net in original['nets'].values()}
    if any(net.name in nets for net in plan.nets):
        raise ValueError('EXTENSION_NEW_NET_ALREADY_EXISTS')
    for row in plan.attachments:
        if row.name not in nets:
            raise ValueError('EXTENSION_EXISTING_NET_NOT_FOUND: ' + row.name)
        if len(nets[row.name]['pins']) + len(row.pins) > 200:
            raise ValueError('EXTENSION_NET_PIN_LIMIT: ' + row.name)
    check_pin_coverage(plan, symbols, plan.attachments)
    for row in [*plan.attachments, *plan.nets]:
        for pin in row.pins:
            bound = resolve({'$pin': [pin.ref, pin.number]}, symbols, {})
            physical = next(item for item in symbols[pin.ref]['pins'] if item['pin_id'] == bound['pin_id'])
            if physical['electrical_type'] == 'no_connect':
                raise ValueError('PIN_FUNCTION_NO_CONNECT: ' + pin.ref + '.' + pin.number)
    positions = {**base_positions, **{row.ref: row.model_dump(exclude={'ref'}) for row in plan.placements}}
    board_with_placements(with_components(original, symbols), positions)
    return positions


def with_components(original, new):
    return dict(original, components={**original['components'],
        **{row['component_id']: dict(id=row['component_id'], ref=ref, pins=row['pins'])
           for ref, row in new.items()}})


async def connect(mutate, plan, original, components):
    nets = original['nets'].copy()
    by_name = {net['name']: net for net in nets.values()}
    for row in plan.attachments:
        original_net = by_name[row.name]
        added = [resolve({'$pin': [pin.ref, pin.number]}, components, {}) for pin in row.pins]
        pins = [*original_net['pins'], *added]
        await mutate('net_members_set', net_id=original_net['id'], pins=pins)
        nets[original_net['id']] = dict(original_net, pins=pins)
    for row in plan.nets:
        ident = (await mutate('net_create', name=row.name, kind=row.kind))['net_id']
        pins = [resolve({'$pin': [pin.ref, pin.number]}, components, {}) for pin in row.pins]
        await mutate('net_members_set', net_id=ident, pins=pins)
        nets[ident] = dict(id=ident, name=row.name, kind=row.kind, pins=pins)
    intents = original['pin_intents'].copy()
    for row in plan.no_connects:
        pin = resolve({'$pin': [row.pin.ref, row.pin.number]}, components, {})
        await mutate('pin_intent_set', pin=pin, intent='no_connect', reason=row.reason)
        intents[pin['component_id'] + ':' + pin['pin_id']] = dict(pin=pin, intent='no_connect', reason=row.reason)
    return dict(original, nets=nets, pin_intents=intents)


def verify_components(expected, actual, catalog, components):
    # Full equality is checked on all inherited specifications. The new
    # backend records also carry normalized display values and library data.
    extended = dict(expected)
    for key in ('components', 'part_bindings', 'footprint_bindings'):
        extended[key] = expected[key].copy()
    for ref, added in components.items():
        ident = added['component_id']
        current = actual['components'][ident]
        part = actual['part_bindings'][ident]
        footprint = actual['footprint_bindings'][ident]
        selected = catalog[ref]
        if (ident in expected['components'] or current['ref'] != ref
                or current['candidate_id'] != selected['symbol']['candidate_id']
                or current['pins'] != added['pins']
                or part['definition_hash'] != selected['part']['definition_hash']
                or footprint['definition_hash'] != selected['footprint']['definition_hash']):
            raise ValueError('EXTENSION_SOURCE_BINDING_CHANGED: ' + ref)
        extended['components'][ident] = current
        extended['part_bindings'][ident] = part
        extended['footprint_bindings'][ident] = footprint
    return extended
