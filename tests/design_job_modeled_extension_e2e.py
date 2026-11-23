"""Qualify a diode branch and its retained model through the installed MCP."""
import argparse
import asyncio
import copy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pcb_mcp_client.journal import digest, save, snapshot


def read(path):
    return json.loads(path.read_bytes())


def pin(ref, number):
    return dict(ref=ref, number=str(number))


async def exercise(out, seed, runtime, sources, before, layout, separate_signal):
    out.mkdir(parents=True, exist_ok=False)
    events = []
    first = next(row['request'] for row in seed if row['tool'] == 'design_job_create')
    first_attempt = next(row['request'] for row in seed if row['tool'] == 'design_job_attempt')
    first_plan = first_attempt['plan']
    if separate_signal:
        request = json.loads(first['requirement_json'])
        request['natural_language'] += (' The ideal development supply at J1 may source up to 1 mA after its '
                                        'declared startup. Use that same 5 V startup in later signal checks.')
        first = dict(first, requirement_json=json.dumps(request))
        envelope = dict(tool='power_envelope_set',args=dict(positive_pin={'$pin':['J1','1']},ground_net_id={'$net':'GND'},
            nominal_v=5.,min_v=5.,max_v=5.,available_current_ma=1.,ambient_c=25.,origin='user_statement',
            reason='Declared 1 mA source capacity of the 5 V ideal development fixture.'))
        first_plan = dict(first_plan, settings=[*first_plan['settings'],envelope],
            requirement_sha256=hashlib.sha256(first['requirement_json'].encode()).hexdigest())
    server = StdioServerParameters(command=sys.executable,
        args=['-m', 'pcb_mcp_client.cli', '--config', str(runtime), '--data-dir', str(out/'data'), 'serve'],
        cwd='/private/tmp', env={key: value for key, value in os.environ.items() if key != 'PYTHONPATH'})
    with (out/'server.stderr').open('w') as log:
        async with stdio_client(server, errlog=log) as (rx, tx), ClientSession(rx, tx) as session:
            await session.initialize()

            async def call(tool, request=None, error=None, package=None):
                if package:
                    request = read(sources/package)
                reply = await session.call_tool(tool, {'request': request} if request is not None else {},
                                               read_timeout_seconds=timedelta(seconds=345))
                retained = dict(package=package, request_sha256=digest(request)) if package else request
                events.append(dict(tool=tool, request=retained, response=reply.model_dump(mode='json')))
                save(out/'transcript.json', events)
                print(tool, error or ('error' if reply.isError else 'ok'), flush=True)
                if error:
                    assert reply.isError and error in str(reply), str(reply)
                    return
                assert not reply.isError, str(reply)
                return reply.structuredContent['data']

            async def attempt(job, plan, key, bindings=None, error=None):
                result = await call('design_job_attempt', dict(job_id=job['job_id'], idempotency_key=key,
                    rationale='Preserve the inherited circuit and verify the requested polarity-selective output.',
                    model_bindings=bindings or {}, plan=dict(plan, requirement_sha256=job['requirement_sha256'])), error)
                return (result, Path(result['artifact_directory'])/'run') if result else (None, None)

            async def part(mpn):
                row, = [row for row in (await call('parts_search', dict(query=mpn)))['candidates'] if row['mpn'] == mpn]
                return await call('part_get', dict(candidate_id=row['candidate_id']))

            version = (await call('system_capabilities'))['contract_version']
            assert not (await call('project_list'))['projects']
            first_job = await call('design_job_create', first)
            first_result, parent_run = await attempt(first_job, first_plan, 'diode-first-design')
            assert first_result['status'] == 'passed', first_result
            base = first_result['delivery']
            parent = out/'data'/base['project_id']
            parent_files = snapshot(parent)
            contract = read(parent_run.parents[1]/'contract.json')

            await call('catalog_import', package='catalog.package.json')
            diode = await call('verification_model_import', package='diode-model.package.json')
            mosfet = await call('verification_model_import', package='mosfet-model.package.json')
            diode_part = await part('1N4148W')
            assert diode_part['definition_hash'] == diode['part_definition_hash']
            resistor = await part('CRCW0603100KFKEA')
            assert resistor['facts']['value_ohm'] == 100000

            normal = contract['requirements'][0]
            forward = dict(normal, id='monitor_forward', positive=pin('J3', 1), negative=pin('J3', 2),
                description='Positive input produces the intended voltage on the diode-isolated monitor.',
                minimum=4.40, maximum=4.70)
            reverse_pulse = copy.deepcopy(contract['scenarios'][0])
            reverse_pulse['id'] = 'negative_pulse'
            reverse_pulse['drives'][0]['pulse'][1] = -5.0
            reverse = dict(forward, id='monitor_reverse', scenarios=['negative_pulse'], minimum=-.01, maximum=.01,
                description='Negative input is blocked at the diode-isolated monitor with the actual pulldown.')
            requirement = dict(natural_language='Keep the original RC output and its 5 V pulse requirement. Add a separate '
                'diode-isolated monitor at J3 and its own 100 kΩ pulldown. At 25 C and 1.99–2.01 ms the new output '
                'must be 4.40–4.70 V for the original ideal 5 V pulse and within ±10 mV for an otherwise identical '
                'ideal −5 V pulse. Preserve the parent circuit, board rules and all existing component locations.',
                functional_additions=[forward, reverse], functional_scenario_additions=[reverse_pulse])
            if separate_signal:
                positive_pulse = copy.deepcopy(contract['scenarios'][0])
                positive_pulse['id'] = 'positive_signal'
                positive_signal = copy.deepcopy(positive_pulse['drives'][0])
                positive_signal.update(positive=pin('J4',1),negative=pin('J4',2))
                positive_pulse['drives'].append(positive_signal)
                negative_signal = copy.deepcopy(positive_signal)
                negative_signal['pulse'][1] = -5.
                reverse_pulse['drives'] = [*contract['scenarios'][0]['drives'], negative_signal]
                forward['scenarios'] = [positive_pulse['id']]
                requirement.update(natural_language='Keep the original 5 V, 1 mA board supply startup and the original '
                    'RC output requirement. Add an independent bipolar signal input at J4 with a common ground, '
                    'a diode-isolated monitor at J3 and a 100 kΩ pulldown. At 25 C and 1.99–2.01 ms require '
                    '4.40–4.70 V at J3 for a +5 V pulse at J4 and ±10 mV for a −5 V pulse at J4. Keep the board '
                    'supply stimulus in both scenarios, with the inherited board rules and component locations.',
                    functional_scenario_additions=[positive_pulse,reverse_pulse])
            save(out/'modeled-extension-requirement.json', requirement)
            job = await call('design_job_create', dict(name='Diode-isolated polarity monitor',
                requirement_json=json.dumps(requirement), base_job=dict(job_id=first_job['job_id'], attempt=1),
                max_attempts=4))
            connector = next(row for row in first_attempt['plan']['components'] if row['ref']=='J2')
            placements = layout or [('D1', 27, 13, 180), ('R2', 37, 14, 0), ('J3', 47, 12, 0)]
            plan = dict(mode='circuit_extension', name='Diode-isolated polarity monitor', base_sha256=base['base_sha256'],
                components=[dict(ref='D1', mpn=diode_part['mpn'], part_definition_hash=diode_part['definition_hash']),
                    dict(ref='R2', mpn=resistor['mpn'], part_definition_hash=resistor['definition_hash'], value='100k'),
                    dict(connector, ref='J3')],
                attachments=[dict(name='INPUT', pins=[pin('D1',2)]), dict(name='GND', pins=[pin('R2',2),pin('J3',2)])],
                nets=[dict(name='MONITOR', kind='signal', pins=[pin('D1',1),pin('R2',1),pin('J3',1)])],
                placements=[dict(ref=ref,x_mm=x,y_mm=y,rotation_deg=angle,
                    reason='Keep the new diode branch above the original circuit and close to the existing return.')
                    for ref,x,y,angle in placements], routing_attempt_limit=4, routing_max_passes=40)
            if separate_signal:
                plan['components'].append(dict(connector,ref='J4'))
                plan['placements'].append(dict(ref='J4',x_mm=10,y_mm=11,rotation_deg=0,
                    reason='Separate the bipolar signal connector from the original supply connector.'))
                plan['attachments'] = [dict(name='GND',pins=[pin('R2',2),pin('J3',2),pin('J4',2)])]
                plan['nets'].append(dict(name='MONITOR_INPUT',kind='signal',pins=[pin('J4',1),pin('D1',2)]))
            save(out/'extension-plan.json', plan)
            models = {'D1':diode['model_id']}
            if before:
                await attempt(job, plan, 'diode-model-refused-before', models, 'REVISION_INHERITS_BASE_FUNCTIONAL_CONTRACT')
                assert snapshot(parent) == parent_files
                save(out/'verification.json', dict(version=version, rejected='REVISION_INHERITS_BASE_FUNCTIONAL_CONTRACT',
                    parent_files_unchanged=len(parent_files)))
                return

            await attempt(job, plan, 'diode-overwrite-parent', {'R1':diode['model_id']},
                          'EXTENSION_MODELS_MUST_BIND_NEW_COMPONENTS')
            for key, model, failure in [('diode-unknown', '0'*64, 'FUNCTIONAL_MODEL_MISSING'),
                                         ('diode-wrong-part', mosfet['model_id'], 'FUNCTIONAL_MODEL_PART_MISMATCH')]:
                rejected, _ = await attempt(job, plan, key, {'D1':model})
                assert rejected['status']=='failed' and rejected['result']['project_id'] is None, rejected
                assert failure in rejected['result']['error'], rejected
            assert len((await call('project_list'))['projects']) == 1

            swapped = copy.deepcopy(plan)
            if separate_signal:
                swapped['nets'][1]['pins'][1] = pin('D1',1)
            else:
                swapped['attachments'][0]['pins'] = [pin('D1',1)]
            swapped['nets'][0]['pins'][0] = pin('D1',2)
            failed, failed_run = await attempt(job, swapped, 'diode-reversed', models)
            assert failed['status']=='failed' and 'schematic_build_id' not in failed['result'], failed
            negative = read(failed_run/'checks.json')['functional_check']
            assert [row['passed'] for row in negative['measurements']] == [True,False,False], negative

            revised, run = await attempt(job, plan, 'diode-correct', models)
            assert revised['status']=='passed' and revised['delivery']['contract_sha256']==job['contract_sha256'], revised
            report = read(run/'checks.json')['functional_check']
            assert [row['passed'] for row in report['measurements']] == [True,True,True]
            assert read(run/'revision.json')['added_model_bindings'] == models
            assert read(run/'checks.json')['drc_run']['details']['unconnected_count'] == 0
            assert all(revised['delivery']['placements'][ref]==pose for ref,pose in base['placements'].items())
            assert snapshot(parent) == parent_files
            revised_files = snapshot(out/'data'/revised['delivery']['project_id'])

            quiet = dict(forward,id='monitor_before_pulse',minimum=-.01,maximum=.01,time_window_s=[.0001,.0009],
                description='Check that the monitor stays at zero before the original pulse begins.')
            following_job = await call('design_job_create', dict(name='Verify monitor before the pulse',
                requirement_json=json.dumps(dict(natural_language='Also verify that the monitor stays within ±10 mV '
                    'from 0.10 to 0.90 ms before the original pulse. Keep the existing circuit and all prior checks.',
                    functional_additions=[quiet])), base_job=dict(job_id=job['job_id'],attempt=revised['number']),
                max_attempts=1))
            following, next_run = await attempt(following_job, dict(mode='functional_revision', name='Verify idle monitor',
                base_sha256=revised['delivery']['base_sha256']), 'diode-model-inherited')
            assert following['status']=='passed', following
            request, = [row['arguments'] for row in read(next_run/'journal.json')['calls'] if row['tool']=='functional_check']
            assert request['model_bindings']==models
            assert all(row['passed'] for row in read(next_run/'checks.json')['functional_check']['measurements'])
            assert snapshot(parent)==parent_files and snapshot(out/'data'/revised['delivery']['project_id'])==revised_files
    save(out/'functional.json',report)
    save(out/'negative-functional.json',negative)
    save(out/'verification.json',dict(version=version,first=first_result,revised=revised,failed=failed,
        next_revision=following, model_bindings=models, parent_files_unchanged=len(parent_files),
        previous_revision_files_unchanged=len(revised_files), formal_tasks_passed=0))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-transcript',type=Path,required=True)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--sources',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--before',action='store_true')
    parser.add_argument('--layout',type=Path)
    parser.add_argument('--separate-signal',action='store_true')
    args=parser.parse_args()
    asyncio.run(exercise(args.output.resolve(),read(args.seed_transcript),args.runtime.resolve(),args.sources.resolve(),
        args.before,read(args.layout) if args.layout else None,args.separate_signal))
