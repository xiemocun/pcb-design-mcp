"""Regression checks for inherited criteria and unchanged placement."""
import copy
import unittest

from pcb_mcp.editing import spec_hash
from pcb_mcp.functional import FunctionalContract
from pcb_mcp_client.revision import (
    MechanicalRevision, MountingChange, MountingRevision, OutlineChange, OutlineRevision, Replacement,
    StackupChange, StackupRevision,
    check_base_state, check_board_revision_mode,
    functional_limit_changes, i2c_pullup_changes,
    outline_target, placement_requirements, resize_board, revised_board, verify_placement_requirements,
    revised_criteria, revised_functional_contract, revised_mounting, revised_requirement_items, revised_stackup, verify_replacement_state,
    verify_revision_state,
)


class MechanicalRevisionTests(unittest.TestCase):
    def setUp(self):
        self.j1 = '1' * 32
        self.j2 = '2' * 32
        self.parent = {
            'id': 'a' * 32, 'revision': 5, 'profile': 'schematic_experiment',
            'components': {self.j1: {'id': self.j1, 'ref': 'J1'}, self.j2: {'id': self.j2, 'ref': 'J2'}},
            'requirements': [{'key': 'existing', 'value': 'same board', 'origin': 'user_statement'}],
            'nets': {},
            'board_constraints': {'placement_profile': 'bounded_grid', 'width_mm': 60.0,
                                  'height_mm': 40.0, 'clearance_mm': 0.31},
        }
        self.base = dict(project_id=self.parent['id'], revision=5, spec_sha256=spec_hash(self.parent), placements={
            'J1': dict(x_mm=14.0, y_mm=15.0, rotation_deg=0),
            'J2': dict(x_mm=25.0, y_mm=10.0, rotation_deg=90),
        })

    def plan(self, **update):
        placement = dict(ref='J1', x_mm=22.0, y_mm=15.0, reason='Requested connector movement.')
        placement.update(update)
        return MechanicalRevision.model_validate(dict(
            mode='mechanical_revision', name='Move a connector', base_sha256='0' * 64,
            requirement_sha256='1' * 64, placements=[placement],
        ))

    def test_mounting_and_authorized_placement_use_the_same_target_board(self):
        hole = dict(x_mm=25.0, y_mm=10.0, diameter_mm=2.2, keepout_mm=1.0)
        change = MountingChange.model_validate(dict(previous=[], revised=[hole]))
        document = dict(mounting_hole_change=change.model_dump())
        args = dict(mode='mounting_revision', name='Relocate enclosure hardware', base_sha256='0' * 64,
                    requirement_sha256='1' * 64, routing_attempt_limit=2, routing_max_passes=20)
        legacy = MountingRevision.model_validate(args)
        self.assertEqual(legacy.model_dump(mode='json'), args)
        check_board_revision_mode(legacy, document)
        moved_connector = dict(ref='J2', x_mm=31.0, y_mm=10.0, rotation_deg=90,
                               reason='Clear the new mounting hole.')
        plan = MountingRevision.model_validate(dict(args, placements=[moved_connector]))
        with self.assertRaisesRegex(ValueError, 'MOUNTING_REVISION_REQUIRES_PLACEMENT_REQUIREMENTS'):
            check_board_revision_mode(plan, document)
        document['placement_requirements'] = [dict(ref='J2', x_mm=31.0)]
        check_board_revision_mode(plan, document)
        verify_placement_requirements(plan, placement_requirements(document), self.base['placements'])
        board, moved = revised_mounting(change, self.parent, self.base['placements'], plan=plan)
        self.assertEqual(board['mounting_holes'], [hole])
        self.assertEqual((board['width_mm'], board['height_mm']), (60, 40))
        self.assertEqual(set(moved), {'J2'})
        placements = {row['component_id']: row for row in board['placements']}
        self.assertEqual(placements[self.j1]['x_mm'], 14)
        self.assertEqual(placements[self.j2]['x_mm'], 31)

    def test_stackup_placement_requires_authorization_and_keeps_legacy_serialization(self):
        previous = dict(layers=2, thickness_mm=1.6, stackup=None)
        self.parent['board_constraints'].update(previous)
        change = StackupChange.model_validate(dict(previous=previous, revised=dict(previous, thickness_mm=1.2)))
        args = dict(mode='stackup_revision', name='Change the board thickness', base_sha256='0' * 64,
                    requirement_sha256='1' * 64, routing_attempt_limit=2, routing_max_passes=20)
        legacy = StackupRevision.model_validate(args)
        self.assertEqual(legacy.model_dump(mode='json'), args)
        document = dict(stackup_change=change.model_dump())
        check_board_revision_mode(legacy, document)
        moved_connector = dict(ref='J2', x_mm=31.0, y_mm=10.0, rotation_deg=90,
                               reason='Clear the new mounting hole.')
        plan = StackupRevision.model_validate(dict(args, placements=[moved_connector]))
        with self.assertRaisesRegex(ValueError, 'STACKUP_REVISION_REQUIRES_PLACEMENT_REQUIREMENTS'):
            check_board_revision_mode(plan, document)
        document['placement_requirements'] = [dict(ref='J2', x_mm=31.0)]
        check_board_revision_mode(plan, document)
        verify_placement_requirements(plan, placement_requirements(document), self.base['placements'])
        hole = dict(x_mm=25.0, y_mm=10.0, diameter_mm=2.2, keepout_mm=1.0)
        mounting = MountingChange.model_validate(dict(previous=[], revised=[hole]))
        board, moved = revised_stackup(change, self.parent, self.base['placements'], mounting=mounting, plan=plan)
        self.assertEqual(board['thickness_mm'], 1.2)
        self.assertEqual(board['mounting_holes'], [hole])
        self.assertEqual((board['width_mm'], board['height_mm']), (60, 40))
        self.assertEqual(set(moved), {'J2'})
        positions = {row['component_id']: row['x_mm'] for row in board['placements']}
        self.assertEqual(positions, {self.j1: 14, self.j2: 31})

    def test_only_requested_component_moves_and_board_rules_are_kept(self):
        board, moved = revised_board(self.plan(), self.parent, self.base['placements'])
        self.assertEqual(list(moved), ['J1'])
        self.assertEqual(board['clearance_mm'], 0.31)
        self.assertEqual(board['placements'][1]['x_mm'], 25.0)
        self.assertEqual(board['placements'][1]['rotation_deg'], 90)

    def test_changed_base_and_outside_placement_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Placement origin outside board'):
            revised_board(self.plan(x_mm=61.0), self.parent, self.base['placements'])
        edited = dict(self.parent, requirements=[])
        with self.assertRaisesRegex(ValueError, 'REVISION_BASE_PROJECT_CHANGED'):
            check_base_state(self.base, edited)

    def test_noop_and_unknown_component_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'REVISION_PLACEMENT_UNCHANGED'):
            revised_board(self.plan(x_mm=14.0), self.parent, self.base['placements'])
        with self.assertRaisesRegex(ValueError, 'REVISION_PLACEMENT_REFERENCE_MISMATCH'):
            revised_board(self.plan(ref='J3'), self.parent, self.base['placements'])

    def test_placement_requirement_checks_exact_target_and_keeps_other_axes(self):
        targets = placement_requirements({'placement_requirements': [{'ref': 'J1', 'x_mm': 22}]})
        positions = self.base['placements']
        verify_placement_requirements(self.plan(), targets, positions)
        for change in (dict(x_mm=21), dict(y_mm=16), dict(rotation_deg=90)):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'PLACEMENT_TARGET_MISMATCH: J1'):
                verify_placement_requirements(self.plan(**change), targets, positions)
        with self.assertRaisesRegex(ValueError, 'REVISION_UNREQUESTED_PLACEMENT: J2'):
            verify_placement_requirements(self.plan(ref='J2'), targets, positions)
        verify_placement_requirements(self.plan(y_mm=16), {'J1': {}}, positions)

    def test_outline_resize_preserves_placements_unless_the_request_permits_moves(self):
        positions = {**self.base['placements'], 'J2': dict(x_mm=54.0, y_mm=10.0, rotation_deg=90)}
        change = OutlineChange.model_validate(dict(previous=dict(width_mm=60, height_mm=40),
                                                   revised=dict(width_mm=50, height_mm=40)))
        plan = OutlineRevision(mode='outline_revision', name='Resize the board',
                               base_sha256='0' * 64, requirement_sha256='1' * 64)
        with self.assertRaisesRegex(ValueError, 'Placement origin outside board'):
            resize_board(change, plan, self.parent, positions)
        candidate = plan.model_copy(update={'placements':self.plan(ref='J2', x_mm=44).placements})
        with self.assertRaisesRegex(ValueError, 'OUTLINE_REVISION_REQUIRES_INHERITED_PLACEMENTS'):
            resize_board(change, candidate, self.parent, positions)
        board, moved = resize_board(change.model_copy(update={'allow_placement_changes':True}),
                                    candidate, self.parent, positions)
        self.assertEqual(board['width_mm'], 50.0)
        self.assertEqual(board['clearance_mm'], 0.31)
        self.assertEqual(board['placements'][0]['x_mm'], 14.0)
        self.assertEqual(list(moved), ['J2'])

    def test_outline_cannot_remove_inherited_mounting_clearance(self):
        parent = copy.deepcopy(self.parent)
        parent['board_constraints']['mounting_holes'] = [
            dict(x_mm=55, y_mm=20, diameter_mm=3, keepout_mm=1)]
        change = OutlineChange.model_validate(dict(previous=dict(width_mm=60, height_mm=40),
                                                   revised=dict(width_mm=50, height_mm=40)))
        with self.assertRaisesRegex(ValueError, 'MOUNTING_HOLE'):
            outline_target(change, parent)

    def test_combined_outline_and_mounting_change_validates_final_geometry(self):
        before = dict(x_mm=58, y_mm=30, diameter_mm=2.2, keepout_mm=1)
        after = dict(before, x_mm=47)
        parent = copy.deepcopy(self.parent)
        parent['board_constraints']['mounting_holes'] = [before]
        outline = OutlineChange(previous=dict(width_mm=60,height_mm=40),revised=dict(width_mm=50,height_mm=40))
        mounting = MountingChange(previous=[before],revised=[after])
        board = outline_target(outline,parent,mounting=mounting)
        self.assertEqual(board['width_mm'],50)
        self.assertEqual(board['mounting_holes'],[after])
        self.assertEqual(parent['board_constraints']['mounting_holes'],[before])
        with self.assertRaisesRegex(ValueError,'MOUNTING_REVISION_BASE_CHANGED'):
            outline_target(outline,parent,mounting=MountingChange(previous=[after],revised=[before]))

    def test_electrical_spec_cannot_change_in_mechanical_revision(self):
        board, _ = revised_board(self.plan(), self.parent, self.base['placements'])
        child = copy.deepcopy(self.parent)
        child['board_constraints'] = board
        verify_revision_state(self.parent, child, board, self.parent['requirements'])
        child['nets']['different'] = {}
        with self.assertRaisesRegex(ValueError, 'REVISION_UNEXPECTED_SPEC_CHANGE: nets'):
            verify_revision_state(self.parent, child, board, self.parent['requirements'])

    def test_original_acceptance_remains_and_cannot_be_replaced(self):
        existing = dict(id='drc', report='drc_run', pointer='/verdict', operator='equals', expected='pass')
        base = {'software_acceptance': [existing]}
        request = {'natural_language': 'Move J1.'}
        self.assertEqual(revised_criteria(base, request)[0].model_dump(), existing)
        request['software_acceptance'] = [dict(existing, expected='fail')]
        with self.assertRaisesRegex(ValueError, 'REVISION_CANNOT_OVERRIDE_BASE_ACCEPTANCE'):
            revised_criteria(base, request)

    def test_mounting_criteria_follow_retained_hole_when_rows_shift(self):
        near = dict(x_mm=3,y_mm=3,diameter_mm=2.2,keepout_mm=1)
        far = dict(x_mm=57,y_mm=37,diameter_mm=2.2,keepout_mm=1.5)
        prefix = '/details/mounting/holes/'
        original = [
            dict(id='near_x',report='pcb_verify',pointer=prefix+'0/x_mm',operator='equals',expected=3),
            dict(id='far_x',report='pcb_verify',pointer=prefix+'1/x_mm',operator='equals',expected=57),
            dict(id='far_clearance',report='pcb_verify',pointer=prefix+'1/keepout_mm',operator='minimum',expected=1.5),
            dict(id='drc',report='drc_run',pointer='/verdict',operator='equals',expected='pass'),
        ]
        base = dict(software_acceptance=original)
        request = dict(natural_language='Remove the near hole and retain the far hole.')
        removed = MountingChange(previous=[near,far],revised=[far])
        self.assertEqual([row.model_dump() for row in revised_criteria(base,request,mounting=removed)], [
            dict(original[1],pointer=prefix+'0/x_mm'),
            dict(original[2],pointer=prefix+'0/keepout_mm'),original[3]])
        moved = dict(far,x_mm=56)
        with self.assertRaisesRegex(ValueError,'MOUNTING_REVISION_HOLE_MAPPING_REQUIRED'):
            revised_criteria(base,request,mounting=MountingChange(previous=[near,far],revised=[moved]))
        combined = MountingChange(previous=[near,far],revised=[moved],removed_indices=[0])
        self.assertEqual([row.model_dump() for row in revised_criteria(base,request,mounting=combined)], [
            dict(original[1],pointer=prefix+'0/x_mm',expected=56),
            dict(original[2],pointer=prefix+'0/keepout_mm'),original[3]])
        with self.assertRaisesRegex(ValueError,'MOUNTING_REVISION_INVALID_HOLE_INDICES'):
            MountingChange(previous=[near,far],revised=[far],removed_indices=[0,0])

        new_near = dict(near,x_mm=5)
        new_center = dict(near,x_mm=30,y_mm=5)
        added = MountingChange(previous=[near,far],revised=[new_center,new_near,far],added_indices=[0])
        self.assertEqual([row.pointer for row in revised_criteria(base,request,mounting=added)],
                         [prefix+'1/x_mm',prefix+'2/x_mm',prefix+'2/keepout_mm','/verdict'])
        none = MountingChange(previous=[near,far],revised=[])
        self.assertEqual([row.model_dump() for row in revised_criteria(base,request,mounting=none)], [original[3]])

    def test_later_outline_revisions_update_the_exact_dimension_and_preserve_the_ceiling(self):
        bounds = '/details/fabrication/rectangle_bounds_mm/'
        first = [
            dict(id='width', report='pcb_verify', pointer=bounds+'2', operator='equals', expected=60),
            dict(id='height', report='pcb_verify', pointer=bounds+'3', operator='equals', expected=40),
            dict(id='width_ceiling', report='pcb_verify', pointer=bounds+'2', operator='maximum', expected=60),
            dict(id='drc', report='drc_run', pointer='/verdict', operator='equals', expected='pass'),
        ]
        request = {'natural_language': 'Resize while keeping the original clearance and width ceiling.'}
        for old_width, new_width in ((60, 50), (50, 54)):
            change = OutlineChange.model_validate(dict(previous=dict(width_mm=old_width, height_mm=40),
                                                       revised=dict(width_mm=new_width, height_mm=40)))
            result = revised_criteria({'software_acceptance': first}, request, outline=change)
            next_criteria = [row.model_dump(mode='json') for row in result]
            self.assertEqual(next_criteria, [dict(first[0], expected=new_width), *first[1:]])
            self.assertEqual(first[0]['expected'], old_width)
            first = next_criteria

    def test_replacement_preserves_component_pin_identity(self):
        original = dict(self.parent, part_bindings={}, footprint_bindings={})
        child = copy.deepcopy(original)
        child['components'][self.j1]['value'] = '10000'
        child['part_bindings'][self.j1] = {'definition_hash': '1' * 64}
        replacement = Replacement(ref='J1', mpn='test-source-part', part_definition_hash='1' * 64)
        preview = dict(component_id=self.j1, after={'value': '10000'}, proposed_spec_sha256=spec_hash(child))
        verify_replacement_state(original, child, preview, replacement)
        child['components'][self.j1]['pins'] = [{'number': '99'}]
        preview['proposed_spec_sha256'] = spec_hash(child)
        with self.assertRaisesRegex(ValueError, 'REVISION_REPLACEMENT_MISMATCH'):
            verify_replacement_state(original, child, preview, replacement)


