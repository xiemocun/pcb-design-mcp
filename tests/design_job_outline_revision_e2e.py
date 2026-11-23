"""Resize a real delivered KiCad project through the normal MCP job interface."""

import argparse
import asyncio
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp_client.acceptance import delivery_criteria
from pcb_mcp_client.journal import save, snapshot


def read(path):
    return json.loads(path.read_bytes())


def board_acceptance():
    width = '/details/fabrication/rectangle_bounds_mm/2'
    return [
        *(row.model_dump(mode='json') for row in delivery_criteria()),
        dict(id='exact_width', report='pcb_verify', pointer=width, operator='equals', expected=60.0),
        dict(id='maximum_width', report='pcb_verify', pointer=width, operator='maximum', expected=60.0),
        dict(id='exact_height', report='pcb_verify', pointer='/details/fabrication/rectangle_bounds_mm/3',
             operator='equals', expected=40.0),
        dict(id='exact_thickness', report='pcb_verify', pointer='/details/stackup/thickness_mm',
             operator='equals', expected=1.6),
    ]


async def combined_revision(call, attempt, job, first, state, construction, unsupported, acceptance_mode):
    previous = {key: state['board_constraints'][key] for key in construction}
    outline = dict(previous=dict(width_mm=60, height_mm=40),
                   revised=dict(width_mm=50, height_mm=40), allow_placement_changes=False)
    requested_placement = dict(ref='J2', x_mm=44)

    async def create(prior, error=None, *, placement=None):
        document = dict(natural_language='Shrink to 50 by 40 mm and use the declared four-layer construction. '
                        'Move only J2 to x=44 mm, with its other coordinates and rotation unchanged. '
                        'Keep the other components and every electrical requirement.',
                        stackup_change=dict(previous=prior, revised=construction), outline_change=outline,
                        placement_requirements=[requested_placement if placement is None else placement])
        return await call('design_job_create', dict(name='Resize and rebuild the delivered PCB',
            requirement_json=json.dumps(document), base_job=dict(job_id=job['job_id'], attempt=1),
            max_attempts=4), error=error)

    if unsupported:
        await create(previous, 'OUTLINE_REVISION_PLAN_MISMATCH')
        return dict(combined_create_rejected=True)
    await create(dict(previous, thickness_mm=1.5), 'STACKUP_REVISION_BASE_CHANGED')
    await create(previous, 'UNKNOWN_PLACEMENT_REQUIREMENT_REFERENCE: J9', placement=dict(ref='J9', x_mm=44))
    combined = await create(previous)
    original = first['delivery']
    candidate = dict(name='Resize and rebuild the board', mode='outline_revision', base_sha256=original['base_sha256'])
    await attempt(combined, 'combined-wrong-mode', dict(candidate, mode='stackup_revision'),
                  'OUTLINE_REVISION_PLAN_MISMATCH')
    move = dict(ref='J2', x_mm=44, y_mm=20, rotation_deg=0, reason='Move only the connector named by the user.')
    extra = dict(ref='R1', x_mm=26, y_mm=20, rotation_deg=0, reason='An unrequested resistor move.')
    failures = {}
    for key, placements, error in (
        ('missing', [], 'REVISION_PLACEMENT_TARGET_MISMATCH: J2'),
        ('wrong_axis', [dict(move, y_mm=21)], 'REVISION_PLACEMENT_TARGET_MISMATCH: J2'),
        ('extra', [move, extra], 'REVISION_UNREQUESTED_PLACEMENT: R1'),
    ):
        rejected = await attempt(combined, 'combined-'+key, dict(candidate, placements=placements))
        assert rejected['status'] == 'failed' and rejected['result']['project_id'] is None
        assert error in rejected['result']['error'], rejected['result']
        failures[key] = rejected
    candidate['placements'] = [move]
    revised = await attempt(combined, 'combined-rebuild', candidate)
    run = Path(revised['artifact_directory'])/'run'
    if acceptance_mode:
        results = {row['id']: row for row in read(run/'acceptance.json')['criteria']}
        assert results['exact_width']['actual'] == 50
        assert results['exact_thickness']['actual'] == construction['thickness_mm']
        assert results['exact_height']['verdict'] == results['maximum_width']['verdict'] == 'pass'
        assert results['exact_height']['expected'] == 40 and results['maximum_width']['expected'] == 60
        if acceptance_mode == 'before':
            assert revised['status'] == 'failed' and 'DECLARED_ACCEPTANCE_FAILED' in revised['result']['error']
            assert results['exact_width']['expected'] == 60
            assert results['exact_thickness']['expected'] == 1.6
            assert results['exact_width']['verdict'] == results['exact_thickness']['verdict'] == 'fail'
            return dict(revised=revised, stale_acceptance=results)
        assert results['exact_width']['expected'] == 50
        assert results['exact_thickness']['expected'] == construction['thickness_mm']
        assert all(row['verdict'] == 'pass' for row in results.values())
    assert revised['status'] == 'passed', revised['result']
    assert await attempt(combined, 'combined-rebuild', candidate) == revised
    assert revised['delivery']['contract_sha256'] == original['contract_sha256']
    expected = {**original['placements'], 'J2':dict(x_mm=44.0, y_mm=20.0, rotation_deg=0.0)}
    assert revised['delivery']['placements'] == expected
    after = read(run/'state.json')
    revision = read(run/'revision.json')
    checks = read(run/'checks.json')
    assert revision['stackup_change'] == dict(previous=previous, revised=construction)
    assert revision['outline_change'] == outline
    assert revision['placement_requirements'] == [requested_placement]
    assert set(revision['moved']) == {'J2'}
    assert after['nets'] == state['nets']
    expected_board = {**state['board_constraints'], **construction, **outline['revised']}
    assert {key: value for key, value in after['board_constraints'].items() if key != 'placements'} == {
        key: value for key, value in expected_board.items() if key != 'placements'}
    assert checks['pcb_verify']['details']['outline_match']
    assert checks['pcb_verify']['details']['stackup']['copper_layers'] == ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
    assert checks['drc_run']['details']['unconnected_count'] == 0
    assert checks['functional_check']['verdict'] == 'pass'
    assert len((await call('project_list'))['projects']) == 2
    result = dict(revised=revised, refused=failures)
    if acceptance_mode == 'after':
        parent = Path(revised['delivery']['bundle_path']).parents[2]
        parent_files = snapshot(parent)
        following_request = dict(natural_language='Widen the 50 mm board to 54 mm without moving any components. '
                                 'Keep the 60 mm maximum width and the four-layer stackup.',
                                 outline_change=dict(previous=outline['revised'],
                                                     revised=dict(width_mm=54, height_mm=40)))
        following_job = await call('design_job_create', dict(name='Widen the accepted four-layer PCB',
            requirement_json=json.dumps(following_request),
            base_job=dict(job_id=combined['job_id'], attempt=revised['number']),
            max_attempts=1))
        following_plan = dict(name='Widen the delivered board', mode='outline_revision',
                              base_sha256=revised['delivery']['base_sha256'])
        following = await attempt(following_job, 'following-width', following_plan)
        assert following['status'] == 'passed', following['result']
        assert following['delivery']['placements'] == revised['delivery']['placements']
        last_run = Path(following['artifact_directory'])/'run'
        actual = {row['id']: row for row in read(last_run/'acceptance.json')['criteria']}
        assert actual['exact_width']['expected'] == actual['exact_width']['actual'] == 54
        assert actual['maximum_width']['expected'] == 60
        assert actual['exact_height']['expected'] == 40
        assert actual['exact_thickness']['expected'] == construction['thickness_mm']
        assert all(row['verdict'] == 'pass' for row in actual.values())
        assert snapshot(parent) == parent_files
        result.update(following=following, revised_parent_files_unchanged=len(parent_files))
    return result


