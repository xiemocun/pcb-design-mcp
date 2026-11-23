"""Read existing review exports and functional evidence attributable to a job attempt."""
import hashlib
import json
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from .common import PCB_REVIEW_RECEIPT, fork_project_id, request_fingerprint
from .editing import SPEC_KEYS, spec_hash


ID = re.compile('[a-f0-9]{32}')
REVIEW_FILES = {'spec.json', 'bom.csv', 'positions.csv', 'portable/design.kicad_pro',
                'portable/design.kicad_pcb', 'portable/design.kicad_sch'}


def stored_json(path, default=None):
    if path.resolve() == path:
        try:
            return json.loads(path.read_bytes())
        except (ValueError, OSError):
            pass
    return default


def failure_reasons(findings):
    descriptions = {
        'NGSPICE_TIMEOUT': '电路仿真超时，缺少这些工况的测量结果。',
        'NGSPICE_TRANSIENT_TIMESTEP_TOO_SMALL': '电路仿真提前中断，缺少这些工况的测量结果。',
        'SPICE_MODEL_TEMPERATURE_OUTSIDE_SCOPE': '元件模型缺少指定温度下的数据，无法验证整板温控。',
        'FUNCTIONAL_REQUIREMENT_FAILED': '已有测量值超出规定范围。',
    }
    groups = {}
    for finding in findings:
        if finding.get('severity') != 'error':
            continue
        code = finding['code']
        group = groups.setdefault(code, dict(code=code, scenarios=[], requirements=[],
                    message=descriptions.get(code) or finding.get('message') or finding.get('next_action') or code))
        for field, target in [('scenario', 'scenarios'), ('requirement', 'requirements')]:
            value = finding.get(field)
            if value and value not in group[target]:
                group[target].append(value)
    return list(groups.values())


def functional_evidence(project, revision, specification, contract, attempt_run=None):
    latest = original = None
    entries = stored_json(project / 'functional_index.json', [])
    for entry in reversed(entries if isinstance(entries, list) else []):
        run_id = entry.get('run_id') if isinstance(entry, dict) else None
        if not isinstance(run_id, str) or not ID.fullmatch(run_id) or entry.get('scope') == 'replacement':
            continue
        if latest and run_id != attempt_run:
            continue
        path = project / 'functional_runs' / run_id / 'report.json'
        if path.resolve() != path:
            continue
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != entry.get('report_sha256'):
                continue
            report = json.loads(data)
            if (report['project_id'], report['revision'], report['spec_sha256'],
                    report['contract_sha256'], report['run_id']) != (
                    project.name, revision, specification, contract, run_id):
                continue
            if report.get('replacement') or report.get('replacements'):
                continue
            summary = dict(run_id=run_id, report_path=str(path), report_sha256=digest,
                           verdict=report['verdict'], required=report['coverage']['required'],
                           executed=report['coverage']['executed'],
                           passed=sum(m.get('passed') is True for m in report['measurements']),
                           failure_reasons=failure_reasons(report['findings']))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if latest is None:
            latest = summary
        if run_id == attempt_run:
            original = summary
        if original or not attempt_run:
            break
    return original, latest


def layout_fork(source, draft):
    # A change of placement or copper policy can yield a reviewable layout of the
    # same circuit. A change of parts, connections or board requirements cannot.
    if any(source.get(k) != draft.get(k) for k in SPEC_KEYS
           if k not in ('board_constraints', 'pcb_copper_policy')):
        return False
    def board(state):
        return {k: v for k, v in (state.get('board_constraints') or {}).items() if k != 'placements'}
    return board(source) == board(draft)