class FunctionalRevisionTests(unittest.TestCase):
    def setUp(self):
        req = dict(id='out', description='Observe output voltage in the original interval.',
            quantity='voltage', minimum=3., maximum=3.3, scenarios=['step'],
            positive=dict(ref='J2', number='1'), negative=dict(ref='J2', number='2'))
        self.base = FunctionalContract.model_validate(dict(description='Inherited transient fixture and checks.',
            requirements=[req, dict(req, id='unaffected', minimum=0., maximum=5.)],
            scenarios=[dict(id='step', engine='spice_transient', stop_s=0.002, step_s=1e-7)],
        )).model_dump(mode='json')
        self.edit = dict(id='out', previous=dict(minimum=3000, maximum=3300, unit='mV'),
            revised=dict(minimum=1.6, maximum=1.7, unit='V'),
            project_requirement=dict(key='out', previous='Original 3–3.3 V', revised='New 1.6–1.7 V'))
        self.items = [dict(key='out', value='Original 3–3.3 V', origin='user_statement'),
                      dict(key='unaffected', value='Stay at 25°C', origin='user_statement')]

    def changes(self, *edits):
        return functional_limit_changes({'functional_limit_changes': list(edits)})

    def test_only_named_limits_and_linked_text_change(self):
        changes = self.changes(self.edit)
        result = revised_functional_contract(self.base, changes)
        expected = copy.deepcopy(self.base)
        expected['requirements'][0].update(minimum=1.6, maximum=1.7)
        self.assertEqual(result, expected)
        self.assertEqual(self.base['requirements'][0]['minimum'], 3.0)
        self.assertEqual(revised_requirement_items(self.items, changes), [
            dict(self.items[0], value='New 1.6–1.7 V', source_message_ref='requirement.json'), self.items[1]])

    def test_stale_duplicate_and_unlinked_changes_are_refused(self):
        for edit, error in ((dict(self.edit, id='unknown'), 'REVISION_UNKNOWN_FUNCTIONAL_LIMIT'),
                            (dict(self.edit, previous=self.edit['revised']), 'REVISION_PREVIOUS_FUNCTIONAL_LIMIT_CHANGED'),
                            (dict(self.edit, revised=self.edit['previous']), 'REVISION_FUNCTIONAL_LIMIT_UNCHANGED')):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                revised_functional_contract(self.base, self.changes(edit))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_FUNCTIONAL_LIMIT_CHANGE'):
            self.changes(self.edit, self.edit)
        with self.assertRaisesRegex(ValueError, 'REVISION_FUNCTIONAL_REQUIREMENT_TEXT_CHANGE_REQUIRED'):
            revised_requirement_items(self.items, self.changes(dict(self.edit, project_requirement=None)))
        with self.assertRaisesRegex(ValueError, 'REVISION_PREVIOUS_REQUIREMENT_TEXT_CHANGED'):
            revised_requirement_items([dict(self.items[0], value='already changed')], self.changes(self.edit))

    def test_i2c_pullup_revisions_preserve_terminals_and_unrelated_checks(self):
        pullup = dict(id='bus', sda=dict(ref='U1', number='4'), scl=dict(ref='U1', number='5'),
                      supply=dict(ref='U1', number='1'), sda_ohm=10000, scl_ohm=10000)
        contract = dict(self.base, i2c_pullups=[pullup, dict(pullup, id='other_bus')])
        edit = dict(id='bus', previous=dict(sda_ohm=10000, scl_ohm=10000),
                    revised=dict(sda_ohm=5100, scl_ohm=10000))
        changes = i2c_pullup_changes({'i2c_pullup_changes': [edit]})
        updated = revised_functional_contract(contract, [], pullup_changes=changes)
        self.assertEqual(updated, dict(contract, i2c_pullups=[dict(pullup, sda_ohm=5100),
                                                               contract['i2c_pullups'][1]]))
        self.assertEqual(contract['i2c_pullups'][0]['sda_ohm'], 10000)
        with self.assertRaisesRegex(ValueError, 'REVISION_PREVIOUS_I2C_PULLUP_CHANGED'):
            revised_functional_contract(updated, [], pullup_changes=changes)
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_I2C_PULLUP_CHANGE'):
            i2c_pullup_changes({'i2c_pullup_changes': [edit, edit]})
