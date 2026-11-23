"""Exercise measurement-aware design jobs through a clean installed MCP."""
import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pcb_mcp_client.journal import digest, save, snapshot


def read(path):
    return json.loads(path.read_bytes())


def bounds(minimum, maximum, unit):
    return dict(minimum=minimum, maximum=maximum, unit=unit)


def fixture(project, kind):
    base = project / 'work/v1/development'
    if kind == 'audio':
        run = project / 'work/v1/distribution/dev25-qualification/rc/execution'
        source = base / 'audio-thdn'
        document = read(source / 'rc-requirement.json')
        document['software_acceptance'] = read(run / 'requirement.json')['software_acceptance']
        contract = read(source / 'rc-functional.json')
    else:
        run = base / 'audio-i2s/attempt-03'
        document = read(run / 'requirement.json')
        journal = read(run / 'journal.json')
        contract = next(row['arguments']['contract'] for row in journal['calls'] if row['tool'] == 'functional_check')
    scenarios = {scenario['id']: scenario for scenario in contract['scenarios']}
    document['formal_requirements'] = []
    for requirement in contract['requirements']:
        quantity = requirement['quantity']
        unit = {'voltage': 'V', 'noise_voltage_rms': 'V', 'thd_plus_n': '%', 'i2s_words': 'word'}[quantity]
        lower, upper = requirement['minimum'], requirement['maximum']
        conditions = {}
        if quantity == 'thd_plus_n':
            assert (lower, upper) == (0, 0.00008)
            lower, upper = 0, 0.008
            conditions['measurement_band'] = bounds(*requirement['thdn']['bandwidth_hz'], 'Hz')
        elif quantity == 'noise_voltage_rms':
            noise = scenarios[requirement['scenarios'][0]]
            conditions['measurement_band'] = bounds(*noise['frequency_hz'], 'Hz')
        if requirement.get('time_window_s'):
            conditions['observation_window'] = bounds(*requirement['time_window_s'], 's')
        document['formal_requirements'].append(dict(id=requirement['id'], allowed=bounds(lower, upper, unit),
                                                    conditions=conditions))
    return document, contract, read(run / 'plan.json')


async def main(project, out, runtime, baseline):
    out.mkdir(parents=True, exist_ok=False)
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
    server = StdioServerParameters(command=sys.executable, args=['-m', 'pcb_mcp_client.cli', '--config',
        str(runtime), '--data-dir', str(out / 'data'), 'serve'], cwd='/private/tmp', env=env)
    events, deliveries = [], {}
    with (out / 'server.stderr').open('w') as log:
        async with stdio_client(server, errlog=log) as (rx, tx), ClientSession(rx, tx) as session:
            await session.initialize()

            async def call(tool, request=None, reject=None):
                response = await session.call_tool(tool, {'request': request} if request is not None else {},
                                                   read_timeout_seconds=timedelta(seconds=340))
                events.append(dict(tool=tool, request=request, response=response.model_dump(mode='json')))
                save(out / 'transcript.json', events)
                if reject is not None:
                    messages = '\n'.join(getattr(block, 'text', '') for block in response.content)
                    assert response.isError and reject in messages, str(response)
                    print(tool, reject, flush=True)
                    return None
                assert not response.isError, str(response)
                result = response.structuredContent['data']
                print(tool, result.get('status', 'ok'), flush=True)
                return result

            version = (await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']

            async def create(name, document, contract, reject=None):
                return await call('design_job_create', dict(name=name, requirement_json=json.dumps(document),
                                  contract=contract, max_attempts=1), reject=reject)

            async def attempt(name, job, plan):
                submit = dict(job_id=job['job_id'], plan=dict(plan, requirement_sha256=job['requirement_sha256']),
                              rationale='Replay the source-bound development candidate against the declared measurements.',
                              idempotency_key=name + '-candidate')
                result = await call('design_job_attempt', submit)
                run = Path(result['artifact_directory']) / 'run'
                assert read(run / 'checks.json')['functional_check']['contract_sha256'] == job['contract_sha256']
                return result, run

            for kind in ('audio', 'i2s'):
                document, contract, plan = fixture(project, kind)
                if baseline:
                    for row in document['formal_requirements']:
                        row.pop('conditions')
                    if kind == 'i2s':
                        await create(kind, document, contract, reject='UNIT_NOT_SUPPORTED: word')
                        document['formal_requirements'][0]['allowed']['unit'] = '1'
                    await create(kind, document, contract, reject="'noise_voltage_rms'" if kind == 'audio' else "'i2s_words'")
                    continue
                job = await create(kind, document, contract)
                result, run = await attempt(kind, job, plan)
                assert result['status'] == 'passed' and result['delivery']['contract_sha256'] == job['contract_sha256'], result
                checks = read(run / 'checks.json')
                assert checks['drc_run']['details']['unconnected_count'] == 0
                deliveries[kind] = result['delivery']
                save(out / (kind + '-functional.json'), checks['functional_check'])
                if kind == 'audio':
                    original = out / 'data' / result['delivery']['project_id']
                    before = snapshot(original)
                    row = next(row for row in document['formal_requirements'] if row['id'] == 'audio_rc_thdn')
                    row['conditions']['measurement_band']['maximum'] = 10000
                    await create('audio wrong band', document, contract, reject='FORMAL_MEASUREMENT_BAND_UNCOVERED')
                    row['conditions']['measurement_band']['maximum'] = 20000
                    row['allowed']['maximum'] = 0.005
                    await create('audio wrong limit', document, contract, reject='FROZEN_REQUIREMENT_LIMIT_CHANGED')
                    measure = next(row for row in contract['requirements'] if row['id'] == 'audio_rc_thdn')
                    measure['maximum'] = 0.00005
                    document['natural_language'] += ' Tight negative control uses a 0.005% limit in the original band.'
                    failed, failed_run = await attempt('audio-tight', await create('audio tighter limit', document, contract), plan)
                    assert failed['status'] == 'failed' and 'schematic_build_id' not in failed['result']
                    assert any(row['code'] == 'FUNCTIONAL_REQUIREMENT_FAILED' for row in failed['diagnosis']['findings'])
                    save(out / 'audio-tight-functional.json', read(failed_run / 'checks.json')['functional_check'])
                    assert snapshot(original) == before
                else:
                    document['formal_requirements'][0]['allowed']['unit'] = 'frame'
                    await create('i2s wrong unit', document, contract, reject='QUANTITY_DIMENSION_MISMATCH')
            projects = (await call('project_list'))['projects']
            assert len(projects) == (0 if baseline else 3)
    save(out / 'verification.json', dict(version=version, baseline=baseline, projects=len(projects),
         transcript_sha256=digest(events), deliveries=deliveries, formal_tasks_passed=0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--baseline', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.project.resolve(), args.output.resolve(), args.runtime.resolve(), args.baseline))
