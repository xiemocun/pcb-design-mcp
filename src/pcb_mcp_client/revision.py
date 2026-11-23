"""Bind a revision to a delivered project and its inherited checks."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Annotated, Literal
from zipfile import ZipFile

from pydantic import Field, TypeAdapter, model_serializer, model_validator

from pcb_mcp.design_contract import Interval, MEASUREMENT_DIMENSIONS
from pcb_mcp.editing import SPEC_KEYS, moved_net_pins, removed_circuit, spec_hash
from pcb_mcp.explicit_placement import validate as validate_placement
from pcb_mcp.functional import FunctionalContract, FunctionalRequirement, Key, Scenario
from pcb_mcp.models import BoardConstraints, BoardDimension, BoardStackup, ComponentPlacement, MountingHole
from pcb_mcp.registry import canonical, sha
from pcb_mcp import stackup, verification_models
from .acceptance import criteria, delivery_criteria
from .journal import digest
from .plan import Endpoint, NetName, Ref, Strict, resolve, select_part
from .revision_summary import SUMMARY_NAME


Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
PlacementCoordinate = Annotated[float, Field(ge=0, le=150, allow_inf_nan=False)]
PlacementRotation = Literal[0, 90, 180, 270]


class Placement(Strict):
    ref: Ref
    x_mm: PlacementCoordinate
    y_mm: PlacementCoordinate
    rotation_deg: PlacementRotation = 0
    reason: Annotated[str, Field(min_length=5, max_length=500)]


class PlacementRequirement(Strict):
    ref: Ref
    x_mm: PlacementCoordinate | None = None
    y_mm: PlacementCoordinate | None = None
    rotation_deg: PlacementRotation | None = None


PLACEMENT_REQUIREMENTS = TypeAdapter(Annotated[list[PlacementRequirement], Field(min_length=1, max_length=128)])


def placement_requirements(document):
    if 'placement_requirements' not in document:
        return {}
    requirements = PLACEMENT_REQUIREMENTS.validate_python(document['placement_requirements'])
    targets = {row.ref: row.model_dump(exclude={'ref'}, exclude_none=True) for row in requirements}
    if len(targets) != len(requirements):
        raise ValueError('DUPLICATE_PLACEMENT_REQUIREMENT')
    return targets


def check_placement_references(targets, base_positions):
    if targets.keys() - base_positions.keys():
        raise ValueError('UNKNOWN_PLACEMENT_REQUIREMENT_REFERENCE: ' +
                         ', '.join(sorted(targets.keys() - base_positions.keys())))


def verify_placement_requirements(plan, targets, base_positions):
    check_placement_references(targets, base_positions)
    updates = {row.ref: row.model_dump(exclude={'ref', 'reason'}) for row in plan.placements}
    if updates.keys() - targets.keys():
        raise ValueError('REVISION_UNREQUESTED_PLACEMENT: ' + ', '.join(sorted(updates.keys() - targets.keys())))
    for ref, axes in targets.items():
        if axes and updates.get(ref, base_positions[ref]) != {**base_positions[ref], **axes}:
            raise ValueError('REVISION_PLACEMENT_TARGET_MISMATCH: ' + ref)


class RevisionPlan(Strict):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    base_sha256: Hash
    requirement_sha256: Hash
    routing_attempt_limit: Annotated[int, Field(ge=1, le=4)] = 2
    routing_max_passes: Annotated[int, Field(ge=1, le=100)] = 20


class PlacementRevision(RevisionPlan):
    placements: Annotated[list[Placement], Field(max_length=128)] = []

    @model_validator(mode='after')
    def unique_references(self):
        if len({row.ref for row in self.placements}) != len(self.placements):
            raise ValueError('DUPLICATE_REVISION_PLACEMENT')
        return self


class MechanicalRevision(PlacementRevision):
    mode: Literal['mechanical_revision']
    placements: Annotated[list[Placement], Field(min_length=1, max_length=128)]


class OutlineRevision(PlacementRevision):
    mode: Literal['outline_revision']


class Replacement(Strict):
    ref: Ref
    mpn: Annotated[str, Field(min_length=1, max_length=80)]
    part_definition_hash: Hash
    model_id: Hash | None = None

    @model_serializer(mode='wrap')
    def omit_absent_model(self, handler):
        value = handler(self)
        if self.model_id is None:
            value.pop('model_id')
        return value


class ComponentRevision(RevisionPlan):
    mode: Literal['component_revision']
    replacement: Replacement


class ComponentGroupRevision(RevisionPlan):
    mode: Literal['component_revision']
    replacements: Annotated[list[Replacement], Field(min_length=2, max_length=16)]

    @model_validator(mode='after')
    def unique_references(self):
        if len({row.ref for row in self.replacements}) != len(self.replacements):
            raise ValueError('DUPLICATE_REPLACEMENT_COMPONENT')
        return self


class FunctionalRevision(RevisionPlan):
    mode: Literal['functional_revision']


class PinMove(Strict):
    pin: Endpoint
    from_net: NetName
    to_net: NetName


PIN_MOVES = Annotated[list[PinMove], Field(min_length=1, max_length=200)]


class ConnectivityRevision(RevisionPlan):
    mode: Literal['connectivity_revision']
    moves: PIN_MOVES


def connectivity_changes(document):
    if 'connectivity_changes' not in document:
        return None
    return TypeAdapter(PIN_MOVES).validate_python(document['connectivity_changes'])


def check_connectivity_plan(plan, requested):
    if requested is None:
        return
    if not isinstance(plan, ConnectivityRevision):
        raise ValueError('CONNECTIVITY_CHANGES_PLAN_MISMATCH')
    targets = {(row.pin.ref, row.pin.number): (row.from_net, row.to_net) for row in requested}
    proposed = {(row.pin.ref, row.pin.number): (row.from_net, row.to_net) for row in plan.moves}
    if len(targets) != len(requested) or len(proposed) != len(plan.moves):
        raise ValueError('DUPLICATE_CONNECTIVITY_MOVE')
    differences = sorted(pin for pin in targets.keys() | proposed.keys()
                         if targets.get(pin) != proposed.get(pin))
    if differences:
        pins = ', '.join(f'{ref}.{number}' for ref, number in differences)
        raise ValueError('REVISION_CONNECTIVITY_TARGET_MISMATCH: ' + pins)


class CircuitRemoval(RevisionPlan):
    mode: Literal['circuit_removal']


class _AuthorizedPlacementRevision(PlacementRevision):
    @model_serializer(mode='wrap')
    def omit_empty_placements(self, handler):
        value = handler(self)
        if not self.placements:
            value.pop('placements')
        return value


class StackupRevision(_AuthorizedPlacementRevision):
    mode: Literal['stackup_revision']


class MountingRevision(_AuthorizedPlacementRevision):
    mode: Literal['mounting_revision']


class BoardConstruction(Strict):
    layers: Literal[2, 4]
    thickness_mm: Annotated[float, Field(ge=.4, le=3.2, allow_inf_nan=False)]
    stackup: BoardStackup | None

    @model_validator(mode='after')
    def construction(self):
        stackup.validate(self.model_dump(mode='json'))
        return self


class StackupChange(Strict):
    previous: BoardConstruction
    revised: BoardConstruction


def stackup_request(document):
    return StackupChange.model_validate(document['stackup_change']) if 'stackup_change' in document else None


class OutlineDimensions(Strict):
    width_mm: BoardDimension
    height_mm: BoardDimension


class OutlineChange(Strict):
    previous: OutlineDimensions
    revised: OutlineDimensions
    allow_placement_changes: bool = False


def outline_request(document):
    return OutlineChange.model_validate(document['outline_change']) if 'outline_change' in document else None


class MountingChange(Strict):
    previous: Annotated[list[MountingHole], Field(max_length=16)]
    revised: Annotated[list[MountingHole], Field(max_length=16)]
    removed_indices: Annotated[list[Annotated[int, Field(ge=0, lt=16, strict=True)]], Field(max_length=16)] | None = None
    added_indices: Annotated[list[Annotated[int, Field(ge=0, lt=16, strict=True)]], Field(max_length=16)] | None = None

    @model_validator(mode='after')
    def check_indices(self):
        if self.removed_indices is not None or self.added_indices is not None:
            removed, added = self.removed_indices or [], self.added_indices or []
            if (len(removed) != len(set(removed)) or len(added) != len(set(added))
                    or any(index >= len(self.previous) for index in removed)
                    or any(index >= len(self.revised) for index in added)
                    or len(self.previous) - len(removed) != len(self.revised) - len(added)):
                raise ValueError('MOUNTING_REVISION_INVALID_HOLE_INDICES')
        return self

    def hole_mapping(self, *, required=True):
        if self.removed_indices is not None or self.added_indices is not None:
            previous = [index for index in range(len(self.previous)) if index not in (self.removed_indices or [])]
            revised = [index for index in range(len(self.revised)) if index not in (self.added_indices or [])]
            return dict(zip(previous, revised, strict=True))
        if len(self.previous) == len(self.revised):
            return {index: index for index in range(len(self.previous))}
        growing = len(self.previous) < len(self.revised)
        shorter, longer = (self.previous, self.revised) if growing else (self.revised, self.previous)
        candidates = iter(enumerate(longer))
        matches = []
        for hole in shorter:
            index = next((index for index, candidate in candidates if candidate == hole), None)
            if index is None:
                if required:
                    raise ValueError('MOUNTING_REVISION_HOLE_MAPPING_REQUIRED')
                return None
            matches.append(index)
        return dict(enumerate(matches)) if growing else {old: new for new, old in enumerate(matches)}


def mounting_request(document):
    return MountingChange.model_validate(document['mounting_hole_change']) if 'mounting_hole_change' in document else None


def mounting_target(change, state):
    previous = [row.model_dump() for row in change.previous]
    if previous != state['board_constraints'].get('mounting_holes', []):
        raise ValueError('MOUNTING_REVISION_BASE_CHANGED')
    revised = [row.model_dump() for row in change.revised]
    if previous == revised:
        raise ValueError('MOUNTING_REVISION_UNCHANGED')
    return revised


def check_board_revision_mode(plan, document):
    has_outline = 'outline_change' in document
    has_stackup = 'stackup_change' in document
    if isinstance(plan, OutlineRevision) != has_outline:
        raise ValueError('OUTLINE_REVISION_PLAN_MISMATCH')
    if isinstance(plan, StackupRevision) != (has_stackup and not has_outline):
        raise ValueError('STACKUP_REVISION_PLAN_MISMATCH')
    if isinstance(plan, MountingRevision) != ('mounting_hole_change' in document and not (has_outline or has_stackup)):
        raise ValueError('MOUNTING_REVISION_PLAN_MISMATCH')
    if isinstance(plan, MountingRevision) and plan.placements and 'placement_requirements' not in document:
        raise ValueError('MOUNTING_REVISION_REQUIRES_PLACEMENT_REQUIREMENTS')
    if isinstance(plan, StackupRevision) and plan.placements and 'placement_requirements' not in document:
        raise ValueError('STACKUP_REVISION_REQUIRES_PLACEMENT_REQUIREMENTS')
    if 'placement_requirements' in document and not isinstance(plan, PlacementRevision):
        raise ValueError('PLACEMENT_REQUIREMENTS_PLAN_MISMATCH')


def outline_target(change, state, construction=None, *, mounting=None):
    before = state['board_constraints']
    previous = change.previous.model_dump()
    if previous != {key: before.get(key) for key in previous}:
        raise ValueError('OUTLINE_REVISION_BASE_CHANGED')
    revised = change.revised.model_dump()
    if previous == revised:
        raise ValueError('OUTLINE_REVISION_UNCHANGED')
    board = stackup_target(construction, state) if construction is not None else before
    if mounting is not None:
        board = dict(board, mounting_holes=mounting_target(mounting, state))
    return validated_board(state, {**board, **revised})


def resize_board(change, plan, state, base_positions, construction=None, *, mounting=None, placements_bound=False):
    board = outline_target(change, state, construction, mounting=mounting)
    if plan.placements:
        if not (change.allow_placement_changes or placements_bound):
            raise ValueError('OUTLINE_REVISION_REQUIRES_INHERITED_PLACEMENTS')
        return revised_board(plan, dict(state, board_constraints=board), base_positions)
    return board_with_inherited_positions(state, board, base_positions), {}


def stackup_target(change, state, *, mounting=None):
    before = state['board_constraints']
    previous = change.previous.model_dump(mode='json')
    if previous != {key: before.get(key) for key in previous}:
        raise ValueError('STACKUP_REVISION_BASE_CHANGED')
    revised = change.revised.model_dump(mode='json')
    if previous == revised:
        raise ValueError('STACKUP_REVISION_UNCHANGED')
    board = {**before, **revised}
    if mounting is not None:
        board = validated_board(state, dict(board, mounting_holes=mounting_target(mounting, state)))
    return board


def revised_stackup(change, state, base_positions, *, mounting=None, plan=None):
    board = stackup_target(change, state, mounting=mounting)
    if plan is not None and plan.placements:
        return revised_board(plan, dict(state, board_constraints=board), base_positions)
    return board_with_inherited_positions(state, board, base_positions), {}


def mounting_board(change, state):
    return validated_board(state, dict(state['board_constraints'], mounting_holes=mounting_target(change, state)))


def revised_mounting(change, state, base_positions, *, plan=None):
    board = mounting_board(change, state)
    if plan is not None and plan.placements:
        return revised_board(plan, dict(state, board_constraints=board), base_positions)
    return board_with_inherited_positions(state, board, base_positions), {}


def board_with_inherited_positions(state, board, base_positions):
    target = dict(state, board_constraints=board)
    if board['placement_profile'] != 'explicit':
        return board_with_placements(target, base_positions)
    validate_placement(target, board)
    return board


class RemovalRequest(Strict):
    components: Annotated[list[Ref], Field(min_length=1, max_length=127)]
    retire_checks: Annotated[list[Key], Field(max_length=100)] = []

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.components)) != len(self.components) or len(set(self.retire_checks)) != len(self.retire_checks):
            raise ValueError('DUPLICATE_CIRCUIT_REMOVAL_ENTRY')
        return self


def removal_request(document):
    return RemovalRequest.model_validate(document['circuit_removal']) if 'circuit_removal' in document else None


def terminal_refs(value):
    if isinstance(value, dict):
        if value.keys() == {'ref', 'number'}:
            yield value['ref']
        else:
            yield from value.get('body_temperature_profiles', {})
            for child in value.values():
                yield from terminal_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from terminal_refs(child)


def removal_contract(contract, removal):
    if removal is None:
        return contract
    removed = set(removal.components)
    retired = set(removal.retire_checks)
    affected = {row['id'] for key in ('requirements', 'i2c_pullups') for row in contract.get(key, [])
                if removed.intersection(terminal_refs(row))}
    if affected != retired:
        raise ValueError('CIRCUIT_REMOVAL_CHECKS_MISMATCH: ' + str(dict(missing=sorted(affected-retired), unrelated=sorted(retired-affected))))
    effective = dict(contract)
    for key in ('requirements', 'i2c_pullups'):
        if key in contract:
            effective[key] = [row for row in contract[key] if row['id'] not in retired]
    used = {scenario for row in effective['requirements'] for scenario in row['scenarios']}
    effective['scenarios'] = [row for row in contract['scenarios'] if row['id'] in used]
    if any(removed.intersection(terminal_refs(row)) for row in effective['scenarios']):
        raise ValueError('CIRCUIT_REMOVAL_RETAINED_SCENARIO_DEPENDENCY')
    return FunctionalContract.model_validate(effective).model_dump(mode='json')


def removal_snapshot(removal, state):
    by_ref = {part['ref']: part['id'] for part in state['components'].values()}
    if set(removal.components) - by_ref.keys():
        raise ValueError('UNKNOWN_COMPONENT_REFERENCE: ' + str(sorted(set(removal.components) - by_ref.keys())))
    ids = [by_ref[ref] for ref in removal.components]
    return ids, removed_circuit(state, ids)

def connectivity_revision(pin_moves, state):
    components = {part['ref']: dict(component_id=part['id'], pins=part['pins'])
                  for part in state['components'].values()}
    nets = {net['name']: net['id'] for net in state['nets'].values()}
    moves = []
    for move in pin_moves:
        if move.from_net not in nets or move.to_net not in nets:
            raise ValueError('UNKNOWN_NET: ' + str((move.from_net, move.to_net)))
        if move.pin.ref not in components:
            raise ValueError('UNKNOWN_COMPONENT_REFERENCE: ' + move.pin.ref)
        pin = resolve({'$pin': [move.pin.ref, move.pin.number]}, components, {})
        moves.append(dict(pin=pin, from_net_id=nets[move.from_net], to_net_id=nets[move.to_net]))
    return moves, dict(state, nets=moved_net_pins(state, moves))

class RequirementTextChange(Strict):
    key: Annotated[str, Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
    previous: Annotated[str, Field(min_length=1, max_length=10000)]
    revised: Annotated[str, Field(min_length=1, max_length=10000)]


class FunctionalLimitChange(Strict):
    id: Key
    previous: Interval
    revised: Interval
    project_requirement: RequirementTextChange | None = None


PullupResistance = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class I2CPullupNominals(Strict):
    sda_ohm: PullupResistance
    scl_ohm: PullupResistance


class I2CPullupChange(Strict):
    id: Key
    previous: I2CPullupNominals
    revised: I2CPullupNominals
    project_requirement: RequirementTextChange | None = None


class ContractDescriptionChange(Strict):
    previous: Annotated[str, Field(min_length=10, max_length=4000)]
    revised: Annotated[str, Field(min_length=10, max_length=4000)]


LIMIT_CHANGES = TypeAdapter(Annotated[list[FunctionalLimitChange], Field(max_length=100)])
I2C_PULLUP_CHANGES = TypeAdapter(Annotated[list[I2CPullupChange], Field(min_length=1, max_length=32)])
ADDITIONAL_CHECKS = TypeAdapter(Annotated[list[FunctionalRequirement], Field(max_length=100)])
ADDITIONAL_SCENARIOS = TypeAdapter(Annotated[list[Scenario], Field(max_length=64)])


def functional_limit_changes(request):
    if request.get('formal_requirements'):
        raise ValueError('REVISION_INHERITS_BASE_FUNCTIONAL_CONTRACT')
    changes = LIMIT_CHANGES.validate_python(request.get('functional_limit_changes', []))
    if len({change.id for change in changes}) != len(changes):
        raise ValueError('DUPLICATE_FUNCTIONAL_LIMIT_CHANGE')
    return changes


def functional_additions(request):
    return (ADDITIONAL_CHECKS.validate_python(request.get('functional_additions', [])),
            ADDITIONAL_SCENARIOS.validate_python(request.get('functional_scenario_additions', [])))


def i2c_pullup_changes(request):
    if 'i2c_pullup_changes' not in request:
        return []
    changes = I2C_PULLUP_CHANGES.validate_python(request['i2c_pullup_changes'])
    if len({change.id for change in changes}) != len(changes):
        raise ValueError('DUPLICATE_I2C_PULLUP_CHANGE')
    return changes


def contract_description_change(request):
    if 'contract_description_change' not in request:
        return None
    return ContractDescriptionChange.model_validate(request['contract_description_change'])


def check_i2c_pullup_plan(plan, changes, base_run, base):
    if not changes:
        return
    if not isinstance(plan, (ComponentRevision, ComponentGroupRevision)):
        raise ValueError('REVISION_I2C_PULLUP_REQUIRES_COMPONENT_REVISION')
    checks = (base_run / 'checks.json').read_bytes()
    if sha(checks) != base['file_hashes']['checks']:
        raise ValueError('REVISION_BASE_CHECKS_NOT_BOUND_TO_DELIVERY')
    measurements = json.loads(checks)['functional_check']['measurements']
    measured = {row['requirement']: row['pullups'] for row in measurements
                if row.get('quantity') == 'i2c_pullups' and row['passed'] is True}
    targets = set()
    for change in changes:
        if change.id not in measured:
            raise ValueError('REVISION_I2C_PULLUP_BASE_EVIDENCE_MISSING: ' + change.id)
        for line, field in (('SDA', 'sda_ohm'), ('SCL', 'scl_ohm')):
            if getattr(change.previous, field) != getattr(change.revised, field):
                targets.add(measured[change.id][line]['reference'])
    replacements = plan.replacements if isinstance(plan, ComponentGroupRevision) else [plan.replacement]
    if {row.ref for row in replacements} != targets:
        raise ValueError('REVISION_I2C_PULLUP_REPLACEMENTS_MISMATCH: ' + ', '.join(sorted(targets)))


def revised_functional_contract(original, changes, additions=(), scenarios=(), *,
                                pullup_changes=(), description_change=None):
    if not changes and not additions and not scenarios and not pullup_changes and description_change is None:
        return original
    if any(scenario.engine == 'linear_dc' and not scenario.linear_sources for scenario in scenarios):
        raise ValueError('JOB_LINEAR_SOURCE_ENVELOPE_REQUIRED')
    effective = copy.deepcopy(original)
    if description_change is not None:
        if description_change.previous != effective['description']:
            raise ValueError('REVISION_PREVIOUS_CONTRACT_DESCRIPTION_CHANGED')
        if not description_change.revised.strip() or description_change.revised == description_change.previous:
            raise ValueError('REVISION_CONTRACT_DESCRIPTION_UNCHANGED')
        effective['description'] = description_change.revised
    by_id = {row['id']: row for row in effective['requirements']}
    for change in changes:
        row = by_id.get(change.id)
        if row is None:
            raise ValueError('REVISION_UNKNOWN_FUNCTIONAL_LIMIT: ' + change.id)
        dimension = MEASUREMENT_DIMENSIONS[row['quantity']]
        previous = change.previous.normalized(dimension)
        revised = change.revised.normalized(dimension)
        if previous != (row['minimum'], row['maximum']):
            raise ValueError('REVISION_PREVIOUS_FUNCTIONAL_LIMIT_CHANGED: ' + change.id)
        if previous == revised:
            raise ValueError('REVISION_FUNCTIONAL_LIMIT_UNCHANGED: ' + change.id)
        row['minimum'], row['maximum'] = revised
    pullups = {row['id']: row for row in effective.get('i2c_pullups', [])}
    for change in pullup_changes:
        row = pullups.get(change.id)
        if row is None:
            raise ValueError('REVISION_UNKNOWN_I2C_PULLUP: ' + change.id)
        previous = change.previous.model_dump()
        revised = change.revised.model_dump()
        if previous != {field: row[field] for field in previous}:
            raise ValueError('REVISION_PREVIOUS_I2C_PULLUP_CHANGED: ' + change.id)
        if previous == revised:
            raise ValueError('REVISION_I2C_PULLUP_UNCHANGED: ' + change.id)
        row.update(revised)
    effective['requirements'].extend(row.model_dump(mode='json') for row in additions)
    effective['scenarios'].extend(row.model_dump(mode='json') for row in scenarios)
    return FunctionalContract.model_validate(effective).model_dump(mode='json')


def revised_requirement_items(original, changes):
    by_key = {item['key']: item for item in original}
    changes_by_key = {}
    for change in changes:
        edit = change.project_requirement
        if edit is None:
            if change.id in by_key:
                raise ValueError('REVISION_FUNCTIONAL_REQUIREMENT_TEXT_CHANGE_REQUIRED: ' + change.id)
            continue
        if change.id in by_key and edit.key != change.id:
            raise ValueError('REVISION_FUNCTIONAL_REQUIREMENT_TEXT_KEY_MISMATCH: ' + change.id)
        if edit.key in changes_by_key:
            raise ValueError('DUPLICATE_REVISION_REQUIREMENT_TEXT_CHANGE: ' + edit.key)
        if edit.key not in by_key or by_key[edit.key]['value'] != edit.previous:
            raise ValueError('REVISION_PREVIOUS_REQUIREMENT_TEXT_CHANGED: ' + edit.key)
        if not edit.revised.strip() or edit.revised == edit.previous:
            raise ValueError('REVISION_REQUIREMENT_TEXT_UNCHANGED: ' + edit.key)
        changes_by_key[edit.key] = dict(by_key[edit.key], value=edit.revised,
                                       origin='user_statement', source_message_ref='requirement.json')
    return [changes_by_key.get(item['key'], item) for item in original]


def native_placements(archive, manifest):
    content = archive.read('assembly-geometry.json')
    if sha(content) != manifest['hashes']['assembly-geometry.json']:
        raise ValueError('REVISION_NATIVE_PLACEMENT_EVIDENCE_CHANGED')
    return {ref: dict(x_mm=row['x'], y_mm=row['y'], rotation_deg=row['angle'] % 360)
            for ref, row in json.loads(content)['components'].items()}


def baseline(run_dir):
    files = {name: (run_dir / (name + '.json')).read_bytes()
             for name in ('run', 'requirement', 'checks', 'journal')}
    run, requirement, checks, journal = (json.loads(value) for value in files.values())
    if (run.get('status') != 'cad_checks_passed' or run.get('software_acceptance') != 'pass'
            or run.get('functional_reference_passed') is not True):
        raise ValueError('REVISION_BASE_RUN_INCOMPLETE')
    if sha(files['requirement']) != run['requirement_sha256']:
        raise ValueError('REVISION_BASE_REQUIREMENT_CHANGED')
    if 'revision_summary_sha256' in run:
        files[SUMMARY_NAME] = (run_dir / SUMMARY_NAME).read_bytes()
        if sha(files[SUMMARY_NAME]) != run['revision_summary_sha256']:
            raise ValueError('REVISION_BASE_SUMMARY_CHANGED')
    effective_hash = journal['binding'].get('effective_acceptance')
    if effective_hash is not None:
        files['effective-acceptance'] = (run_dir / 'effective-acceptance.json').read_bytes()
        effective = json.loads(files['effective-acceptance'])
        if digest(effective) != effective_hash:
            raise ValueError('REVISION_BASE_ACCEPTANCE_CHANGED')
        requirement = dict(requirement, software_acceptance=effective)
    reports = {}
    for call in journal['calls']:
        if call['tool'] in ('functional_check', 'pcb_review_export'):
            if call['tool']=='functional_check' and (
                    call['arguments'].get('replacement') is not None or call['arguments'].get('replacements') is not None):
                continue
            response = call['response']
            if response.get('isError'):
                raise ValueError('REVISION_BASE_CHECK_FAILED')
            reports[call['tool']] = call['arguments'], response['structuredContent']['data']
    functional_args, functional_report = reports['functional_check']
    export_args, export = reports['pcb_review_export']
    contract = functional_args['contract']
    model_bindings = functional_args['model_bindings']
    contract_sha256 = sha(canonical(contract))
    manifest = export['manifest']
    if (checks['functional_check'] != functional_report or checks['pcb_review_export'] != export
            or functional_report['verdict'] != 'pass'
            or functional_report['contract_sha256'] != contract_sha256
            or functional_args['contract_sha256'] != contract_sha256
            or functional_args['expected_spec_sha256'] != functional_report['spec_sha256']
            or functional_report['project_id'] != run['project_id']
            or functional_report['revision'] != run['revision']
            or export_args != dict(project_id=run['project_id'], pcb_build_id=run['pcb_build_id'])
            or manifest['pcb_build_id'] != run['pcb_build_id'] or manifest['revision'] != run['revision']
            or manifest['assembly']['status'] != 'complete_source_linked_tables'
            or manifest['portable_project']['passed'] is not True):
        raise ValueError('REVISION_BASE_CHECKS_NOT_BOUND_TO_DELIVERY')
    bundle = Path(export['bundle_path']).resolve()
    project_dir = (Path(journal['binding']['data_dir']) / run['project_id']).resolve()
    required = {'bom.csv', 'positions.csv', 'assembly-geometry.json', 'portable/design.kicad_sch',
                'portable/design.kicad_pcb', 'portable/design.kicad_pro'}
    if not bundle.is_relative_to(project_dir):
        raise ValueError('REVISION_BASE_BUNDLE_NOT_BOUND_TO_PROJECT')
    if not required <= manifest['hashes'].keys():
        raise ValueError('REVISION_BASE_BUNDLE_INCOMPLETE')
    with bundle.open('rb') as stream:
        bundle_sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        stream.seek(0)
        with ZipFile(stream) as archive:
            members = archive.namelist()
            if (len(set(members)) != len(members) or set(members) != {'manifest.json', *manifest['hashes']}
                    or json.loads(archive.read('manifest.json')) != manifest):
                raise ValueError('REVISION_BASE_BUNDLE_MANIFEST_CHANGED')
            if any(sha(archive.read(name)) != expected for name, expected in manifest['hashes'].items()):
                raise ValueError('REVISION_BASE_BUNDLE_CONTENT_CHANGED')
            placements = native_placements(archive, manifest)
    file_hashes = {name: sha(value) for name, value in files.items()}
    binding = dict(
        base_sha256=digest(dict(file_hashes=file_hashes, bundle_sha256=bundle_sha256)),
        project_id=run['project_id'], revision=run['revision'], pcb_build_id=run['pcb_build_id'],
        spec_sha256=functional_report['spec_sha256'], contract_sha256=contract_sha256,
        bundle_path=str(bundle), bundle_sha256=bundle_sha256, file_hashes=file_hashes, placements=placements,
    )
    return binding, requirement, contract, model_bindings


def mounting_criteria(original, change):
    prefix = '/details/mounting/holes/'
    indexed = {}
    for row in original:
        if row.report == 'pcb_verify' and row.pointer.startswith(prefix):
            match = re.fullmatch(r'(0|[1-9][0-9]*)/(.+)', row.pointer[len(prefix):])
            if match and int(match[1]) < len(change.previous):
                indexed[row.id] = (int(match[1]), match[2])
    if not indexed:
        return original
    pairs = change.hole_mapping()
    inherited = []
    for row in original:
        if row.id not in indexed:
            inherited.append(row)
            continue
        old, field = indexed[row.id]
        if old not in pairs:
            continue
        new = pairs[old]
        update = dict(pointer=f'{prefix}{new}/{field}')
        before = change.previous[old].model_dump()
        if (field in before and row.operator == 'equals' and type(row.expected) in (int, float)
                and row.expected == before[field]):
            after = getattr(change.revised[new], field)
            if before[field] != after:
                update['expected'] = after
        inherited.append(row.model_copy(update=update))
    return inherited


def revised_criteria(base_requirement, request, *, outline=None, construction=None, mounting=None):
    original = criteria(base_requirement)
    if not original:
        raise ValueError('REVISION_BASE_ACCEPTANCE_REQUIRED')
    if not isinstance(request, dict) or not isinstance(request.get('natural_language'), str) or not request['natural_language'].strip():
        raise ValueError('REVISION_REQUEST_TEXT_REQUIRED')
    additional = criteria(request)
    if {row.id for row in original} & {row.id for row in additional}:
        raise ValueError('REVISION_CANNOT_OVERRIDE_BASE_ACCEPTANCE')
    if mounting is not None:
        original = mounting_criteria(original, mounting)
    if len(original) + len(additional) > 100:
        raise ValueError('REVISION_ACCEPTANCE_LIMIT_EXCEEDED')
    board_values = {}
    if outline is not None:
        board_values['/details/fabrication/rectangle_bounds_mm/2'] = (
            outline.previous.width_mm, outline.revised.width_mm)
        board_values['/details/fabrication/rectangle_bounds_mm/3'] = (
            outline.previous.height_mm, outline.revised.height_mm)
    if construction is not None:
        board_values['/details/stackup/thickness_mm'] = (
            construction.previous.thickness_mm, construction.revised.thickness_mm)
    inherited = []
    for row in original:
        changed = board_values.get(row.pointer)
        if (changed is not None and row.report == 'pcb_verify' and row.operator == 'equals'
                and type(row.expected) in (int, float) and row.expected == changed[0]
                and changed[0] != changed[1]):
            row = row.model_copy(update={'expected': changed[1]})
        inherited.append(row)
    return inherited + additional or delivery_criteria()


def check_base_state(base, state):
    if state['id'] != base['project_id'] or state['revision'] != base['revision'] or spec_hash(state) != base['spec_sha256']:
        raise ValueError('REVISION_BASE_PROJECT_CHANGED')


def revised_board(plan, state, base_positions):
    components = {part['ref']: part['id'] for part in state['components'].values()}
    updates = {row.ref: row.model_dump(exclude={'ref'}) for row in plan.placements}
    if set(components) != base_positions.keys() or not updates.keys() <= components.keys():
        raise ValueError('REVISION_PLACEMENT_REFERENCE_MISMATCH')
    moved = {}
    for ref, position in updates.items():
        original = base_positions[ref]
        if any(position[key] != value for key, value in original.items()):
            moved[ref] = dict(before=original, after=position)
    if not moved:
        raise ValueError('REVISION_PLACEMENT_UNCHANGED')
    return board_with_placements(state, {**base_positions, **updates}), moved


def validated_board(state, board):
    return BoardConstraints.model_validate(dict(
        project_id=state['id'], expected_revision=state['revision'], idempotency_key='revision-preflight',
        **board,
    )).model_dump(exclude={'project_id', 'expected_revision', 'idempotency_key'})


def board_with_placements(state, positions):
    before = state['board_constraints']
    if not before or before['placement_profile'] not in ('bounded_grid', 'explicit'):
        raise ValueError('REVISION_LAYOUT_PROFILE_UNSUPPORTED')
    placements = []
    for component in state['components'].values():
        position = {'reason': 'Retain placement from the verified parent board.', **positions[component['ref']]}
        placements.append(ComponentPlacement(component_id=component['id'], **position).model_dump())
    after = validated_board(state, {**before, 'placement_profile': 'explicit', 'placements': placements})
    validate_placement(state, after)
    return after


async def replacement_request(call, replacement, original, model_bindings):
    ref = replacement.ref
    matches = [key for key, component in original['components'].items() if component['ref'] == ref]
    if len(matches) != 1 or matches[0] not in original['part_bindings']:
        raise ValueError('REVISION_REQUIRES_SOURCE_BOUND_COMPONENT: ' + ref)
    if ref in model_bindings and replacement.model_id is None:
        raise ValueError('REVISION_REPLACEMENT_REQUIRES_NEW_MODEL_BINDING: ' + ref)
    component_id = matches[0]
    previous = original['part_bindings'][component_id]
    if previous['definition_hash'] == replacement.part_definition_hash:
        raise ValueError('REVISION_COMPONENT_UNCHANGED: ' + ref)
    found = await call('parts_search', dict(query=replacement.mpn))
    selected = select_part(found['candidates'], replacement.mpn, replacement.part_definition_hash)
    part = await call('part_get', dict(candidate_id=selected['candidate_id']))
    if part['definition_hash'] != replacement.part_definition_hash or part['evidence_status'] != 'available':
        raise ValueError('REVISION_REPLACEMENT_EVIDENCE_CHANGED: ' + ref)
    if replacement.model_id is not None:
        verification_models.bound_model(verification_models.packages(), ref, replacement.model_id,
                                        part['definition_hash'])
    found = await call('footprint_search', dict(query=part['footprint_id']))
    matches = [row for row in found['candidates'] if row['footprint_id'] == part['footprint_id']]
    if len(matches) != 1:
        raise ValueError('REVISION_REPLACEMENT_FOOTPRINT_NOT_UNIQUE: ' + ref)
    footprint = await call('footprint_get', dict(candidate_id=matches[0]['candidate_id']))
    return dict(component_id=component_id, candidate_id=selected['candidate_id'],
                footprint_candidate_id=footprint['candidate_id'])


def verify_replacement_state(original, replaced, preview, replacement):
    change = {'reference': replacement.ref, **preview}
    verify_replacement_group_state(original, replaced, [change], [replacement], preview['proposed_spec_sha256'])


def verify_replacement_group_state(original, replaced, changes, replacements, proposed_sha256):
    by_ref = {row['reference']: row for row in changes}
    changed_components = {row['component_id'] for row in changes}
    if by_ref.keys() != {row.ref for row in replacements} or len(changed_components) != len(replacements):
        raise ValueError('REVISION_REPLACEMENT_COVERAGE_MISMATCH')
    unchanged_entries = ('components', 'part_bindings', 'footprint_bindings')
    for key in SPEC_KEYS:
        before, after = original.get(key), replaced.get(key)
        if key in unchanged_entries:
            before = {cid: row for cid, row in before.items() if cid not in changed_components}
            after = {cid: row for cid, row in after.items() if cid not in changed_components}
        if before != after:
            raise ValueError('REVISION_UNEXPECTED_SPEC_CHANGE: ' + key)
    if spec_hash(replaced) != proposed_sha256:
        raise ValueError('REVISION_REPLACEMENT_MISMATCH: ' + ', '.join(by_ref))
    for replacement in replacements:
        row = by_ref[replacement.ref]
        component_id = row['component_id']
        component = original['components'][component_id]
        expected_component = dict(component, value=row['after']['value'])
        if (component['ref'] != replacement.ref or replaced['components'][component_id] != expected_component
                or replaced['part_bindings'][component_id]['definition_hash'] != replacement.part_definition_hash):
            raise ValueError('REVISION_REPLACEMENT_MISMATCH: ' + replacement.ref)


def verify_revision_state(original, revised, board, requirements):
    changed = [key for key in SPEC_KEYS if key not in ('board_constraints', 'requirements')
               and original.get(key) != revised.get(key)]
    if changed or revised['board_constraints'] != board or revised['requirements'] != requirements:
        raise ValueError('REVISION_UNEXPECTED_SPEC_CHANGE: ' + ', '.join(changed))


def verify_reused_pcb(base_run, base, manifest):
    raw = (base_run/'checks.json').read_bytes()
    if sha(raw) != base['file_hashes']['checks']:
        raise ValueError('REVISION_BASE_CHECKS_NOT_BOUND_TO_DELIVERY')
    parent_hashes = json.loads(raw)['pcb_review_export']['manifest']['hashes']
    for ext in ('kicad_pcb', 'kicad_pro', 'kicad_dru'):
        name = 'routed.' + ext
        expected = parent_hashes.get(name)
        if (expected is None or manifest['hashes'].get(name) != expected
                or manifest['hashes'].get('portable/design.' + ext) != expected):
            raise ValueError('REVISION_REUSED_PCB_CHANGED: ' + name)


def verify_delivered_placements(export, expected):
    with ZipFile(export['bundle_path']) as archive:
        actual = native_placements(archive, export['manifest'])
    if set(actual) != expected.keys():
        raise ValueError('REVISION_DELIVERED_PLACEMENT_COVERAGE_MISMATCH')
    for ref, pose in actual.items():
        if (pose['rotation_deg'] != expected[ref]['rotation_deg']
                or any(not math.isclose(pose[key], expected[ref][key], rel_tol=0, abs_tol=1.01e-6)
                       for key in ('x_mm', 'y_mm'))):
            raise ValueError('REVISION_DELIVERED_PLACEMENT_MISMATCH: ' + ref)
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_run', type=Path)
    binding, _, _, _ = baseline(parser.parse_args().base_run)
    print(json.dumps(binding, indent=2))


if __name__ == '__main__':
    main()
