"""Change a delivered RC board's functional limits through the installed MCP."""
import argparse
import asyncio
import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp_client.journal import save, snapshot


def read(path):
    return json.loads(path.read_bytes())


def interval(minimum, maximum, unit='V'):
    return dict(minimum=minimum, maximum=maximum, unit=unit)


def change_request(previous, revised, previous_text, revised_text):
    return dict(natural_language=revised_text, functional_limit_changes=[
        dict(id='rc_step', previous=previous, revised=revised,
             project_requirement=dict(key='output_window', previous=previous_text, revised=revised_text))])


async def main(out, seed, runtime):
    out.mkdir(parents=True, exist_ok=False)
    transcript = []
    first = next(copy.deepcopy(row['request']) for row in seed if row['tool'] == 'design_job_create')
    plan = next(row['request']['plan'] for row in seed if row['tool'] == 'design_job_attempt')
    requirement = json.loads(first['requirement_json'])
    requirement['natural_language'] += ' The J1 input pulse must stay at 4.99–5.01 V in the same window.'
    requirement['formal_requirements'].append(dict(id='input_level', allowed=interval(4.99, 5.01)))
    output_check = first['contract']['requirements'][0]
    first['contract']['requirements'].append(dict(output_check, id='input_level',
        description='Preserve the original input pulse over the observation window.',
        positive=dict(ref='J1', number='1'), negative=dict(ref='J1', number='2'), minimum=4.99, maximum=5.01))
    first['requirement_json'] = json.dumps(requirement, ensure_ascii=False)
    first['max_attempts'] = 1
    server = StdioServerParameters(command=sys.executable,
        args=['-m', 'pcb_mcp_client.cli', '--config', str(runtime), '--data-dir', str(out/'data'), 'serve'],
        cwd='/private/tmp', env={k: v for k, v in os.environ.items() if k != 'PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server, errlog=log) as (rx, tx), ClientSession(rx, tx) as session:
            await session.initialize()

            async def call(tool, request=None, reject=None):
                response = await session.call_tool(tool, {'request': request} if request is not None else {},
                                                   read_timeout_seconds=timedelta(seconds=340))
                transcript.append(dict(tool=tool, request=request, response=response.model_dump(mode='json')))
                save(out/'transcript.json', transcript)
                if reject:
                    messages = '\n'.join(getattr(block, 'text', '') for block in response.content)
                    assert response.isError and reject in messages, str(response)
                    print(tool, reject, flush=True)
                    return None
                assert not response.isError, str(response)
                result = response.structuredContent['data']
                print(tool, result.get('status', 'ok'), flush=True)
                return result

            async def attempt(job, candidate, key, expected='passed'):
                request = dict(job_id=job['job_id'], plan=dict(candidate, requirement_sha256=job['requirement_sha256']),
                    rationale='Verify the unchanged fixture and the explicitly requested functional limits.',
                    idempotency_key=key)
                result = await call('design_job_attempt', request)
                assert result['status'] == expected, result
                run = Path(result['artifact_directory'])/'run'
                checks = read(run/'checks.json')
                assert checks['functional_check']['contract_sha256'] == job['contract_sha256']
                if expected == 'passed':
                    assert result['delivery']['contract_sha256'] == job['contract_sha256']
                    assert checks['drc_run']['details']['unconnected_count'] == 0
                return result, run

            version = (await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            job = await call('design_job_create', first)
            original, original_run = await attempt(job, plan, 'functional-original')
            base = original['delivery']
            original_path = out/'data'/base['project_id']
            original_files, original_run_files = snapshot(original_path), snapshot(original_run)
            original_state = read(original_run/'state.json')
            original_text = next(row['value'] for row in original_state['requirements'] if row['key'] == 'output_window')

            changed_text = 'For the same 5 V pulse at 25°C, change J2.1 relative to J2.2 to 1.60–1.70 V in 1.99–2.01 ms.'
            document = change_request(interval(3000, 3300, 'mV'), interval(1.6, 1.7), original_text, changed_text)
            revise = dict(name='Change the delivered RC voltage response', requirement_json=json.dumps(document),
                          base_job=dict(job_id=job['job_id'], attempt=1), max_attempts=2)
            stale = copy.deepcopy(document)
            stale['functional_limit_changes'][0]['previous']['minimum'] = 2900
            await call('design_job_create', dict(revise, requirement_json=json.dumps(stale)),
                       reject='REVISION_PREVIOUS_FUNCTIONAL_LIMIT_CHANGED')
            stale['functional_limit_changes'][0]['previous']['minimum'] = 3000
            stale['functional_limit_changes'][0]['project_requirement']['previous'] = 'Stale project text'
            await call('design_job_create', dict(revise, requirement_json=json.dumps(stale)),
                       reject='REVISION_PREVIOUS_REQUIREMENT_TEXT_CHANGED')

            revision_job = await call('design_job_create', revise)
            assert revision_job['contract_sha256'] != job['contract_sha256']
            common_plan = dict(name='Revise the RC response', base_sha256=base['base_sha256'])
            failed, failed_run = await attempt(revision_job, dict(common_plan, mode='functional_revision'),
                                              'functional-original-circuit', 'failed')
            assert 'schematic_build_id' not in failed['result'] and 'delivery' not in failed
            assert any(f['code'] == 'FUNCTIONAL_REQUIREMENT_FAILED' for f in failed['diagnosis']['findings'])
            assert snapshot(original_path) == original_files

            mpn = 'CRCW060324K9FKEA'
            options = (await call('parts_search', dict(query=mpn)))['candidates']
            selected, = [row for row in options if row['mpn'] == mpn]
            part = await call('part_get', dict(candidate_id=selected['candidate_id']))
            assert part['facts']['value_ohm'] == 24900.0 and part['evidence_status'] == 'available'
            replacement = dict(ref='R1', mpn=mpn, part_definition_hash=part['definition_hash'])
            revised, revised_run = await attempt(revision_job,
                dict(common_plan, mode='component_revision', replacement=replacement), 'functional-actual-change')
            assert revised['number'] == 2 and revised['delivery']['placements'] == base['placements']
            updated_state = read(revised_run/'state.json')
            for field in ('nets', 'board_constraints', 'passive_rating_requirements', 'power_sources'):
                assert updated_state[field] == original_state[field], field
            resistor, = [cid for cid, row in updated_state['components'].items() if row['ref'] == 'R1']
            assert updated_state['part_bindings'][resistor]['mpn'] == mpn
            old_items = {item['key']: item for item in original_state['requirements']}
            new_items = {item['key']: item for item in updated_state['requirements']}
            assert new_items['output_window']['value'] == changed_text
            assert all(new_items[key] == item for key, item in old_items.items() if key != 'output_window')
            info = read(revised_run/'revision.json')
            assert info['previous_contract_sha256'] == job['contract_sha256']
            assert info['revised_contract_sha256'] == revision_job['contract_sha256']
            assert info['functional_limit_changes'] == document['functional_limit_changes']
            assert [row['status'] for row in (await call('design_job_read', dict(job_id=revision_job['job_id'])))['attempts']] == ['failed', 'passed']
            revised_path = out/'data'/revised['delivery']['project_id']
            revised_files = snapshot(revised_path)

            narrowed_text = 'Keep the revised circuit, pulse and window. Tighten the J2 output to 1.62–1.68 V.'
            second_change = change_request(interval(1.6, 1.7), interval(1.62, 1.68), changed_text, narrowed_text)
            second = await call('design_job_create', dict(name='Tighten the delivered voltage tolerance',
                base_job=dict(job_id=revision_job['job_id'], attempt=2), requirement_json=json.dumps(second_change), max_attempts=1))
            tightened, tightened_run = await attempt(second, dict(mode='functional_revision',
                name='Requalify the revised circuit', base_sha256=revised['delivery']['base_sha256']), 'functional-tighter-limit')
            tightened_state = read(tightened_run/'state.json')
            for field in ('components', 'part_bindings', 'footprint_bindings', 'nets', 'board_constraints'):
                assert tightened_state[field] == updated_state[field], field
            assert tightened['delivery']['placements'] == revised['delivery']['placements']

            original_contract = read(original_run.parents[1]/'contract.json')
            for active_job, expected in ((revision_job, (1.6, 1.7)), (second, (1.62, 1.68))):
                expected_contract = copy.deepcopy(original_contract)
                expected_contract['requirements'][0].update(minimum=expected[0], maximum=expected[1])
                assert read(out/'data/design_jobs'/active_job['job_id']/'contract.json') == expected_contract
            for label, run in (('original', original_run), ('failed', failed_run),
                               ('changed', revised_run), ('tightened', tightened_run)):
                save(out/(label+'-functional.json'), read(run/'checks.json')['functional_check'])
            assert snapshot(original_path) == original_files and snapshot(original_run) == original_run_files
            assert snapshot(revised_path) == revised_files
            assert len((await call('project_list'))['projects']) == 4
    save(out/'verification.json', dict(version=version, original=original, failed=failed, changed=revised,
        tightened=tightened, original_files_unchanged=len(original_files),
        first_revision_files_unchanged=len(revised_files), formal_tasks_passed=0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-transcript', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.output.resolve(), read(args.seed_transcript), args.runtime.resolve()))