async def exercise(output, runtime, seed, construction=None, expect_unsupported=False, acceptance_mode=None):
    output.mkdir(parents=True, exist_ok=False)
    calls = []
    plan = read(seed/'plan.json')
    requirement = (seed/'requirement.json').read_text()
    if acceptance_mode:
        requirement = json.dumps(dict(json.loads(requirement), software_acceptance=board_acceptance()))
    contract = next(row['arguments']['contract'] for row in read(seed/'journal.json')['calls']
                    if row['tool'] == 'functional_check')
    plan['requirement_sha256'] = hashlib.sha256(requirement.encode()).hexdigest()
    server = StdioServerParameters(
        command=sys.executable,
        args=['-m', 'pcb_mcp_client.cli', '--config', str(runtime), '--data-dir', str(output/'data'), 'serve'],
        cwd='/private/tmp', env={key: value for key, value in os.environ.items() if key != 'PYTHONPATH'},
    )
    with (output/'server.stderr').open('w') as log:
        async with stdio_client(server, errlog=log) as (rx, tx), ClientSession(rx, tx) as session:
            await session.initialize()

            async def call(tool, request=None, error=None):
                reply = await session.call_tool(tool, {'request': request} if request is not None else {},
                                                read_timeout_seconds=timedelta(seconds=345))
                calls.append(dict(tool=tool, request=request, response=reply.model_dump(mode='json')))
                save(output/'transcript.json', calls)
                if error is not None:
                    assert reply.isError and error in str(reply), str(reply)
                    print(tool, error, flush=True)
                    return None
                assert not reply.isError, str(reply)
                print(tool, flush=True)
                return reply.structuredContent['data']

            async def resize(parent, text, previous, revised, *, allow_moves=False, error=None):
                change = dict(previous=previous, revised=revised, allow_placement_changes=allow_moves)
                return await call('design_job_create', dict(name='Resize the delivered rectangular PCB',
                    requirement_json=json.dumps(dict(natural_language=text, outline_change=change)),
                    base_job=dict(job_id=parent['job_id'], attempt=1), max_attempts=2), error=error)

            async def attempt(job, key, candidate, error=None):
                return await call('design_job_attempt', dict(job_id=job['job_id'], idempotency_key=key,
                    rationale='Use the parent circuit and inherited checks with the frozen board outline.',
                    plan=dict(candidate, requirement_sha256=job['requirement_sha256'])), error=error)

            version = (await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            job = await call('design_job_create', dict(name='Deliver the RC reference design from empty',
                requirement_json=requirement, contract=contract, max_attempts=1))
            first = await attempt(job, 'outline-first', plan)
            assert first['status'] == 'passed', first['result']
            original = first['delivery']
            original_dir = output/'data'/original['project_id']
            original_files = snapshot(original_dir)
            original_state = read(Path(first['artifact_directory'])/'run/state.json')
            if construction is not None:
                result = await combined_revision(call, attempt, job, first, original_state,
                                                 construction, expect_unsupported, acceptance_mode)
                assert snapshot(original_dir) == original_files
                save(output/'verification.json', dict(version=version, first=first, **result,
                    parent_files_unchanged=len(original_files), formal_tasks_passed=0))
                return
            original_size = dict(width_mm=60, height_mm=40)
            larger = dict(width_mm=64, height_mm=44)
            smaller = dict(width_mm=50, height_mm=40)
            enlarge_text = 'Resize from 60 by 40 mm to 64 by 44 mm. Retain every component position.'
            await resize(job, enlarge_text, dict(original_size, width_mm=61), larger,
                         error='OUTLINE_REVISION_BASE_CHANGED')
            await resize(job, enlarge_text, original_size, original_size, error='OUTLINE_REVISION_UNCHANGED')
            enlarged_job = await resize(job, enlarge_text, original_size, larger)
            candidate = dict(name='Enlarge the board', mode='outline_revision', base_sha256=original['base_sha256'])
            await attempt(enlarged_job, 'outline-mode-wrong', dict(candidate, mode='functional_revision'),
                          'OUTLINE_REVISION_PLAN_MISMATCH')
            enlarged = await attempt(enlarged_job, 'outline-enlarge', candidate)
            assert enlarged['status'] == 'passed', enlarged['result']
            assert await attempt(enlarged_job, 'outline-enlarge', candidate) == enlarged
            assert enlarged['delivery']['placements'] == original['placements']
            assert enlarged['delivery']['contract_sha256'] == original['contract_sha256']
            enlarged_dir = output/'data'/enlarged['delivery']['project_id']
            enlarged_files = snapshot(enlarged_dir)

            shrink_text = 'Shrink to 50 by 40 mm. Keep all component positions.'
            unsafe_job = await resize(enlarged_job, shrink_text, larger, smaller)
            candidate = dict(candidate, name='Shrink without moving components',
                             base_sha256=enlarged['delivery']['base_sha256'])
            outside = await attempt(unsafe_job, 'outline-outside', candidate)
            assert outside['status'] == 'failed' and not outside['result']['project_id']
            assert 'Placement origin outside board' in outside['result']['error']
            move = dict(ref='J2', x_mm=44, y_mm=20, rotation_deg=0,
                        reason='The user requested this connector position on the smaller board.')
            forbidden = await attempt(unsafe_job, 'outline-forbidden-move', dict(candidate, placements=[move]))
            assert forbidden['status'] == 'failed' and not forbidden['result']['project_id']
            assert 'OUTLINE_REVISION_REQUIRES_INHERITED_PLACEMENTS' in forbidden['result']['error']

            shrink_text = 'Shrink to 50 by 40 mm and move J2 to x=44 mm, y=20 mm. Keep other components fixed.'
            reduced_job = await resize(enlarged_job, shrink_text, larger, smaller, allow_moves=True)
            reduced = await attempt(reduced_job, 'outline-reduce', dict(candidate, placements=[move]))
            assert reduced['status'] == 'passed', reduced['result']
            assert reduced['delivery']['contract_sha256'] == original['contract_sha256']
            actual = reduced['delivery']['placements']
            expected = {**original['placements'], 'J2':dict(x_mm=44.0, y_mm=20.0, rotation_deg=0.0)}
            assert actual == expected, actual

            for attempt_result, dimensions in ((enlarged, larger), (reduced, smaller)):
                run = Path(attempt_result['artifact_directory'])/'run'
                state = read(run/'state.json')
                checks = read(run/'checks.json')
                board = {key: value for key, value in state['board_constraints'].items() if key != 'placements'}
                expected_board = {key: value for key, value in original_state['board_constraints'].items()
                                  if key != 'placements'}
                assert board == {**expected_board, **dimensions}
                assert state['nets'] == original_state['nets']
                assert checks['pcb_verify']['details']['outline_match']
                assert checks['drc_run']['details']['unconnected_count'] == 0
                assert checks['functional_check']['verdict'] == 'pass'
            assert snapshot(original_dir) == original_files and snapshot(enlarged_dir) == enlarged_files
            assert len((await call('project_list'))['projects']) == 3

    save(output/'verification.json', dict(version=version, first=first, enlarged=enlarged, reduced=reduced,
        outside=outside, forbidden_move=forbidden, parent_files_unchanged=len(original_files),
        enlarged_files_unchanged=len(enlarged_files), formal_tasks_passed=0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'runtime', 'seed'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--construction', type=Path)
    parser.add_argument('--expect-unsupported', action='store_true')
    parser.add_argument('--board-acceptance', choices=('before', 'after'))
    args = parser.parse_args()
    asyncio.run(exercise(args.output.resolve(), args.runtime.resolve(), args.seed.resolve(),
                         read(args.construction) if args.construction else None, args.expect_unsupported,
                         args.board_acceptance))
