"""Exercise durable design-job recovery through the installed MCP stdio service."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pcb_mcp import __version__
from pcb_mcp_client.journal import save, snapshot


FAULT = """
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

fault = os.environ.get('PCB_E2E_JOB_FAULT')
root = Path(os.environ['PCB_E2E_JOB_ROOT'])

def owned_run(arguments):
    if '--output' not in arguments:
        return None
    run = Path(arguments[arguments.index('--output')+1]).resolve()
    return run if run.is_relative_to(root/'data/design_jobs') else None

if sys.argv[-1] == 'serve':
    os.environ['PCB_E2E_JOB_OWNER'] = str(os.getpid())
    if fault in ('after_first_delivery', 'after_revision_delivery'):
        wait = subprocess.Popen.wait
        def wait_then_drop_response(process, *args, **kwargs):
            code = wait(process, *args, **kwargs)
            cmd = process.args
            if code == 0 and isinstance(cmd, list) and 'pcb_mcp_client.run_plan' in cmd:
                run = owned_run(cmd)
                if run is not None:
                    result = json.loads((run/'run.json').read_bytes())
                    revision = fault == 'after_revision_delivery'
                    if result['status'] == 'cad_checks_passed' and bool(result.get('parent')) == revision:
                        marker = 'revision-run-complete.json' if revision else 'first-run-complete.json'
                        (root/marker).write_bytes((run/'run.json').read_bytes())
                        os._exit(73)
            return code
        subprocess.Popen.wait = wait_then_drop_response
elif fault == 'before_project_create' and '--resume' not in sys.argv and owned_run(sys.argv):
    from pcb_mcp_client.journal import Journal, save
    begin = Journal.begin
    def pause_before_create(journal, tool, arguments):
        entry, cached = begin(journal, tool, arguments)
        if tool == 'project_create' and not cached:
            owner = int(os.environ['PCB_E2E_JOB_OWNER'])
            if owner != os.getppid():
                raise AssertionError('Test runner is not owned by its isolated MCP service')
            save(root/'create-intent.json', dict(journal=str(journal.path), entry=entry))
            os.kill(owner, signal.SIGKILL)
            raise asyncio.CancelledError('test: project creation intent recorded before the request')
        return entry, cached
    Journal.begin = pause_before_create
elif fault == 'fork_state_without_ledger' and '--pcb-run-marker' in sys.argv:
    from pcb_mcp import editing
    from pcb_mcp_client.journal import save
    write_json = editing.write_json
    def pause_before_fork_ledger(path, value):
        if path == root/'data/fork_requests.json':
            receipt, = value.values()
            child = root/'data'/receipt['result']['project_id']
            if not (child/'state.json').is_file():
                raise AssertionError('The fork state was not published before the receipt')
            save(root/'fork-before-ledger.json', receipt)
            os.kill(int(os.environ['PCB_E2E_JOB_OWNER']), signal.SIGKILL)
            os._exit(74)
        return write_json(path, value)
    editing.write_json = pause_before_fork_ledger
elif fault in ('after_revision_commit', 'after_group_check', 'after_group_commit', 'after_route', 'after_review_export', 'project_create', 'project_fork') and '--resume' not in sys.argv and owned_run(sys.argv):
    from pcb_mcp_client.journal import Journal, save
    finish = Journal.finish
    target = {'after_revision_commit': 'board_constraints_set', 'after_group_check': 'functional_check',
              'after_group_commit': 'component_part_replace', 'after_route': 'pcb_autoroute',
              'after_review_export': 'pcb_review_export'}.get(fault, fault)
    marker = 'revision-commit.json' if fault == 'after_revision_commit' else fault+'.json'
    def pause_after_commit(journal, entry, response):
        if entry['tool'] != target or response.get('isError'):
            return finish(journal, entry, response)
        if fault.startswith('after_group_') and not entry['arguments'].get('replacements'):
            return finish(journal, entry, response)
        owner = int(os.environ['PCB_E2E_JOB_OWNER'])
        if owner != os.getppid():
            raise AssertionError('Test runner is not owned by its isolated MCP service')
        save(root/marker, dict(journal=str(journal.path), entry=entry,
            response=response, runner_pid=os.getpid(), host_pid=owner))
        os.kill(owner, signal.SIGKILL)
        deadline = time.monotonic() + 45
        while not (root/(fault+'-release')).is_file() and time.monotonic() < deadline:
            time.sleep(.05)
        raise asyncio.CancelledError('test: committed '+target+' before journal acknowledgement')
    Journal.finish = pause_after_commit