def review_export(receipt_path, project, source, provenance):
    if receipt_path.resolve() != receipt_path:
        return None
    folder = receipt_path.parent
    bundle = folder / 'pcb_review_bundle.zip'
    if not ID.fullmatch(folder.name) or bundle.resolve() != bundle:
        return None
    request = dict(project_id=project.name, pcb_build_id=folder.name)
    receipt = stored_json(receipt_path, {})
    if not isinstance(receipt, dict) or receipt.get('request_sha256') != request_fingerprint(request):
        return None
    try:
        with bundle.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != receipt.get('bundle_sha256'):
                return None
            stream.seek(0)
            with ZipFile(stream) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                if not REVIEW_FILES.issubset(archive.namelist()):
                    return None
                draft = json.loads(archive.read('spec.json'))
        if (manifest['pcb_build_id'] != folder.name or manifest['revision'] != draft['revision']
                or draft['id'] != project.name or manifest['portable_project']['passed'] is not True
                or manifest['assembly']['status'] != 'complete_source_linked_tables'):
            return None
        if provenance is None:
            if draft['revision'] != source['revision'] or spec_hash(draft) != spec_hash(source):
                return None
        elif draft.get('provenance') != provenance or not layout_fork(source, draft):
            return None
        return dict(project_id=project.name, revision=draft['revision'], spec_sha256=spec_hash(draft),
                    pcb_build_id=folder.name, bundle_path=str(bundle), bundle_sha256=digest,
                    open_after_extract='portable/design.kicad_pro', bom='bom.csv', positions='positions.csv',
                    component_count=manifest['assembly']['bom_components'],
                    drc_errors=manifest['portable_project']['drc_errors'], qualification='review_only')
    except (OSError, ValueError, TypeError, KeyError, BadZipFile):
        return None


def job_outcome(root, folder, job):
    root, folder = root.resolve(), folder.resolve()
    drafts = []
    result = dict(review_drafts=drafts, current_attempt=None)
    ledger = stored_json(root / 'fork_requests.json', {})
    for attempt in reversed(job['attempts']):
        number = attempt['number']
        is_current = number == job['attempts'][-1]['number']
        if is_current:
            current = result['current_attempt'] = dict(number=number, status=attempt['status'],
                    failure_reasons=failure_reasons(attempt.get('diagnosis', {}).get('findings', [])))
        record = attempt.get('result') or {}
        run = folder / f'attempt-{number:03d}' / 'run'
        source = stored_json(run / 'state.json')
        if not isinstance(source, dict):
            continue
        pid = source.get('id')
        if (not isinstance(pid, str) or not ID.fullmatch(pid) or pid != record.get('project_id')
                or source.get('revision') != record.get('revision')):
            continue
        specification = spec_hash(source)
        if is_current:
            checks = stored_json(run / 'checks.json', {})
            check = checks.get('functional_check') if isinstance(checks, dict) else None
            run_id = check.get('run_id') if isinstance(check, dict) else None
            original, latest = functional_evidence(root / pid, source['revision'], specification,
                                                  job['contract_sha256'], run_id)
            current.update(project_id=pid, revision=source['revision'], spec_sha256=specification,
                           attempt_functional_check=original, latest_functional_check=latest)
        if attempt['status'] != 'failed':
            continue
        projects = [(pid, None)]
        parent = dict(parent_project_id=pid, parent_revision=source['revision'],
                      parent_spec_sha256=specification, approvals_inherited=False)
        for key, entry in (ledger.items() if isinstance(ledger, dict) else ()):
            origin, _, token = key.partition(':')
            fork = entry.get('result', {}) if isinstance(entry, dict) else {}
            if (isinstance(fork, dict) and origin == pid and fork.get('provenance') == parent
                    and fork.get('project_id') == fork_project_id(pid, token)):
                projects.append((fork['project_id'], parent))
        for project_id, provenance in projects:
            project = root / project_id
            for receipt in sorted((project / 'pcb_builds').glob('*/' + PCB_REVIEW_RECEIPT)):
                draft = review_export(receipt, project, source, provenance)
                if draft:
                    _, latest = functional_evidence(project, draft['revision'], draft['spec_sha256'],
                                                    job['contract_sha256'])
                    drafts.append(dict(source_attempt=number, source_project_id=pid,
                                       latest_functional_check=latest, **draft))
    return result
