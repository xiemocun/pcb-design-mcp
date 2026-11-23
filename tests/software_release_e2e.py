"""Run with the independently installed Python, from outside the checkout.

Deliberately lose a real component_add response, then resume through the CLI.
Artifacts and original failure records stay in the supplied output directory.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--interrupt', action='store_true')
    args = parser.parse_args()
    root, fixture = args.output.resolve(), args.fixtures.resolve()
    from pcb_mcp_client.run_plan import execute
    from pcb_mcp_client.journal import Journal
    from pcb_mcp.config import data_dir
    import pcb_mcp
    if args.interrupt:
        original = Journal.finish
        def lose_response(self, entry, response):
            if entry['tool'] == 'component_add':
                raise asyncio.CancelledError('test: server committed, response not journaled')
            return original(self, entry, response)
        Journal.finish = lose_response
        try:
            asyncio.run(execute(fixture / 'logic-plan.json', fixture / 'logic-requirement.json', root / 'logic'))
        except asyncio.CancelledError:
            return 0
        raise AssertionError('Interruption did not occur')
    root.mkdir(parents=True, exist_ok=False)
    assert '/site-packages/' in pcb_mcp.__file__, pcb_mcp.__file__
    assert not os.environ.get('PYTHONPATH'), 'Must test installed distribution without PYTHONPATH'
    write(root / 'environment.json', dict(python=sys.executable, package=pcb_mcp.__file__, cwd=str(Path.cwd()), data_dir=str(data_dir())))
    def run(name, command, expected=0):
        with (root / (name + '.log')).open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        assert result.returncode == expected, (name, result.returncode)
    cli = [sys.executable, '-m', 'pcb_mcp_client.cli']
    run('doctor', cli + ['doctor'])
    run('interruption', [sys.executable, str(Path(__file__).resolve()), '--fixtures', str(fixture), '--output', str(root), '--interrupt'])
    interrupted = json.loads((root / 'logic/run.json').read_text())
    write(root / 'interrupted-result.json', interrupted)
    state = json.loads((data_dir() / interrupted['project_id'] / 'state.json').read_text())
    assert state['revision'] == 1 and len(state['components']) == 1
    for name in ('logic', 'pulse'):
        command = cli + ['run', str(fixture / (name + '-plan.json')), '--requirement', str(fixture / (name + '-requirement.json')), '--output', str(root / name)]
        if name == 'logic':
            command.append('--resume')
        run(name, command)
        result = json.loads((root / name / 'run.json').read_text())
        assert result['status'] == 'cad_checks_passed' and result['software_acceptance'] == 'pass', result
        assert not result['physical_verified'] and not result['full_function_qualified']
        if name == 'logic':
            assert result['project_id'] == interrupted['project_id']
        plan = json.loads((fixture / (name + '-plan.json')).read_text())
        state = json.loads((data_dir() / result['project_id'] / 'state.json').read_text())
        assert len(state['components']) == len(plan['components'])
        assert len({c['ref'] for c in state['components'].values()}) == len(plan['components'])
    # An impossible frequency must fail at electrical validation before CAD.
    negative = copy.deepcopy(json.loads((fixture / 'pulse-plan.json').read_text()))
    requirement = copy.deepcopy(json.loads((fixture / 'pulse-requirement.json').read_text()))
    requirement['output'].update(frequency_min_hz=400.0, frequency_max_hz=600.0)
    requirement['natural_language'] = 'Software negative fixture: require 400–600 Hz from the retained approximately 50 Hz design.'
    for setting in negative['settings']:
        if setting['tool'] == 'requirements_set':
            for entry in setting['args']['items']:
                if entry['key'] == 'pulse_frequency_min_hz': entry['value'] = 400.0
                if entry['key'] == 'pulse_frequency_max_hz': entry['value'] = 600.0
                if entry['key'] == 'function': entry['value'] = requirement['natural_language']
    write(root / 'negative-requirement.json', requirement)
    negative['requirement_sha256'] = hashlib.sha256((root / 'negative-requirement.json').read_bytes()).hexdigest()
    for setting in negative['settings']:
        if setting['tool'] == 'requirements_set':
            for entry in setting['args']['items']:
                if entry['key'] == 'frozen_requirement_sha256': entry['value'] = negative['requirement_sha256']
    write(root / 'negative-plan.json', negative)
    run('negative', cli + ['run', str(root / 'negative-plan.json'), '--requirement', str(root / 'negative-requirement.json'), '--output', str(root / 'negative')], expected=1)
    result = json.loads((root / 'negative/run.json').read_text())
    assert result['status'] == 'failed' and 'PRECISION_TIMER_INTERVAL_MISMATCH' in (root / 'negative.log').read_text()
    state = json.loads((data_dir() / result['project_id'] / 'state.json').read_text())
    assert not state['builds'] and not state['pcb_builds']
    write(root / 'verification.json', dict(passed=True, installed_distribution=True, recovered_lost_mutation_response=True,
          successful_cad_cases=['logic', 'pulse'], known_requirement_mismatch_rejected_before_cad=True,
          physical_verified=False, full_function_qualified=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
