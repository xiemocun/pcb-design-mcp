"""Retry only incomplete numerics while retaining one electrical contract."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pcb_mcp import spice, transient_recovery
from pcb_mcp.functional import Scenario


class RecoveryTests(unittest.TestCase):
    def scenario(self, **recovery):
        retry = dict(first_timeout_s=5., integration_method='gear', step_s=.0005)
        retry.update(recovery)
        return Scenario(id='case', engine='spice_transient', stop_s=.1, step_s=.001, integration_method='trap',
                        solver_timeout_s=12., transient_recovery=retry)

    def test_requires_finite_resolution_and_reserved_time(self):
        with self.assertRaisesRegex(ValueError, 'RECOVERY_TRANSIENT_GRID_INVALID'):
            self.scenario(step_s=.002)
        with self.assertRaisesRegex(ValueError, 'RECOVERY_REQUIRES_RESERVED_SOLVER_BUDGET'):
            self.scenario(first_timeout_s=12.)
        old = Scenario(id='existing', engine='spice_transient', stop_s=.1, step_s=.001)
        self.assertNotIn('transient_recovery', old.model_dump(mode='json'))

    def run_fixture(self, outcomes, check):
        identity = dict(library_sha256='retained-test-fixture')
        calls = []

        def solver(deck, vectors, directory, timeout, **options):
            calls.append(dict(deck=deck, vectors=vectors, directory=directory, timeout=timeout, options=options))
            directory.mkdir()
            (directory / 'input.json').write_text(json.dumps(dict(runtime=identity, deck=deck)))
            outcome = outcomes[len(calls)-1]
            if isinstance(outcome, str):
                raise ValueError(outcome)
            return {}, {'time': outcome}

        with tempfile.TemporaryDirectory() as parent, patch('pcb_mcp.spice.runtime_identity', return_value=identity), patch('pcb_mcp.spice.run', side_effect=solver):
            folder = Path(parent) / 'scenario'
            def invoke():
                return transient_recovery.run('Fixture\nR1 out 0 100\n.tran 0.001 0.1 0 0.001\n.end\n',
                    ['time', 'v(out)'], self.scenario(), folder, dialect='ltspice', vendor_models={'retained': 'same'})

            check(invoke, calls, folder)

    def test_only_identified_transient_timestep_failures_are_recoverable(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            deck='generated circuit'
            normal=dict(status='fail', runtime_sha256='runtime', deck_sha256=hashlib.sha256(deck.encode()).hexdigest(),
                        controlled_exits=[],commands=[0,0],errors=[
                            'stderr doAnalyses: TRAN:  Timestep too small; time = 0.05, timestep = 1e-17'])
            (path/'process.json').write_text('{"returncode":1}')
            for overrides, expected in [({},True),({'errors':normal['errors']+['error: bad model']},False),
                                       ({'controlled_exits':[1]},False),({'commands':[1]},False),
                                       ({'deck_sha256':'changed'},False),({'runtime_sha256':'other'},False)]:
                with self.subTest(overrides=overrides):
                    (path/'solver.json').write_text(json.dumps(dict(normal,**overrides)))
                    info = spice.timestep_diagnostic(path, deck, 'runtime')
                    self.assertEqual(info, {'solver_stop_s': .05} if expected else None)
            report = dict(normal, errors=['stderr doAnalyses: TRAN: Timestep too small; time = 1e999, timestep = 0'])
            (path/'solver.json').write_text(json.dumps(report))
            self.assertEqual(spice.timestep_diagnostic(path, deck, 'runtime'), {})

    def test_timeout_fallback_keeps_circuit_and_uses_remaining_budget(self):
        def check(invoke, calls, folder):
            _, _, artifacts, evidence = invoke()
            self.assertEqual(artifacts.name, 'attempt-02')
            self.assertEqual(evidence['selected_attempt'], 2)
            self.assertEqual(evidence['attempts'][0]['reason'], 'LTSPICE_TIMEOUT')
            self.assertEqual(calls[0]['deck'].splitlines()[:2], calls[1]['deck'].splitlines()[:2])
            self.assertIn('.tran 0.0005 0.1 0 0.0005\n.options method=gear', calls[1]['deck'])
            self.assertEqual(calls[0]['vectors'], calls[1]['vectors'])
            self.assertEqual(calls[0]['options'], calls[1]['options'])
            self.assertLess(calls[1]['timeout'], 12.)
            self.assertTrue((folder/'attempt-01/input.json').is_file())

        self.run_fixture(['LTSPICE_TIMEOUT', [0., .1]], check)

    def test_incomplete_waveform_can_retry_but_missing_model_and_unverified_cleanup_cannot(self):
        def partial(invoke, calls, folder):
            self.assertEqual(invoke()[3]['attempts'][0]['reason'], 'TRANSIENT_ANALYSIS_INCOMPLETE')
            self.assertEqual(len(calls), 2)
        self.run_fixture([[0., .05], [0., .1]], partial)

        for reason in ('LTSPICE_TIMEOUT_CLEANUP_UNVERIFIED', 'LTSPICE_VENDOR_MODEL_CHANGED'):
            def stopped(invoke, calls, folder):
                with self.assertRaisesRegex(ValueError, reason):
                    invoke()
                self.assertEqual(len(calls), 1)
                self.assertNotIn('selected_attempt', json.loads((folder/'attempts.json').read_text()))
            self.run_fixture([reason], stopped)
