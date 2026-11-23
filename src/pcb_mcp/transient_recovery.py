"""Bounded fallback for transient solver failures, before any measurement."""

import hashlib
import json
import time

from . import spice
from .common import write_json


RETRYABLE = {'LTSPICE_TIMEOUT', 'NGSPICE_TIMEOUT', 'TRANSIENT_ANALYSIS_INCOMPLETE',
             'NGSPICE_TRANSIENT_TIMESTEP_TOO_SMALL'}


def transient_directive(step, stop):
    return f'.tran {step:.15g} {stop:.15g} 0 {step:.15g}'


def integration_deck(deck, method):
    if method is None:
        return deck
    return deck[:-5] + f'.options method={method}\n.end\n'


def require_transient_coverage(waves, stop):
    axis = waves['time']
    if not len(axis) or axis[0] > 0 or axis[-1] < stop * (1 - 1e-10):
        raise ValueError('TRANSIENT_ANALYSIS_INCOMPLETE')


def run(deck, vectors, scenario, folder, *, dialect, vendor_models):
    policy = scenario.transient_recovery
    first_directive = transient_directive(scenario.step_s, scenario.stop_s)
    first_line = f'\n{first_directive}\n'
    if deck.count(first_line) != 1:
        raise ValueError('TRANSIENT_RECOVERY_REQUIRES_GENERATED_TRANSIENT_DECK')
    fallback_line = f'\n{transient_directive(policy.step_s, scenario.stop_s)}\n'
    fallback = deck.replace(first_line, fallback_line, 1)
    options = dict(dialect=dialect, vendor_models=vendor_models)
    budget = scenario.solver_timeout_s or 60.
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    identity_hash = spice.runtime_hash(spice.runtime_identity(dialect))
    evidence = dict(solver_budget_s=budget, runtime_sha256=identity_hash, attempts=[])

    def verify_input(directory, row):
        path = directory / 'input.json'
        if not path.is_file():
            raise ValueError('TRANSIENT_RECOVERY_SOLVER_INPUT_MISSING')
        raw = path.read_bytes()
        row['input_sha256'] = hashlib.sha256(raw).hexdigest()
        if spice.runtime_hash(json.loads(raw)['runtime']) != identity_hash:
            raise ValueError('TRANSIENT_RECOVERY_SOLVER_RUNTIME_CHANGED')

    def attempt(number, input_deck, method, step, timeout):
        directory = folder / f'attempt-{number:02d}'
        row = dict(number=number, artifact_directory=str(directory), integration_method=method,
                   maximum_step_s=step, timeout_s=timeout)
        evidence['attempts'].append(row)
        started_attempt = time.monotonic()
        solver_deck = integration_deck(input_deck, method)
        try:
            meta, waves = spice.run(solver_deck, vectors, directory, timeout=timeout, **options)
            verify_input(directory, row)
            require_transient_coverage(waves, scenario.stop_s)
        except (ValueError, KeyError, OSError) as error:
            row.update(status='failed', reason=str(error))
            if isinstance(error, spice.SimulationFailure):
                row['failure_code'] = str(error)
                row['finding'] = error.finding
            if str(error) in RETRYABLE and 'input_sha256' not in row:
                verify_input(directory, row)
            raise
        else:
            row['status'] = 'completed_waveform'
            return meta, waves, directory
        finally:
            row['elapsed_s'] = time.monotonic() - started_attempt

    try:
        try:
            remaining = budget - (time.monotonic() - started)
            if remaining < 1:
                raise ValueError('TRANSIENT_RECOVERY_BUDGET_EXHAUSTED')
            result = attempt(1, deck, scenario.integration_method, scenario.step_s, min(policy.first_timeout_s, remaining))
        except ValueError as error:
            if str(error) not in RETRYABLE:
                raise
            remaining = budget - (time.monotonic() - started)
            if remaining < 1:
                raise ValueError('TRANSIENT_RECOVERY_BUDGET_EXHAUSTED') from error
            result = attempt(2, fallback, policy.integration_method, policy.step_s, remaining)
        if time.monotonic() - started > budget:
            raise ValueError('TRANSIENT_RECOVERY_BUDGET_EXHAUSTED')
        evidence['selected_attempt'] = evidence['attempts'][-1]['number']
        return (*result, evidence)
    finally:
        evidence['elapsed_s'] = time.monotonic() - started
        write_json(folder / 'attempts.json', evidence)
