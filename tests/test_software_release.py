"""Portable fault tests for interrupted RPCs and declared acceptance evidence."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from pcb_mcp.common import DomainError, PCB_REVIEW_RECEIPT, fork_project_id, file_snapshot, request_fingerprint
from pcb_mcp.design_jobs import DesignJobSearch, DesignJobService, stop_timed_out_runner
from pcb_mcp.models import Create, ProjectFork, Read
from pcb_mcp.service import Service
from pcb_mcp_client.journal import Journal, save
from pcb_mcp_client.acceptance import criteria, evaluate
from pcb_mcp_client.cli import configure
from pcb_mcp_client.run_plan import execute


class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'journal.json'
        self.binding = {'inputs': 'frozen'}
        self.pid = 'a' * 32
        self.project = self.root / self.pid
        self.project.mkdir()
        save(self.project / 'state.json', {'revision': 0, 'idempotency': {}})
        self.journal = Journal(self.path, self.binding, self.root)
        entry, _ = self.journal.begin('project_create', {'name': 'demo'})
        self.journal.finish(entry, self.response({'project_id': self.pid, 'revision': 0}))

    @staticmethod
    def response(data):
        return {'isError': False, 'structuredContent': {'data': data}}

    def resume(self):
        return Journal(self.path, self.binding, self.root, True)

    def test_completed_create_replays_same_project(self):
        journal = self.resume()
        entry, cached = journal.begin('project_create', {'name': 'demo'})
        self.assertTrue(cached)
        self.assertEqual(entry['response']['structuredContent']['data']['project_id'], self.pid)

    def test_lost_mutation_response_retains_exact_idempotency_key(self):
        original = dict(project_id=self.pid, expected_revision=0, idempotency_key='original', value='x')
        self.journal.begin('component_add', original)
        save(self.project / 'state.json', {'revision': 1, 'idempotency': {'original': {'result': {'revision': 1}}}})
        journal = self.resume()
        journal.begin('project_create', {'name': 'demo'})
        entry, cached = journal.begin('component_add', dict(original, idempotency_key='new-random'))
        self.assertFalse(cached)
        self.assertEqual(entry['arguments']['idempotency_key'], 'original')

    def test_uncommitted_mutation_can_retry(self):
        self.journal.begin('component_add', dict(project_id=self.pid, expected_revision=0, idempotency_key='original'))
        self.resume()

    def test_lost_create_does_not_create_again(self):
        path = self.root / 'uncertain.json'
        journal = Journal(path, {}, self.root)
        journal.begin('project_create', {'name': 'uncertain'})
        with self.assertRaisesRegex(ValueError, 'UNCERTAIN_NON_IDEMPOTENT'):
            Journal(path, {}, self.root, True)

    def test_confirmed_fork_replays_and_tracks_only_child(self):
        path = self.root / 'fork.json'
        child = 'b' * 32
        (self.root / child).mkdir()
        save(self.root / child / 'state.json', {'revision': 0, 'idempotency': {}})
        journal = Journal(path, {}, self.root)
        args = dict(project_id=self.pid, expected_revision=0, name='revision', idempotency_key='fork')
        entry, _ = journal.begin('project_fork', args)
        journal.finish(entry, self.response({'project_id': child, 'revision': 0}))
        save(self.project / 'state.json', {'revision': 1, 'idempotency': {}})
        resumed = Journal(path, {}, self.root, True)
        entry, cached = resumed.begin('project_fork', args)
        self.assertTrue(cached)
        self.assertEqual(resumed.value['project_id'], child)
        self.assertEqual(entry['response']['structuredContent']['data']['project_id'], child)

    def test_unacknowledged_fork_stops_replay(self):
        path = self.root / 'fork.json'
        journal = Journal(path, {}, self.root)
        journal.begin('project_fork', {'project_id': self.pid})
        with self.assertRaisesRegex(ValueError, 'UNCERTAIN_NON_IDEMPOTENT_CALL: project_fork'):
            Journal(path, {}, self.root, True)

    def test_committed_create_reuses_receipt_and_rejects_changed_state(self):
        service = Service(self.root / 'actual')
        path = self.root / 'create.json'
        args = dict(name='new board', idempotency_key='create-key')
        journal = Journal(path, {}, service.root)
        journal.begin('project_create', args)
        entry, cached = Journal(path, {}, service.root, True).begin('project_create', args)
        self.assertFalse(cached)
        self.assertEqual(entry['arguments'], args)

        result = service.create(Create(**args))
        pid = result['project_id']
        self.assertEqual(service.create(Create(**args)), result)
        self.assertNotIn('_creation_request', service.read(Read(project_id=pid)))
        with self.assertRaisesRegex(DomainError, 'IDEMPOTENCY_CONFLICT'):
            service.create(Create(**dict(args, name='other board')))
        entry, cached = Journal(path, {}, service.root, True).begin(
            'project_create', dict(args, idempotency_key='new-random'))
        self.assertFalse(cached)
        self.assertEqual(entry['arguments'], args)

        state_path = service.root / pid / 'state.json'
        state = json.loads(state_path.read_bytes())
        save(state_path, dict(state, name='externally changed'))
        with self.assertRaisesRegex(ValueError, 'UNCERTAIN_NON_IDEMPOTENT_CALL: project_create'):
            Journal(path, {}, service.root, True)
        self.assertEqual(len(service.list_projects()['projects']), 1)

    def test_committed_fork_requires_matching_receipt_and_unmodified_branch(self):
        service = Service(self.root / 'actual')
        parent = service.create(Create(name='base', idempotency_key='create-key'))
        path = self.root / 'pending-fork.json'
        args = dict(project_id=parent['project_id'], expected_revision=0,
                    name='revision', idempotency_key='fork-key')
        Journal(path, {}, service.root).begin('project_fork', args)
        Journal(path, {}, service.root, True)
        result = service.project_fork(ProjectFork(**args))
        Journal(path, {}, service.root, True)
        self.assertEqual(service.project_fork(ProjectFork(**args)), result)
        child = service.root / result['project_id']
        (child / 'unexpected.kicad_pcb').touch()
        with self.assertRaisesRegex(ValueError, 'UNCERTAIN_NON_IDEMPOTENT_CALL: project_fork'):
            Journal(path, {}, service.root, True)
        self.assertEqual(len(service.list_projects()['projects']), 2)

    def test_fork_recovers_published_initial_state_when_receipt_write_fails(self):
        service = Service(self.root/'actual')
        parent = service.create(Create(name='base',idempotency_key='parent-key'))
        args = dict(project_id=parent['project_id'],expected_revision=0,
                    name='new branch',idempotency_key='fork-key')
        request = ProjectFork(**args)
        path = self.root/'fork-pending.json'
        Journal(path,{},service.root).begin('project_fork',args)
        with patch('pcb_mcp.editing.write_json',side_effect=OSError('test receipt interruption')):
            with self.assertRaisesRegex(OSError,'test receipt interruption'):
                service.project_fork(request)
        child = service.root/fork_project_id(parent['project_id'],'fork-key')
        state = child/'state.json'
        original = state.read_bytes()
        self.assertFalse((service.root/'fork_requests.json').exists())
        save(state,dict(json.loads(original),name='changed'))
        with self.assertRaisesRegex(ValueError,'UNCERTAIN_NON_IDEMPOTENT_CALL: project_fork'):
            Journal(path,{},service.root,True)
        with self.assertRaisesRegex(DomainError,'FORK_STATE_CHANGED'):
            service.project_fork(request)
        state.write_bytes(original)
        Journal(path,{},service.root,True)
        result = service.project_fork(request)
        self.assertEqual(result['project_id'],child.name)
        self.assertEqual(service.project_fork(request),result)
        self.assertEqual(state.read_bytes(),original)
        self.assertEqual(len(service.list_projects()['projects']),2)

    def test_lost_route_does_not_overwrite(self):
        self.journal.begin('pcb_autoroute', {'project_id': self.pid})
        with self.assertRaisesRegex(ValueError, 'UNCERTAIN_NON_IDEMPOTENT'):
            self.resume()

    def test_route_receipt_requires_the_exact_request_and_completed_build(self):
        board = 'b'*32
        args = dict(project_id=self.pid, pcb_build_id=board, max_passes=20)
        state_path = self.project/'state.json'
        state = dict(id=self.pid, revision=0, pcb_builds={board:dict(id=board, revision=0, sources={})})
        save(state_path,state)
        self.journal.finish(self.journal.calls[0],self.response({'project_id':self.pid,'revision':0}))
        self.journal.begin('pcb_autoroute',args)
        before = self.journal.value['snapshot']['state.json']
        route = self.project/'pcb_builds'/board/'routed.kicad_pcb'
        route.parent.mkdir(parents=True)
        route.write_bytes(b'routed fixture')
        with self.assertRaisesRegex(ValueError,'UNCERTAIN_NON_IDEMPOTENT'):
            self.resume()
        board_hash = hashlib.sha256(route.read_bytes()).hexdigest()
        result = dict(pcb_build_id=board,revision=0,board_hash=board_hash,
                      qualification='routed_not_drc_or_manufacturing_approved')
        build = state['pcb_builds'][board]
        build['sources']['routed.kicad_pcb'] = board_hash
        after = request_fingerprint(state)
        build['route_receipt'] = dict(request_sha256=request_fingerprint(dict(args,max_passes=21)),
            before_state_sha256=before,routed_state_sha256=after,result=result,
            artifacts={'routed.kicad_pcb':board_hash})
        save(state_path,state)
        with self.assertRaisesRegex(ValueError,'UNCERTAIN_NON_IDEMPOTENT'):
            self.resume()
        build['route_receipt']['request_sha256'] = request_fingerprint(args)
        save(state_path,state)
        resumed = self.resume()
        resumed.begin('project_create',{'name':'demo'})
        row,cached=resumed.begin('pcb_autoroute',args)
        self.assertTrue(cached)
        self.assertEqual(row['response']['structuredContent']['data'],result)
        self.assertEqual(row['recovered_from'],'pcb_route_receipt')

    def test_review_export_recovery_is_bound_to_its_pre_export_snapshot(self):
        board = 'b' * 32
        args = dict(project_id=self.pid, pcb_build_id=board)
        state = self.project/'state.json'
        save(state, dict(id=self.pid, revision=0, pcb_builds={board: dict(id=board, revision=0)}))
        build = self.project/'pcb_builds'/board
        build.mkdir(parents=True)
        (build/'routed.kicad_pcb').write_bytes(b'native board fixture')
        before = file_snapshot(build)
        self.journal.finish(self.journal.calls[0], self.response({'project_id': self.pid, 'revision': 0}))
        self.journal.begin('pcb_review_export', args)
        self.resume()

        manifest = dict(pcb_build_id=board, revision=0, hashes=before)
        save(build/'manifest.json', manifest)
        bundle = build/'pcb_review_bundle.zip'
        bundle.write_bytes(b'completed archive fixture')
        after = file_snapshot(build)
        with self.assertRaisesRegex(ValueError, 'RESUME_PCB_REVIEW_EVIDENCE_CHANGED'):
            self.resume()
        receipt = dict(request_sha256=request_fingerprint(args),
                       project_state_sha256=hashlib.sha256(state.read_bytes()).hexdigest(),
                       before_build_sha256=request_fingerprint({}), exported_build_sha256=request_fingerprint(after),
                       bundle_sha256=after[bundle.name])
        save(build/PCB_REVIEW_RECEIPT, receipt)
        with self.assertRaisesRegex(ValueError, 'RESUME_PCB_REVIEW_EVIDENCE_CHANGED'):
            self.resume()
        save(build/PCB_REVIEW_RECEIPT, dict(receipt, before_build_sha256=request_fingerprint(before)))
        unrelated = self.project/'unexpected.json'
        unrelated.touch()
        with self.assertRaisesRegex(ValueError, 'RESUME_PCB_REVIEW_EVIDENCE_CHANGED'):
            self.resume()
        unrelated.unlink()
        resumed = self.resume()
        resumed.begin('project_create', {'name': 'demo'})
        row, cached = resumed.begin('pcb_review_export', args)
        self.assertTrue(cached)
        self.assertEqual(row['recovered_from'], 'pcb_review_receipt')
        self.assertEqual(row['response']['structuredContent']['data'],
                         dict(pcb_build_id=board, revision=0, bundle_path=str(bundle), manifest=manifest))

    def test_external_revision_change_refused(self):
        save(self.project / 'state.json', {'revision': 1, 'idempotency': {}})
        with self.assertRaisesRegex(ValueError, 'RESUME_PROJECT_CHANGED'):
            self.resume()

    def test_lost_response_with_later_user_edit_refused(self):
        self.journal.begin('component_add', dict(project_id=self.pid, expected_revision=0, idempotency_key='original'))
        save(self.project / 'state.json', {'revision': 2, 'idempotency': {'original': {'result': {'revision': 1}}}})
        with self.assertRaisesRegex(ValueError, 'RESUME_PROJECT_CHANGED'):
            self.resume()

    def test_changed_artifact_refused(self):
        (self.project / 'board.kicad_pcb').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'RESUME_PROJECT_CHANGED'):
            self.resume()

    def test_changed_inputs_or_runtime_refused_without_overwrite(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'RESUME_INPUT_OR_RUNTIME_CHANGED'):
            Journal(self.path, {'inputs': 'changed'}, self.root, True)
        self.assertEqual(before, self.path.read_bytes())

    def test_changed_call_arguments_refused(self):
        with self.assertRaisesRegex(ValueError, 'RESUME_CALL_SEQUENCE_CHANGED'):
            self.resume().begin('project_create', {'name': 'other'})


class DesignJobDelivery(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.service = DesignJobService()
        self.service.root = Path(temp.name)
        self.key = 'a' * 32
        self.folder = self.service.job_path(self.key)
        self.folder.mkdir(parents=True)
        for name in ('requirement', 'contract'):
            (self.folder / (name + '.json')).write_text('{}')
        self.value = dict(job_id=self.key, status='running', max_attempts=1,
            requirement_sha256=hashlib.sha256(b'{}').hexdigest(),
            contract_sha256=hashlib.sha256(b'{}').hexdigest(),
            attempts=[{'status': 'running'}])

    def test_search_returns_durable_job_for_any_candidate_or_original_wording(self):
        save(self.folder / 'requirement.json', {'natural_language': 'A two-channel 温度 logger'})
        requirement_hash = hashlib.sha256((self.folder / 'requirement.json').read_bytes()).hexdigest()
        first, second = '1' * 32, '2' * 32
        attempts = [dict(status='failed', number=n, result=dict(project_id=pid))
                    for n, pid in enumerate([first, second], 1)]
        self.value.update(name='传感器首版', attempts=attempts, max_attempts=3, requirement_sha256=requirement_hash)
        save(self.folder / 'job.json', self.value)
        for text in ['首版', 'LOGGER 温度', first, second]:
            result = self.service.design_job_search(DesignJobSearch(query=text))
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['jobs'][0]['job_id'], self.key)
            self.assertEqual(result['jobs'][0]['latest_attempt'], 2)
            self.assertEqual(result['jobs'][0]['status'], 'awaiting_revision')
        self.assertEqual(self.service.design_job_search(DesignJobSearch(query='unrelated'))['jobs'], [])

        other = 'b' * 32
        folder = self.service.job_path(other)
        folder.mkdir()
        for filename in ('contract.json', 'requirement.json'):
            (folder / filename).write_bytes((self.folder / filename).read_bytes())
        save(folder / 'job.json', dict(self.value, job_id=other, attempts=[]))
        os.utime(folder / 'job.json', (1, 1))
        first_page = self.service.design_job_search(DesignJobSearch(limit=1))
        second_page = self.service.design_job_search(DesignJobSearch(offset=first_page['next_offset'], limit=1))
        self.assertEqual([first_page['jobs'][0]['job_id'], second_page['jobs'][0]['job_id']], [self.key, other])
        self.assertIsNone(second_page['next_offset'])
        (folder / 'requirement.json').write_text('changed')
        result = self.service.design_job_search(DesignJobSearch(query='温度'))
        self.assertEqual((result['total'], result['unavailable_jobs']), (1, 1))

    def test_incomplete_runner_acceptance_has_no_success_receipt(self):
        result = dict(status='cad_checks_passed', functional_reference_passed=True, software_acceptance='incomplete')
        attempt = self.service.finish_attempt(self.value, self.folder, self.value['attempts'][0], self.folder, result)
        self.assertEqual(attempt['status'], 'failed')
        self.assertNotIn('delivery', attempt)
        self.assertIn('DESIGN_JOB_DELIVERY_INCOMPLETE', str(attempt['diagnosis']))

    def test_old_success_without_receipt_is_reported_as_incomplete(self):
        self.value.update(status='completed_reference_design', attempts=[{'status': 'passed'}])
        path = self.folder / 'job.json'
        save(path, self.value)
        retained = path.read_bytes()
        loaded, _ = self.service.load_design_job(self.key)
        self.assertEqual(loaded['status'], 'needs_attention_attempt_budget')
        self.assertEqual(loaded['attempts'][0]['status'], 'failed')
        self.assertEqual(loaded['attempt_timeout_s'], 300)
        self.assertEqual(path.read_bytes(), retained)

    def test_failed_later_candidate_preserves_verified_delivery_status(self):
        delivery = dict(project_id='b' * 32)
        first = dict(number=1, status='passed', delivery=delivery)
        later = dict(number=2, status='failed')
        self.value.update(status='needs_attention_attempt_budget', max_attempts=2,
                          attempts=[first, later], latest_delivery=dict(attempt=1, **delivery))
        path = self.folder/'job.json'
        save(path,self.value)
        retained = path.read_bytes()
        loaded, _ = self.service.load_design_job(self.key)
        self.assertEqual(loaded['status'], 'completed_reference_design')
        self.assertEqual(loaded['latest_delivery'], self.value['latest_delivery'])
        self.assertEqual(loaded['attempts'], self.value['attempts'])
        self.assertEqual(path.read_bytes(), retained)
        self.service.save_attempt(self.value, self.folder, later)
        self.assertEqual(json.loads(path.read_bytes())['status'], 'completed_reference_design')

        for status in ('running', 'interrupted'):
            with self.subTest(status=status):
                later['status'] = status
                save(path, self.value)
                loaded, _ = self.service.load_design_job(self.key)
                self.assertEqual(loaded['status'], status)
                self.assertEqual(loaded['latest_delivery'], self.value['latest_delivery'])

    def test_timeout_cleanup_reports_process_table_denial(self):
        process = Mock()
        with patch('pcb_mcp_client.process_job.snapshot', side_effect=PermissionError('sandbox process table')):
            errors = stop_timed_out_runner(process, self.folder)
        process.wait.assert_called_once_with(timeout=10)
        self.assertEqual(errors, ['sandbox process table'])


class Acceptance(unittest.TestCase):
    def criterion(self, **kwargs):
        return criteria({'software_acceptance': [dict(id='drc', report='drc_run', pointer='/verdict', operator='equals', expected='pass', **kwargs)]})

    def test_pass_is_limited_to_report_not_hardware(self):
        result = evaluate(self.criterion(), {'drc_run': {'verdict': 'pass'}})
        self.assertEqual(result['verdict'], 'pass')
        self.assertFalse(result['full_function_qualified'])
        self.assertFalse(result['physical_verified'])

    def test_missing_report_never_passes(self):
        self.assertEqual(evaluate(self.criterion(), {})['verdict'], 'incomplete')

    def test_mismatch_fails(self):
        self.assertEqual(evaluate(self.criterion(), {'drc_run': {'verdict': 'fail'}})['verdict'], 'fail')

    def test_functional_criterion_needs_actual_passing_function_report(self):
        requested=criteria({'software_acceptance':[dict(id='function',report='functional_check',pointer='/verdict',operator='equals',expected='pass')]})
        for report,expected in [({},'incomplete'),({'functional_check':{'verdict':'fail'}},'fail'),({'functional_check':{'verdict':'pass'}},'pass')]:
            self.assertEqual(evaluate(requested,report)['verdict'],expected)

    def test_declared_function_requires_contract_before_project_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requirement = root / 'requirement.json'
            save(requirement, {'software_acceptance': [
                dict(id='function', report='functional_check', pointer='/verdict',
                     operator='equals', expected='pass'),
            ]})
            plan = root / 'plan.json'
            save(plan, dict(
                version=1, name='Missing contract', requirement_sha256=hashlib.sha256(requirement.read_bytes()).hexdigest(),
                components=[dict(ref='R1', mpn='fixture', part_definition_hash='0' * 64)],
                nets=[dict(name='FIXTURE', kind='signal', pins=[dict(ref='R1', number='1'), dict(ref='R1', number='2')])],
                settings=[dict(tool='board_constraints_set', args={})],
            ))
            output = root / 'run'
            with patch('pcb_mcp_client.run_plan.stdio_client') as connect:
                with self.assertRaisesRegex(ValueError, 'FUNCTIONAL_CONTRACT_REQUIRED'):
                    asyncio.run(execute(plan, requirement, output))
            connect.assert_not_called()
            self.assertFalse((output / 'journal.json').exists())
            result = json.loads((output / 'run.json').read_text())
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['software_acceptance'], 'incomplete')
            self.assertIsNone(result['project_id'])

    def test_no_mapping_remains_incomplete(self):
        self.assertEqual(evaluate(criteria({'natural_language': 'anything'}), {})['verdict'], 'incomplete')

    def test_numeric_limits_and_json_pointer(self):
        requested = criteria({'software_acceptance': [dict(id='limit', report='pcb_layout_check', pointer='/a~1b/0/distance', operator='maximum', expected=3.0)]})
        for actual, verdict in [(2.9, 'pass'), (3.1, 'fail'), (True, 'incomplete'), (float('nan'), 'incomplete')]:
            with self.subTest(actual=actual):
                self.assertEqual(evaluate(requested, {'pcb_layout_check': {'a/b': [{'distance': actual}]}})['verdict'], verdict)

    def test_boolean_not_equal_number(self):
        requested = criteria({'software_acceptance': [dict(id='limit', report='drc_run', pointer='/count', operator='equals', expected=0)]})
        self.assertEqual(evaluate(requested, {'drc_run': {'count': False}})['verdict'], 'fail')

    def test_bad_criteria_rejected_before_execution(self):
        base = dict(id='x', report='drc_run', pointer='/count', operator='maximum', expected=0)
        for change in [dict(operator='eval'), dict(report='exec'), dict(pointer='/~2'), dict(expected=True), dict(expected=float('inf'))]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                criteria({'software_acceptance': [dict(base, **change)]})
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_CRITERION_ID'):
            criteria({'software_acceptance': [base, base]})


class Configuration(unittest.TestCase):
    def test_nested_transport_errors_expose_electrical_reason(self):
        from pcb_mcp_client.run_plan import error_message
        error = ExceptionGroup('transport', [ExceptionGroup('session', [ValueError('PRECISION_TIMER_INTERVAL_MISMATCH')])])
        self.assertIn('PRECISION_TIMER_INTERVAL_MISMATCH', error_message(error))

    def test_relative_config_and_cli_override(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            path = Path(temp) / 'runtime.toml'
            path.write_text('[runtime]\ndata_dir="data"\njava="jdk/bin/java"\n')
            configure(path, Path(temp) / 'explicit')
            self.assertEqual(os.environ['PCB_MCP_DATA_DIR'], str(Path(temp).resolve() / 'explicit'))
            self.assertEqual(os.environ['PCB_MCP_JAVA'], str(Path(temp).resolve() / 'jdk/bin/java'))

    def test_unknown_config_key_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'runtime.toml'
            path.write_text('[runtime]\nexec="script.py"\n')
            with self.assertRaisesRegex(ValueError, 'INVALID_RUNTIME_SETTING'):
                configure(path)


if __name__ == '__main__':
    unittest.main()
