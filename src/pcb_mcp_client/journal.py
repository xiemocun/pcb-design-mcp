"""Durable local RPC journal. Recover only from verifiable committed results."""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path

from pcb_mcp.common import DomainError, PCB_REVIEW_RECEIPT, create_project_id, file_snapshot as snapshot, request_fingerprint
from pcb_mcp.models import Create, PCBBuilt, PCBExport, PCBRef, PCBRoute, ProjectFork, Reply


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def replayable_creation(root, pending):
    tool = pending['tool']
    try:
        if tool == 'project_create':
            request = Create.model_validate(pending['arguments'])
            if request.idempotency_key is None:
                return False
            pid = create_project_id(request.idempotency_key)
            folder = root / pid
            if not folder.exists() and not folder.is_symlink():
                return True
            if folder.is_symlink():
                return False
            state = json.loads((folder / 'state.json').read_bytes())
            receipt = state.pop('_creation_request')
        else:
            request = ProjectFork.model_validate(pending['arguments'])
            ledger_file = root/'fork_requests.json'
            if ledger_file.is_symlink():
                return False
            ledger = json.loads(ledger_file.read_bytes()) if ledger_file.exists() else {}
            if not isinstance(ledger, dict):
                return False
            key = request.project_id + ':' + request.idempotency_key
            if key not in ledger:
                from pcb_mcp.editing import initial_fork_state, untouched_initial_fork
                from pcb_mcp.service import Service
                parent = Service(root).load(request.project_id)
                if parent['id'] != request.project_id or parent['revision'] != request.expected_revision:
                    return False
                state = initial_fork_state(parent, request)
                folder = root/state['id']
                return ((not folder.exists() and not folder.is_symlink())
                        or untouched_initial_fork(folder,state))
            receipt = ledger[key]
            pid = receipt['result']['project_id']
            if not re.fullmatch('[a-f0-9]{32}', pid) or pid == request.project_id:
                return False
            folder = root / pid
            if folder.is_symlink():
                return False
            state = json.loads((folder / 'state.json').read_bytes())
        expected = dict(project_id=pid, revision=0, profile=state['profile'])
        if tool == 'project_fork':
            expected['provenance'] = state['provenance']
        return (receipt['fingerprint'] == request_fingerprint(request.model_dump())
                and receipt['result'] == expected
                and state['id'] == pid and state['revision'] == 0
                and receipt['state_sha256'] == request_fingerprint(state)
                and set(snapshot(folder)) == {'state.json'})
    except (DomainError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def committed_functional_result(folder, pending, previous, current, backend_sha256):
    from pcb_mcp.editing import spec_hash
    from pcb_mcp.functional import FunctionalCheck
    from pcb_mcp.model_power import read_report, runtime_matches

    index_name = 'functional_index.json'
    try:
        entries = json.loads((folder/index_name).read_bytes())
        entry = entries[-1]
        earlier = entries[:-1]
        index_hash = hashlib.sha256(json.dumps(earlier, ensure_ascii=False, indent=2).encode()).hexdigest()
        if (previous[index_name] != index_hash if index_name in previous else bool(earlier)):
            return None
        if any(current.get(name) != sha for name, sha in previous.items() if name != index_name):
            return None
        report = read_report(folder, entry)
        run_id = entry['run_id']
        prefix = f'functional_runs/{run_id}/'
        changed = {name: sha for name, sha in current.items() if previous.get(name) != sha and name != index_name}
        artifacts = {prefix+name: sha for name, sha in entry['artifacts'].items()}
        if changed != artifacts or entry['artifacts'].get('report.json') != entry['report_sha256']:
            return None
        run = folder/'functional_runs'/run_id
        request = FunctionalCheck.model_validate(pending['arguments'])
        if request != FunctionalCheck.model_validate_json((run/'request.json').read_bytes()):
            return None
        state = json.loads((folder/'state.json').read_bytes())
        evaluated = json.loads((run/'state.json').read_bytes())
        if (request.project_id != folder.name or state['id'] != folder.name or evaluated['id'] != folder.name
                or state['revision'] != request.revision or evaluated['revision'] != request.revision
                or spec_hash(evaluated) != request.expected_spec_sha256):
            return None
        scopes = {key: request.model_dump(mode='json').get(key) for key in ('replacement', 'replacements')}
        proposed = any(value is not None for value in scopes.values())
        expected = dict(project_id=request.project_id, revision=request.revision,
                        contract_sha256=request.contract_sha256, spec_sha256=request.expected_spec_sha256,
                        run_id=run_id, artifact_directory=str(run), backend_sha256=backend_sha256,
                        applies_to_current_spec=not proposed, **scopes)
        if proposed:
            expected.update(base_spec_sha256=spec_hash(state), replacement_base_current=True)
        elif request.expected_spec_sha256 != spec_hash(state):
            return None
        if (not backend_sha256 or entry.get('scope') != ('replacement' if proposed else None)
                or any(report.get(name) != value for name, value in expected.items())
                or report['verdict'] not in ('pass', 'fail')
                or (report['verdict']=='pass' and not all(runtime_matches(row) for row in report['runs']))):
            return None
        return report
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None


def committed_route_result(folder, pending, previous, current):
    try:
        request = PCBRoute.model_validate(pending['arguments'])
        state = json.loads((folder/'state.json').read_bytes())
        build = state['pcb_builds'][request.pcb_build_id]
        receipt = build.pop('route_receipt')
        if (request.project_id != folder.name or state['id'] != folder.name
                or build['id'] != request.pcb_build_id or build['revision'] != state['revision']
                or receipt['request_sha256'] != request_fingerprint(request.model_dump(mode='json'))
                or receipt['before_state_sha256'] != previous['state.json']
                or receipt['routed_state_sha256'] != request_fingerprint(state)
                or previous.keys() - current.keys()):
            return None
        prefix = f'pcb_builds/{request.pcb_build_id}/'
        changed = {name: sha for name, sha in current.items() if previous.get(name)!=sha and name!='state.json'}
        artifacts = {prefix+name: sha for name, sha in receipt['artifacts'].items()}
        if changed != artifacts:
            return None
        if any(current.get(prefix+name) != sha for name, sha in build['sources'].items()):
            return None
        result = PCBBuilt.model_validate(receipt['result']).model_dump(mode='json')
        if (result['pcb_build_id'] != request.pcb_build_id or result['revision'] != state['revision']
                or result['board_hash'] != build['sources']['routed.kicad_pcb']
                or receipt['artifacts'].get('routed.kicad_pcb') != result['board_hash']
                or result['qualification'] != 'routed_not_drc_or_manufacturing_approved'):
            return None
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def committed_review_export(folder, pending, previous, current):
    try:
        request = PCBRef.model_validate(pending['arguments'])
        prefix = f'pcb_builds/{request.pcb_build_id}/'
        if {name: sha for name, sha in previous.items() if not name.startswith(prefix)} != {
            name: sha for name, sha in current.items() if not name.startswith(prefix)
        }:
            return None
        before = {name[len(prefix):]: sha for name, sha in previous.items() if name.startswith(prefix)}
        after = {name[len(prefix):]: sha for name, sha in current.items() if name.startswith(prefix)}
        receipt_sha = after.pop(PCB_REVIEW_RECEIPT)
        build = folder/prefix
        raw_receipt = (build/PCB_REVIEW_RECEIPT).read_bytes()
        receipt = json.loads(raw_receipt)
        state = json.loads((folder/'state.json').read_bytes())
        board = state['pcb_builds'][request.pcb_build_id]
        bundle = build/'pcb_review_bundle.zip'
        if (hashlib.sha256(raw_receipt).hexdigest() != receipt_sha
                or request.project_id != folder.name or state['id'] != folder.name
                or board['id'] != request.pcb_build_id or board['revision'] != state['revision']
                or receipt['request_sha256'] != request_fingerprint(request.model_dump(mode='json'))
                or receipt['project_state_sha256'] != current['state.json']
                or receipt['before_build_sha256'] != request_fingerprint(before)
                or receipt['exported_build_sha256'] != request_fingerprint(after)
                or receipt['bundle_sha256'] != after[bundle.name] or bundle.is_symlink()):
            return None
        raw_manifest = (build/'manifest.json').read_bytes()
        manifest = json.loads(raw_manifest)
        if (hashlib.sha256(raw_manifest).hexdigest() != after['manifest.json']
                or manifest['pcb_build_id'] != request.pcb_build_id or manifest['revision'] != state['revision']
                or any(after.get(name) != checksum for name, checksum in manifest['hashes'].items())):
            return None
        return PCBExport(pcb_build_id=request.pcb_build_id, revision=state['revision'],
                         bundle_path=str(bundle), manifest=manifest).model_dump(mode='json')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


class Journal:
    def __init__(self, path, binding, data_dir, resume=False):
        self.path, self.root, self.cursor = path, Path(data_dir), 0
        self.value = json.loads(path.read_text()) if resume else dict(binding=binding, calls=[], project_id=None, snapshot={})
        if self.value['binding'] != binding:
            raise ValueError('RESUME_INPUT_OR_RUNTIME_CHANGED')
        calls = self.value['calls']
        pending = calls[-1] if calls and 'response' not in calls[-1] else None
        pid = self.value['project_id']
        tool = pending['tool'] if pending else None
        if tool == 'pcb_autoroute' and not pid:
            raise ValueError('UNCERTAIN_NON_IDEMPOTENT_CALL: pcb_autoroute; inspect retained project data before starting a new run')
        if pid and not re.fullmatch('[a-f0-9]{32}', pid):
            raise ValueError('INVALID_JOURNAL_PROJECT_ID')
        creating = tool in ('project_create', 'project_fork')
        if resume and (creating or pid):
            with (self.root/'.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_SH)
                if creating and not replayable_creation(self.root,pending):
                    raise ValueError('UNCERTAIN_NON_IDEMPOTENT_CALL: ' + tool + '; inspect retained project data before starting a new run')
                if pid:
                    self.restore_project(self.root/pid, pending)
        self.flush()

    def restore_project(self, folder, pending):
        previous, current = self.value['snapshot'], snapshot(folder)
        if pending and pending['tool']=='pcb_autoroute':
            result = committed_route_result(folder, pending, previous, current)
            if result is None:
                raise ValueError('UNCERTAIN_NON_IDEMPOTENT_CALL: pcb_autoroute; inspect retained project data before starting a new run')
            self.restore_response(pending, result, current, 'pcb_route_receipt')
            return
        if current == previous:
            return
        if pending and pending['tool']=='pcb_review_export':
            result = committed_review_export(folder, pending, previous, current)
            if result is None:
                raise ValueError('RESUME_PCB_REVIEW_EVIDENCE_CHANGED')
            self.restore_response(pending, result, current, 'pcb_review_receipt')
            return
        if pending and pending['tool']=='functional_check':
            report = committed_functional_result(folder, pending, previous, current, self.value['binding'].get('server'))
            if report is None:
                raise ValueError('RESUME_FUNCTIONAL_EVIDENCE_CHANGED')
            self.restore_response(pending, report, current, 'functional_index')
            return
        # A lost response may have committed one atomic idempotent operation.
        request = pending.get('arguments', {}) if pending else {}
        key = request.get('idempotency_key')
        state = json.loads((folder/'state.json').read_text())
        saved = state.get('idempotency', {}).get(key)
        if not (saved and saved['result'].get('revision') == state['revision']
                and state['revision'] in (request.get('expected_revision'), request.get('expected_revision', -2) + 1)):
            raise ValueError('RESUME_PROJECT_CHANGED')
        if any(current.get(name) != value for name, value in previous.items() if name != 'state.json'):
            raise ValueError('RESUME_ARTIFACT_CHANGED')

    def restore_response(self, pending, data, current, source):
        reply = Reply[dict](project_id=data.get('project_id'), revision=data['revision'], data=data)
        pending['response'] = dict(isError=False, structuredContent=reply.model_dump(mode='json'),
                                   content=[], meta=None)
        pending['recovered_from'] = source
        self.value['snapshot'] = current

    @property
    def calls(self):
        return self.value['calls']

    def flush(self):
        save(self.path, self.value)

    def begin(self, tool, args):
        if self.cursor < len(self.calls):
            entry = self.calls[self.cursor]
            def stable(value):
                return {k: v for k, v in (value or {}).items() if k != 'idempotency_key'}
            if entry['tool'] != tool or stable(entry['arguments']) != stable(args):
                raise ValueError('RESUME_CALL_SEQUENCE_CHANGED')
            self.cursor += 1
            return entry, 'response' in entry
        entry = dict(tool=tool, arguments=args)
        self.calls.append(entry)
        self.cursor += 1
        self.flush()  # Intent reaches disk before sending an RPC.
        return entry, False

    def finish(self, entry, response):
        if entry['tool'] in ('project_create', 'project_fork') and not response.get('isError'):
            self.value['project_id'] = response['structuredContent']['data']['project_id']
        pid = self.value['project_id']
        if pid:
            self.value['snapshot'] = snapshot(self.root / pid)
        entry['response'] = response
        self.flush()