"""


def read(path):
    return json.loads(path.read_bytes())


async def wait_for_runner_exit(lock_path):
    # The lock is inherited by the runner, including after the host is killed.
    async with asyncio.timeout(20):
        while True:
            with lock_path.open('a') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    return
            await asyncio.sleep(.1)


async def main(out, seed, runtime, creation_boundaries=False, group_boundary=None, expect_group_check_failure=False,
               tamper_group_evidence=False, route_boundary=None, revision_delivery_boundary=False,
               review_export_boundary=None, fork_commit_boundary=None):
    out.mkdir(parents=True, exist_ok=False)
    hook = out/'hook'
    hook.mkdir()
    (hook/'sitecustomize.py').write_text(FAULT)
    events = []
    first_create = next(row['request'] for row in seed if row['tool'] == 'design_job_create')
    first_plan = next(row['request'] for row in seed if row['tool'] == 'design_job_attempt')

    @asynccontextmanager
    async def client(name, fault=None):
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        if fault:
            env.update(PYTHONPATH=str(hook), PCB_E2E_JOB_FAULT=fault, PCB_E2E_JOB_ROOT=str(out))
        server = StdioServerParameters(command=sys.executable, args=['-m', 'pcb_mcp_client.cli',
            '--config', str(runtime), '--data-dir', str(out/'data'), 'serve'], cwd='/private/tmp', env=env)
        with (out/(name+'.stderr')).open('w') as log:
            async with stdio_client(server, errlog=log) as (rx, tx), ClientSession(rx, tx) as session:
                await session.initialize()
                yield session

    async def call(session, tool, request=None, reject=None):
        response = await session.call_tool(tool, {'request': request} if request is not None else {},
                                           read_timeout_seconds=timedelta(seconds=340))
        events.append(dict(tool=tool, request=request, response=response.model_dump(mode='json')))
        save(out/'transcript.json', events)
        if reject:
            assert response.isError and reject in str(response), str(response)
            print(tool, reject, flush=True)
            return None
        assert not response.isError, str(response)
        result = response.structuredContent['data']
        print(tool, result.get('status', 'ok'), flush=True)
        return result

    async def expect_connection_loss(name, fault, submit, marker):
        try:
            async with client(name, fault) as session:
                await call(session, 'design_job_attempt', submit)
        except Exception as error:
            assert (out/marker).is_file(), str(error)
            (out/(name+'-disconnect.txt')).write_text(repr(error)+'\n')
            print('connection lost', fault, flush=True)
        else:
            raise AssertionError('Fault did not interrupt the MCP connection')

    async with client('setup') as session:
        assert (await call(session, 'system_capabilities'))['contract_version'] == __version__
        assert not (await call(session, 'project_list'))['projects']
        first = await call(session, 'design_job_create', dict(first_create, max_attempts=1))
    submit = dict(first_plan, job_id=first['job_id'],
                  plan=dict(first_plan['plan'], requirement_sha256=first['requirement_sha256']))
    if route_boundary:
        await expect_connection_loss('route-fault','after_route',submit,'after_route.json')
        committed=read(out/'after_route.json')
        journal_path=Path(committed['journal'])
        request=committed['entry']['arguments']
        result=committed['response']['structuredContent']['data']
        project=out/'data'/request['project_id']
        route=project/'pcb_builds'/result['pcb_build_id']/'routed.kicad_pcb'
        original=route.read_bytes()
        assert 'response' not in read(journal_path)['calls'][-1]
        try:
            async with client('while-router-runner-active') as session:
                await call(session,'design_job_attempt',submit,reject='DESIGN_JOB_ATTEMPT_RUNNING')
        finally:
            (out/'after_route-release').touch()
        await wait_for_runner_exit(journal_path.parents[2]/'.attempt.lock')
        assert read(journal_path.parent/'run.json')['status']=='interrupted'
        process_log=journal_path.parents[1]/'process.log'
        async with client('route-reconnect') as session:
            await call(session,'design_job_attempt',dict(submit,idempotency_key='another-route-attempt'),
                       reject='DESIGN_JOB_RESUME_EXISTING_ATTEMPT')
            if route_boundary=='after':
                try:
                    route.write_bytes(original+b'\n')
                    refused=await call(session,'design_job_attempt',submit)
                    assert refused['status']=='interrupted'
                    assert 'UNCERTAIN_NON_IDEMPOTENT_CALL: pcb_autoroute' in process_log.read_text()
                    assert 'response' not in read(journal_path)['calls'][-1]
                    save(out/'altered-route-rejected.json',refused)
                finally:
                    route.write_bytes(original)
            recovered=await call(session,'design_job_attempt',submit)
            if route_boundary=='before':
                assert recovered['status']=='interrupted'
                assert 'UNCERTAIN_NON_IDEMPOTENT_CALL: pcb_autoroute' in process_log.read_text()
            else:
                assert recovered['status']=='passed' and recovered==await call(session,'design_job_attempt',submit)
                assert len((await call(session,'project_list'))['projects'])==1
                row,=[row for row in read(journal_path)['calls'] if row['tool']=='pcb_autoroute']
                assert row['recovered_from']=='pcb_route_receipt'
                assert row['response']['structuredContent']==committed['response']['structuredContent']
                assert recovered['result']['pcb_build_id']==result['pcb_build_id']
            assert route.read_bytes()==original
        save(out/'verification.json',dict(version=__version__,route_response_lost=True,
             route_board_unchanged=True,recovered=recovered,formal_tasks_passed=0))
        return
    if fork_commit_boundary=='after':
        first_fault, first_marker = 'before_project_create', 'create-intent.json'
    elif creation_boundaries:
        first_fault, first_marker = 'project_create', 'project_create.json'
    else:
        first_fault, first_marker = 'after_first_delivery', 'first-run-complete.json'
    await expect_connection_loss('first-fault', first_fault, submit, first_marker)
    first_folder = out/'data/design_jobs'/first['job_id']
    process_log = first_folder/'attempt-001/process.log'
    if creation_boundaries:
        created = read(out/first_marker)
        first_journal_path = Path(created['journal'])
        assert read(first_journal_path)['calls'][-1] == created['entry']
        (out/(first_fault+'-release')).touch()
        await wait_for_runner_exit(first_folder/'.attempt.lock')
    elif first_fault=='before_project_create':
        created = read(out/first_marker)
        first_journal_path = Path(created['journal'])
        await wait_for_runner_exit(first_folder/'.attempt.lock')
        assert read(first_journal_path)['calls'][-1] == created['entry']
    before_recovery = process_log.read_bytes()
    async with client('first-reconnect') as session:
        saved = await call(session, 'design_job_read', dict(job_id=first['job_id']))
        assert saved['status'] == 'running' and len(saved['attempts']) == 1
        if first_fault=='before_project_create':
            assert not (await call(session, 'project_list'))['projects']
        published = await call(session, 'design_job_attempt', submit)
        assert published['status'] == 'passed'
        base = published['delivery']
        if creation_boundaries:
            initial = created['response']['structuredContent']['data']
            assert base['project_id'] == initial['project_id'] and initial['revision'] == 0
            row, = [item for item in read(first_journal_path)['calls'] if item['tool'] == first_fault]
            assert row['arguments'] == created['entry']['arguments']
            assert row['response']['structuredContent'] == created['response']['structuredContent']
            assert len(published['recovery_snapshots']) == 1
        elif first_fault=='before_project_create':
            row,=[item for item in read(first_journal_path)['calls'] if item['tool']=='project_create']
            assert row['arguments']==created['entry']['arguments'] and 'response' in row
            assert published['number']==1 and len(published['recovery_snapshots'])==1
        else:
            assert published['result'] == read(out/first_marker)
            assert process_log.read_bytes() == before_recovery
        assert len((await call(session, 'project_list'))['projects']) == 1
        revision_request = dict(name='Move the delivered RC output connector right',
            requirement_json=json.dumps(dict(natural_language='Move J2 right by 2 mm and keep the original circuit and acceptance.')))
        if group_boundary:
            revision_request = next(row['request'] for row in seed
                                    if row['tool']=='design_job_create' and row['request'].get('base_job'))
        revision = await call(session, 'design_job_create', dict(revision_request,
            base_job=dict(job_id=first['job_id'], attempt=1), max_attempts=1))

    parent_path = out/'data'/base['project_id']
    parent_snapshot = snapshot(parent_path)
    position = base['placements']['J2']
    moved = dict(position, x_mm=position['x_mm']+2, rotation_deg=int(position['rotation_deg']))
    plan = dict(mode='mechanical_revision', name='Move output connector J2',
        base_sha256=base['base_sha256'], requirement_sha256=revision['requirement_sha256'],
        placements=[dict(moved, ref='J2', reason='Apply the requested two millimeter shift.')])
    change = dict(job_id=revision['job_id'], plan=plan,
                  rationale='Resume one real placement edit under its original acceptance.',
                  idempotency_key='durable-revision-attempt')
    if fork_commit_boundary:
        await expect_connection_loss('fork-ledger-fault','fork_state_without_ledger',change,'fork-before-ledger.json')
        folder=out/'data/design_jobs'/revision['job_id']
        await wait_for_runner_exit(folder/'.attempt.lock')
        child=read(out/'fork-before-ledger.json')['result']['project_id']
        state=out/'data'/child/'state.json'
        original=state.read_bytes()
        fork_ledger=out/'data/fork_requests.json'
        journal=folder/'attempt-001/run/journal.json'
        assert not fork_ledger.exists() and read(journal)['calls'][-1]['tool']=='project_fork'
        assert 'response' not in read(journal)['calls'][-1]
        async with client('fork-ledger-reconnect') as session:
            if fork_commit_boundary=='after':
                try:
                    save(state,dict(read(state),name='Externally changed partial fork'))
                    refused=await call(session,'design_job_attempt',change)
                    assert refused['status']=='interrupted'
                    assert not fork_ledger.exists() and 'response' not in read(journal)['calls'][-1]
                    save(out/'changed-fork-rejected.json',refused)
                finally:
                    state.write_bytes(original)
            recovered=await call(session,'design_job_attempt',change)
            if fork_commit_boundary=='before':
                assert recovered['status']=='interrupted' and state.read_bytes()==original
                assert not fork_ledger.exists()
            else:
                assert recovered['status']=='passed' and recovered['number']==1
                assert recovered['delivery']['project_id']==child
                assert recovered['delivery']['placements']==dict(base['placements'],J2=moved)
                assert recovered['delivery']['contract_sha256']==base['contract_sha256']
                assert list(read(fork_ledger).values())==[read(out/'fork-before-ledger.json')]
                assert recovered==await call(session,'design_job_attempt',change)
            assert {row['project_id'] for row in (await call(session,'project_list'))['projects']}=={base['project_id'],child}
        assert snapshot(parent_path)==parent_snapshot
        save(out/'verification.json',dict(version=__version__,first=published,recovered=recovered,
            first_fault=first_fault,fork_project_id=child,parent_files_unchanged=len(parent_snapshot),
            formal_tasks_passed=0))
        return
    if review_export_boundary:
        fault = 'after_review_export'
        marker = fault + '.json'
        await expect_connection_loss('review-fault', fault, change, marker)
        committed = read(out/marker)
        journal_path = Path(committed['journal'])
        result = committed['response']['structuredContent']['data']
        bundle = Path(result['bundle_path'])
        original = bundle.read_bytes()
        checksum = hashlib.sha256(original).hexdigest()
        fork_ledger = (out/'data/fork_requests.json').read_bytes()
        assert 'response' not in read(journal_path)['calls'][-1]
        (out/(fault+'-release')).touch()
        await wait_for_runner_exit(journal_path.parents[2]/'.attempt.lock')
        assert read(journal_path.parent/'run.json')['status'] == 'interrupted'
        process_log = journal_path.parents[1]/'process.log'
        async with client('review-reconnect') as session:
            if review_export_boundary == 'after':
                try:
                    bundle.write_bytes(original + b'\n')
                    refused = await call(session, 'design_job_attempt', change)
                    assert refused['status'] == 'interrupted'
                    assert 'RESUME_PCB_REVIEW_EVIDENCE_CHANGED' in process_log.read_text()
                    assert 'response' not in read(journal_path)['calls'][-1]
                    save(out/'altered-review-rejected.json', refused)
                finally:
                    bundle.write_bytes(original)
            mtime = bundle.stat().st_mtime_ns
            recovered = await call(session, 'design_job_attempt', change)
            if review_export_boundary == 'before':
                assert recovered['status'] == 'interrupted'
                assert 'RESUME_PROJECT_CHANGED' in process_log.read_text()
            else:
                assert recovered['status'] == 'passed' and recovered['number'] == 1
                assert recovered == await call(session, 'design_job_attempt', change)
                assert len((await call(session, 'project_list'))['projects']) == 2
                row, = [row for row in read(journal_path)['calls'] if row['tool'] == 'pcb_review_export']
                assert row['recovered_from'] == 'pcb_review_receipt'
                assert row['response']['structuredContent'] == committed['response']['structuredContent']
                assert recovered['delivery']['bundle_sha256'] == checksum
                assert recovered['delivery']['bundle_path'] == str(bundle)
                assert recovered['delivery']['contract_sha256'] == base['contract_sha256']
                assert recovered['delivery']['placements'] == dict(base['placements'], J2=moved)
            assert bundle.stat().st_mtime_ns == mtime and bundle.read_bytes() == original
        assert (out/'data/fork_requests.json').read_bytes() == fork_ledger
        assert snapshot(parent_path) == parent_snapshot
        save(out/'verification.json', dict(version=__version__, first=published, recovered=recovered,
             review_response_lost=True, review_bundle_sha256=checksum,
             bundle_unchanged=True, parent_files_unchanged=len(parent_snapshot), formal_tasks_passed=0))
        return
    if revision_delivery_boundary:
        await expect_connection_loss('revision-delivery-fault', 'after_revision_delivery', change,
                                     'revision-run-complete.json')
        folder = out/'data/design_jobs'/revision['job_id']
        process_log = folder/'attempt-001/process.log'
        original_log = process_log.read_bytes()
        run = folder/'attempt-001/run'
        completed = read(out/'revision-run-complete.json')
        summary = run/'revision-summary.md'
        contents = summary.read_bytes()
        checksum = hashlib.sha256(contents).hexdigest()
        assert checksum == completed['revision_summary_sha256']
        before = f"({position['x_mm']:g}, {position['y_mm']:g}) mm"
        after = f"({moved['x_mm']:g}, {moved['y_mm']:g}) mm"
        assert f'J2 · {before}' in contents.decode() and after in contents.decode()
        fork_ledger = (out/'data/fork_requests.json').read_bytes()
        async with client('revision-delivery-reconnect') as session:
            pending = await call(session, 'design_job_read', dict(job_id=revision['job_id']))
            assert pending['status'] == 'running' and len(pending['attempts']) == 1
            assert 'delivery' not in pending['attempts'][0]
            recovered = await call(session, 'design_job_attempt', change)
            assert recovered['status'] == 'passed' and recovered['result'] == completed
            assert recovered['revision_summary'] == dict(path=str(summary), sha256=checksum)
            assert recovered['delivery']['file_hashes'][summary.name] == checksum
            assert recovered == await call(session, 'design_job_attempt', change)
            assert len((await call(session, 'project_list'))['projects']) == 2
        assert process_log.read_bytes() == original_log and summary.read_bytes() == contents
        assert (out/'data/fork_requests.json').read_bytes() == fork_ledger
        assert snapshot(parent_path) == parent_snapshot
        save(out/'verification.json', dict(version=__version__, first=published, recovered=recovered,
             revision_delivery_response_lost=True, revision_recovered_without_rerun=True,
             summary_unchanged=True, parent_files_unchanged=len(parent_snapshot), formal_tasks_passed=0))
        return
    revision_fault = 'project_fork' if creation_boundaries else 'after_revision_commit'
    revision_marker = 'project_fork.json' if creation_boundaries else 'revision-commit.json'
    if group_boundary:
        group = next(row['request'] for row in seed if row['tool']=='design_job_attempt'
                     and row['request']['idempotency_key']=='group-both')
        change = dict(group, job_id=revision['job_id'], plan=dict(group['plan'],
                      base_sha256=base['base_sha256'], requirement_sha256=revision['requirement_sha256']))
        revision_fault = 'after_group_'+group_boundary
        revision_marker = revision_fault+'.json'
    await expect_connection_loss('revision-fault', revision_fault, change, revision_marker)
    committed = read(out/revision_marker)
    payload = committed['response']['structuredContent']['data']
    child_path = out/'data'/payload['project_id']
    child_before = read(child_path/'state.json')
    key = committed['entry']['arguments'].get('idempotency_key')
    if creation_boundaries:
        assert child_before['revision'] == payload['revision'] == 0
        assert child_before['idempotency'] == {} and '_creation_request' not in child_before
    elif group_boundary=='check':
        assert child_before['revision']==payload['revision']==0 and payload['verdict']=='pass'
        assert payload['applies_to_current_spec'] is False and len(payload['replacements'])==2
    else:
        assert child_before['revision'] == child_before['idempotency'][key]['result']['revision'] == 1
    save(out/'committed-child-before-recovery.json', child_before)
    fork_ledger = (out/'data/fork_requests.json').read_bytes()
    journal_path = Path(committed['journal'])
    assert 'response' not in read(journal_path)['calls'][-1]
    try:
        async with client('while-runner-active') as session:
            await call(session, 'design_job_attempt', change, reject='DESIGN_JOB_ATTEMPT_RUNNING')
    finally:
        (out/(revision_fault+'-release')).touch()
    await wait_for_runner_exit(journal_path.parents[2]/'.attempt.lock')
    interrupted = read(journal_path.parent/'run.json')
    assert interrupted['status'] == 'interrupted'
    async with client('revision-reconnect') as session:
        await call(session, 'design_job_attempt', dict(change, idempotency_key='new-accidental-branch'),
                   reject='DESIGN_JOB_RESUME_EXISTING_ATTEMPT')
        if tamper_group_evidence:
            request_file=Path(payload['artifact_directory'])/'request.json'
            original_request=request_file.read_bytes()
            try:
                request_file.write_bytes(original_request+b'\n')
                refused=await call(session, 'design_job_attempt', change)
                assert refused['status']=='interrupted',refused
                assert 'RESUME_FUNCTIONAL_EVIDENCE_CHANGED' in (journal_path.parents[1]/'process.log').read_text()
                assert read(child_path/'state.json')==child_before
                assert 'response' not in read(journal_path)['calls'][-1]
                save(out/'altered-functional-evidence-rejected.json',refused)
            finally:
                request_file.write_bytes(original_request)
        recovered = await call(session, 'design_job_attempt', change)
        if expect_group_check_failure:
            assert recovered['status']=='interrupted',recovered
            process_log=journal_path.parents[1]/'process.log'
            assert 'RESUME_PROJECT_CHANGED' in process_log.read_text()
            assert read(child_path/'state.json')==child_before
            assert snapshot(parent_path)==parent_snapshot
            save(out/'verification.json',dict(version=__version__, recovered=recovered,
                 interrupted_scope='functional_check', failure='RESUME_PROJECT_CHANGED'))
            return
        assert recovered['status'] == 'passed' and recovered['number'] == 1
        assert recovered['result']['project_id'] == payload['project_id']
        assert recovered['result']['revision'] == 2
        assert recovered['delivery']['contract_sha256'] == base['contract_sha256']
        expected_placements=base['placements'] if group_boundary else dict(base['placements'], J2=moved)
        assert recovered['delivery']['placements'] == expected_placements
        assert recovered == await call(session, 'design_job_attempt', change)
        assert len((await call(session, 'project_list'))['projects']) == 2
        detail = await call(session, 'design_job_read', dict(job_id=revision['job_id']))
        assert len(detail['attempts']) == 1
        assert len(recovered['recovery_snapshots']) == (2 if tamper_group_evidence else 1)
    assert (out/'data/fork_requests.json').read_bytes() == fork_ledger
    assert snapshot(parent_path) == parent_snapshot
    if not creation_boundaries and key:
        assert read(child_path/'state.json')['idempotency'][key] == child_before['idempotency'][key]
    journal = read(journal_path)
    row, = [item for item in journal['calls'] if item['tool'] == committed['entry']['tool']
            and (group_boundary!='check' or item['arguments'].get('replacements'))]
    assert row['arguments'] == committed['entry']['arguments']
    assert row['response']['structuredContent'] == committed['response']['structuredContent']
    if group_boundary=='check':
        assert row['recovered_from']=='functional_index'
        index=read(child_path/'functional_index.json')
        assert len(index)==2 and index[0]['run_id']==payload['run_id']
    retained = Path(recovered['recovery_snapshots'][0])
    assert 'response' not in read(retained/'journal.json')['calls'][-1]
    if group_boundary:
        replacement,=[item for item in journal['calls'] if item['tool']=='component_part_replace']
        assert len(replacement['response']['structuredContent']['data']['replacements'])==2
    save(out/'verification.json', dict(version=__version__, first_fault=first_fault, revision_fault=revision_fault,
        first_recovered_without_rerun=not creation_boundaries, first_resumed_on_same_project=True,
        competing_request_rejected_after_host_loss=True, second_idempotency_key_rejected=True,
        revision_resumed_on_same_branch=True, committed_edit_count=1, project_count=2,
        parent_files_unchanged=len(parent_snapshot), first=published, recovered=recovered,
        formal_tasks_passed=0, independent_user_trial=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-transcript', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--creation-boundaries', action='store_true')
    parser.add_argument('--group-boundary', choices=('check','commit'))
    parser.add_argument('--expect-group-check-failure', action='store_true')
    parser.add_argument('--tamper-group-evidence', action='store_true')
    parser.add_argument('--route-boundary', choices=('before','after'))
    parser.add_argument('--revision-delivery-boundary', action='store_true')
    parser.add_argument('--review-export-boundary', choices=('before','after'))
    parser.add_argument('--fork-commit-boundary', choices=('before','after'))
    args = parser.parse_args()
    asyncio.run(main(args.output.resolve(), read(args.seed_transcript), args.runtime.resolve(),
                     args.creation_boundaries, args.group_boundary, args.expect_group_check_failure,
                     args.tamper_group_evidence,args.route_boundary,args.revision_delivery_boundary,
                     args.review_export_boundary,args.fork_commit_boundary))
