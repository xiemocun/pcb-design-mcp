"""Exercise an added verification scenario through an installed MCP server."""
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
from pcb_mcp.editing import SPEC_KEYS
from pcb_mcp_client.journal import save, snapshot


def read(path):
    return json.loads(path.read_bytes())


async def exercise(out, seed, runtime, before, retain_pcb):
    out.mkdir(parents=True, exist_ok=False)
    events = []
    first = next(row['request'] for row in seed if row['tool'] == 'design_job_create')
    first_attempt = next(row['request'] for row in seed if row['tool'] == 'design_job_attempt')
    server = StdioServerParameters(command=sys.executable,
        args=['-m', 'pcb_mcp_client.cli', '--config', str(runtime), '--data-dir', str(out/'data'), 'serve'],
        cwd='/private/tmp', env={key: value for key, value in os.environ.items() if key != 'PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server, errlog=log) as (rx, tx), ClientSession(rx, tx) as session:
            await session.initialize()

            async def call(tool, request=None, error=None):
                reply = await session.call_tool(tool, {'request': request} if request is not None else {},
                                               read_timeout_seconds=timedelta(seconds=345))
                events.append(dict(tool=tool, request=request, response=reply.model_dump(mode='json')))
                save(out/'transcript.json', events)
                print(tool, error or ('error' if reply.isError else 'ok'), flush=True)
                if error:
                    assert reply.isError and error in str(reply), str(reply)
                    return
                assert not reply.isError, str(reply)
                return reply.structuredContent['data']

            version = (await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            first_job = await call('design_job_create', first)
            first_result = await call('design_job_attempt', dict(first_attempt, job_id=first_job['job_id']))
            assert first_result['status'] == 'passed', first_result
            base = first_result['delivery']
            parent = out/'data'/base['project_id']
            parent_files = snapshot(parent)
            parent_run = Path(first_result['artifact_directory'])/'run'
            contract = read(parent_run.parents[1]/'contract.json')
            lower = copy.deepcopy(contract['scenarios'][0])
            lower['id'] = 'step_low_supply'
            lower['drives'][0]['pulse'][1] = 4.75
            check = dict(contract['requirements'][0], id='low_supply_output', scenarios=[lower['id']],
                         description='Check the output in the original observation window at the lower pulse amplitude.',
                         minimum=2.96, maximum=3.02)
            request = dict(natural_language='Also require the unloaded output to remain between 2.96 and 3.02 V '
                'from 1.99 to 2.01 ms when the original pulse amplitude is 4.75 V at 25 C. '
                'Preserve the 5 V test, its limits, all existing electrical connections and board rules.',
                functional_additions=[check], functional_scenario_additions=[lower])
            save(out/'low-supply-requirement.json', request)
            create = dict(name='Check lower pulse amplitude in addition to nominal', max_attempts=1,
                          base_job=dict(job_id=first_job['job_id'], attempt=1))

            async def revision(document, error=None):
                return await call('design_job_create', dict(create, requirement_json=json.dumps(document)), error)

            if before:
                await revision(request, 'REQUIREMENT_SCENARIO_MISSING')
                assert snapshot(parent) == parent_files
                save(out/'verification.json', dict(version=version, first=first_result,
                     rejected='REQUIREMENT_SCENARIO_MISSING', parent_files_unchanged=len(parent_files)))
                return

            await revision(dict(request, functional_scenario_additions=[dict(lower, id='step')]),
                           'DUPLICATE_FUNCTIONAL_ID')
            await revision(dict(request, functional_additions=[]), 'UNMEASURED_SCENARIO')
            assert len((await call('project_list'))['projects']) == 1

            async def attempt(job, key):
                candidate = dict(job_id=job['job_id'], idempotency_key=key,
                    rationale='Keep the original circuit and run both pulse scenarios as frozen in the job.',
                    plan=dict(mode='functional_revision', name='Verify both pulse amplitudes',
                              base_sha256=base['base_sha256'], requirement_sha256=job['requirement_sha256']))
                result = await call('design_job_attempt', candidate)
                return result, Path(result['artifact_directory'])/'run'

            bad_check = dict(check, minimum=3.05, maximum=3.10)
            bad_doc = dict(request, natural_language=request['natural_language'].replace('2.96 and 3.02', '3.05 and 3.10'),
                           functional_additions=[bad_check])
            save(out/'out-of-range-requirement.json', bad_doc)
            failed, failed_run = await attempt(await revision(bad_doc), 'scenario-out-of-range')
            assert failed['status'] == 'failed' and 'schematic_build_id' not in failed['result'], failed
            failed_report = read(failed_run/'checks.json')['functional_check']
            assert [row['passed'] for row in failed_report['measurements']] == [True, False]

            job = await revision(request)
            revised, run = await attempt(job, 'scenario-verify-both')
            assert revised['status'] == 'passed' and revised['delivery']['contract_sha256'] == job['contract_sha256'], revised
            assert revised['delivery']['placements'] == base['placements']
            effective = read(run.parents[1]/'contract.json')
            assert effective['scenarios'] == [*contract['scenarios'], lower]
            assert effective['requirements'] == [*contract['requirements'], check]
            before_state, after_state = read(parent_run/'state.json'), read(run/'state.json')
            for key in set(SPEC_KEYS) - {'requirements'}:
                assert before_state.get(key) == after_state.get(key), key
            checks = read(run/'checks.json')
            assert checks['drc_run']['details']['unconnected_count'] == 0
            if retain_pcb:
                old_hashes = read(parent_run/'checks.json')['pcb_review_export']['manifest']['hashes']
                new_hashes = checks['pcb_review_export']['manifest']['hashes']
                for ext in ('kicad_pcb', 'kicad_pro', 'kicad_dru'):
                    assert old_hashes['routed.'+ext] == new_hashes['portable/design.'+ext]
                journal = read(run/'journal.json')
                used = {row['tool'] for row in journal['calls']}
                assert {'pcb_verify', 'drc_run', 'pcb_copper_check', 'pcb_review_export'} <= used
                assert not {'pcb_autoroute', 'pcb_route_retry_prepare'} & used
                assert revised['result']['routing_attempt_count'] == 0
            report = checks['functional_check']
            assert [row['passed'] for row in report['measurements']] == [True, True]
            assert snapshot(parent) == parent_files
            following = await call('design_job_create', dict(name='Subsequent revision preserves both scenarios',
                base_job=dict(job_id=job['job_id'], attempt=1), requirement_json=json.dumps(dict(
                    natural_language='Keep both verified supply checks for the next revision.'))))
            assert following['contract_sha256'] == job['contract_sha256']
    save(out/'functional.json', report)
    save(out/'negative-functional.json', failed_report)
    save(out/'verification.json', dict(version=version, first=first_result, revised=revised, failed=failed,
        parent_files_unchanged=len(parent_files), formal_tasks_passed=0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-transcript', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before', action='store_true')
    parser.add_argument('--retain-pcb', action='store_true')
    args = parser.parse_args()
    asyncio.run(exercise(args.output.resolve(), read(args.seed_transcript), args.runtime.resolve(),
                         args.before, args.retain_pcb))
