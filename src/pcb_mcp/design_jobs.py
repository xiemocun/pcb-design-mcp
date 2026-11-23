"""Bounded, durable AI-driven candidate/diagnosis/retry orchestration.

The calling AI selects and revises arbitrary data plans. This service freezes
the acceptance contract, executes real tools and preserves each attempt. It
does not pretend that deterministic software understands arbitrary prose.
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import signal
import sys
import uuid
from typing import Annotated
from zipfile import BadZipFile
from pydantic import Field,model_validator
from .models import Strict,Id
from .functional import FunctionalContract, I2CPullups
from . import registry
from .design_contract import Interval, MEASUREMENT_DIMENSIONS
from pcb_mcp_client.plan import Plan
from pcb_mcp_client.revision_summary import SUMMARY_NAME
from pcb_mcp_client.extension import CircuitExtension, added_model_bindings
from pcb_mcp_client.revision import (MechanicalRevision, ComponentGroupRevision, ComponentRevision, ConnectivityRevision, CircuitRemoval, FunctionalRevision, MountingRevision, OutlineRevision, RevisionPlan, StackupRevision,
    baseline, check_base_state, check_board_revision_mode, check_i2c_pullup_plan, check_placement_references, contract_description_change,
    connectivity_changes, connectivity_revision, functional_additions, functional_limit_changes, i2c_pullup_changes, mounting_board, mounting_request, placement_requirements, revised_criteria, revised_functional_contract,
    outline_request, outline_target, removal_contract, removal_request, removal_snapshot, revised_requirement_items,
    revised_mounting, revised_stackup, stackup_request, stackup_target)
from .common import DomainError,require,write_json
from .job_outcome import job_outcome


DEFAULT_ATTEMPT_TIMEOUT_S = 300


class DeliveredAttempt(Strict):
    job_id:Id
    attempt:Annotated[int,Field(ge=1,le=20)]


class DesignJobCreate(Strict):
    name:Annotated[str,Field(min_length=3,max_length=200)]
    requirement_json:Annotated[str,Field(min_length=10,max_length=200000)]
    contract:FunctionalContract|None=None
    base_job:DeliveredAttempt|None=None
    max_attempts:Annotated[int,Field(ge=1,le=20)]=8
    attempt_timeout_s:Annotated[int,Field(ge=30,le=600)]=DEFAULT_ATTEMPT_TIMEOUT_S

    @model_validator(mode='after')
    def contract_or_base(self):
        if (self.contract is None)==(self.base_job is None):
            raise ValueError('DESIGN_JOB_REQUIRES_CONTRACT_OR_DELIVERED_BASE')
        return self


class DesignJobSearch(Strict):
    query: Annotated[str, Field(max_length=200)] = ''
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=50)] = 20


class DesignJobRef(Strict):
    job_id:Id


class DesignJobAttempt(DesignJobRef):
    plan:Plan|MechanicalRevision|ComponentRevision|ComponentGroupRevision|FunctionalRevision|CircuitExtension|ConnectivityRevision|CircuitRemoval|StackupRevision|OutlineRevision|MountingRevision
    model_bindings:dict[str,Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]]={}
    rationale:Annotated[str,Field(min_length=10,max_length=4000)]
    idempotency_key:Annotated[str,Field(min_length=8,max_length=100)]


def check_measurement_condition(key, bounds, requirement, scenarios):
    if key == 'input_voltage':
        lo, hi = bounds.normalized('voltage')
        if not any(s.engine == 'linear_dc' and any(
                source.minimum_v == lo and source.maximum_v == hi for source in s.linear_sources
        ) for s in scenarios):
            raise ValueError('FORMAL_SOURCE_ENVELOPE_UNCOVERED')
    elif key == 'observation_window':
        if list(bounds.normalized('time')) != requirement.time_window_s:
            raise ValueError('FORMAL_MEASUREMENT_WINDOW_UNCOVERED')
    elif key == 'measurement_band':
        band = list(bounds.normalized('frequency'))
        if requirement.thdn:
            measured = requirement.thdn.bandwidth_hz == band
        else:
            measured = requirement.quantity == 'noise_voltage_rms' and all(
                scenario.frequency_hz == band for scenario in scenarios)
        if not measured:
            raise ValueError('FORMAL_MEASUREMENT_BAND_UNCOVERED')
    else:
        raise ValueError('FORMAL_CONDITION_ADAPTER_REQUIRED: ' + key)


def check_requirement_binding(document, contract):
    raw = json.loads(document)
    if (not isinstance(raw, dict) or not isinstance(raw.get('natural_language'), str)
            or not raw['natural_language'].strip()):
        raise ValueError('NATURAL_LANGUAGE_REQUIREMENT_REQUIRED')
    revision_fields = {'functional_limit_changes', 'functional_additions', 'functional_scenario_additions', 'i2c_pullup_changes',
                       'contract_description_change', 'circuit_removal', 'stackup_change', 'outline_change', 'placement_requirements',
                       'mounting_hole_change', 'connectivity_changes'}
    if revision_fields & raw.keys():
        raise ValueError('FUNCTIONAL_REVISION_REQUIRES_DELIVERED_BASE')
    formal = raw.get('formal_requirements', [])
    by_id = {requirement.id: requirement for requirement in contract.requirements}
    pullups = {requirement.id: requirement for requirement in contract.i2c_pullups}
    if not isinstance(formal, list) or not all(
            isinstance(row, dict) and isinstance(row.get('id'), str) for row in formal):
        raise ValueError('FROZEN_REQUIREMENT_COVERAGE_MISMATCH')
    declared = {row['id'] for row in formal}
    if len(declared) != len(formal) or not by_id.keys() <= declared <= (by_id.keys() | pullups.keys()):
        raise ValueError('FROZEN_REQUIREMENT_COVERAGE_MISMATCH')
    scenarios = {scenario.id: scenario for scenario in contract.scenarios}
    for row in formal:
        if row['id'] in pullups:
            if I2CPullups.model_validate(row) != pullups[row['id']]:
                raise ValueError('FROZEN_I2C_PULLUP_CHANGED')
            continue
        requirement = by_id[row['id']]
        interval = Interval.model_validate(row.get('allowed'))
        if interval.normalized(MEASUREMENT_DIMENSIONS[requirement.quantity]) != (requirement.minimum, requirement.maximum):
            raise ValueError('FROZEN_REQUIREMENT_LIMIT_CHANGED')
        conditions = row.get('conditions', {})
        if not isinstance(conditions, dict):
            raise ValueError('FORMAL_CONDITIONS_MUST_BE_OBJECT')
        bound_scenarios = [scenarios[key] for key in requirement.scenarios]
        for key, bounds in conditions.items():
            check_measurement_condition(key, Interval.model_validate(bounds), requirement, bound_scenarios)
    if any(s.engine == 'linear_dc' and not s.linear_sources for s in contract.scenarios):
        raise ValueError('JOB_LINEAR_SOURCE_ENVELOPE_REQUIRED')
    return raw


def diagnose(result,checks):
    findings=[]
    for check,report in checks.items():
        for item in report.get('findings',[]):
            if item.get('severity')=='error':findings.append(dict(check=check,**item))
    if result.get('error'):
        if findings and result['error'].startswith('ValueError: CHECK_ERRORS:'):
            findings.append(dict(code='PIPELINE_FAILED', severity='error', message='设计流程停在验证阶段，具体原因见各检查项。',
                                 next_action='按对应场景的诊断补齐证据后复验。'))
        else:
            findings.append(dict(code='PIPELINE_FAILED', severity='error', message=result['error']))
    actions = []
    for finding in findings:
        row = dict(code=finding.get('code'), action=finding.get('next_action',
                   'Inspect the named check, submit a corrected source-selected plan, and retry without changing the frozen requirement.'))
        if 'scenario' in finding:
            row['scenario'] = finding['scenario']
        actions.append(row)
    return dict(findings=findings, next_actions=actions, requires_new_candidate=result.get('status')!='cad_checks_passed')


def runner_evidence(run):
    result_path=run/'run.json'
    result=json.loads(result_path.read_bytes()) if result_path.is_file() else None
    journal=run/'journal.json'
    calls=json.loads(journal.read_bytes())['calls'] if journal.is_file() else []
    pending=bool(calls and 'response' not in calls[-1])
    return result,pending


def delivery_incomplete(record, checks, message):
    record.update(status='failed', diagnosis=diagnose(dict(status='failed',error=message),checks))


def design_job_status(attempts, max_attempts):
    if not attempts:
        return 'awaiting_candidate'
    latest = attempts[-1]['status']
    if latest in ('running', 'interrupted'):
        return latest
    if any(row['status']=='passed' and row.get('delivery') for row in attempts):
        return 'completed_reference_design'
    return 'awaiting_revision' if len(attempts)<max_attempts else 'needs_attention_attempt_budget'


def stop_timed_out_runner(proc, run):
    cleanup_errors = []
    try:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid,signal.SIGKILL)
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as error:
            cleanup_errors.append(str(error))
    except (OSError, subprocess.SubprocessError) as error:
        cleanup_errors.append(str(error))
    from pcb_mcp_client.process_job import snapshot,belongs
    try:
        for child in snapshot():
            if belongs(child,run) and child in snapshot():
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
    except (OSError, subprocess.SubprocessError) as error:
        cleanup_errors.append(str(error))
    return cleanup_errors


class DesignJobService:
    def job_path(self,key):
        require(len(key)==32 and all(c in '0123456789abcdef' for c in key),'INVALID_JOB_ID','Invalid design job identifier.')
        return self.root/'design_jobs'/key

    def design_job_create(self,request):
        with self.locked():
            if request.base_job is None:
                check_requirement_binding(request.requirement_json,request.contract)
                contract=request.contract.model_dump(mode='json')
            else:
                _,base,base_requirement,contract=self.delivered_attempt(request.base_job)
                original=self.load(base['project_id'])
                check_base_state(base,original)
                change=json.loads(request.requirement_json)
                if not isinstance(change,dict):
                    raise ValueError('REVISION_REQUEST_TEXT_REQUIRED')
                removal=removal_request(change)
                construction=stackup_request(change)
                outline=outline_request(change)
                mounting=mounting_request(change)
                revised_criteria(base_requirement,change,outline=outline,construction=construction,mounting=mounting)
                placement_targets=placement_requirements(change)
                if placement_targets and removal is not None:
                    raise ValueError('PLACEMENT_REQUIREMENTS_PLAN_MISMATCH')
                check_placement_references(placement_targets,base['placements'])
                if outline is not None:
                    if removal is not None:
                        raise ValueError('OUTLINE_REVISION_PLAN_MISMATCH')
                    outline_target(outline,original,construction,mounting=mounting)
                elif construction is not None:
                    if removal is not None:
                        raise ValueError('STACKUP_REVISION_PLAN_MISMATCH')
                    if placement_targets:
                        stackup_target(construction,original,mounting=mounting)
                    else:
                        revised_stackup(construction,original,base['placements'],mounting=mounting)
                elif mounting is not None:
                    if removal is not None:
                        raise ValueError('MOUNTING_REVISION_PLAN_MISMATCH')
                    if placement_targets:
                        mounting_board(mounting,original)
                    else:
                        revised_mounting(mounting,original,base['placements'])
                if removal is not None:
                    removal_snapshot(removal,original)
                changes=functional_limit_changes(change)
                pullup_changes=i2c_pullup_changes(change)
                rewiring=connectivity_changes(change)
                if rewiring is not None:
                    if removal or construction or outline or mounting or placement_targets or pullup_changes:
                        raise ValueError('CONNECTIVITY_CHANGES_PLAN_MISMATCH')
                    connectivity_revision(rewiring,original)
                if pullup_changes and (removal or construction or outline or mounting or placement_targets):
                    raise ValueError('REVISION_I2C_PULLUP_REQUIRES_COMPONENT_REVISION')
                contract=revised_functional_contract(removal_contract(contract,removal),changes,
                                                     *functional_additions(change),pullup_changes=pullup_changes,
                                                     description_change=contract_description_change(change))
                revised_requirement_items(original['requirements'],[*changes,*pullup_changes])
            key=uuid.uuid4().hex;folder=self.job_path(key);folder.mkdir(parents=True)
            value=dict(job_id=key,name=request.name,status='awaiting_candidate',max_attempts=request.max_attempts,
                       attempt_timeout_s=request.attempt_timeout_s,attempts=[],
                       requirement_sha256=registry.sha(request.requirement_json.encode()),
                       contract_sha256=registry.sha(registry.canonical(contract)))
            if request.base_job is not None:
                value.update(base_job=request.base_job.model_dump(),base=base,require_connectivity_changes=True)
            (folder/'requirement.json').write_text(request.requirement_json)
            (folder/'contract.json').write_bytes(registry.canonical(contract));write_json(folder/'job.json',value)
            return value

    def design_job_search(self, request):
        """Return task references from this workspace, including pre-search jobs."""
        directory = self.root / 'design_jobs'
        terms = request.query.casefold().split()
        matches = []
        unavailable = 0
        if directory.is_dir() and not directory.is_symlink():
            for path in directory.glob('*/job.json'):
                folder = path.parent
                if (len(folder.name) != 32 or any(c not in '0123456789abcdef' for c in folder.name)
                        or folder.is_symlink() or path.is_symlink()):
                    continue
                try:
                    document = folder / 'requirement.json'
                    if document.is_symlink() or (folder / 'contract.json').is_symlink():
                        unavailable += 1
                        continue
                    job, _ = self.load_design_job(folder.name)
                    if job['job_id'] != folder.name:
                        unavailable += 1
                        continue
                    prose = json.loads(document.read_bytes())['natural_language'] if terms else ''
                    attempts = job['attempts']
                    project_ids = [((row.get('result') or {}).get('project_id') or '') for row in attempts]
                    searchable = '\n'.join([job['name'], prose, folder.name, *project_ids]).casefold()
                    if not all(term in searchable for term in terms):
                        continue
                    changed = path.stat().st_mtime
                    row = dict(job_id=folder.name, name=job['name'], status=job['status'],
                               latest_attempt=attempts[-1]['number'] if attempts else None,
                               updated_at=datetime.fromtimestamp(changed, timezone.utc).isoformat())
                    if job.get('base_job'):
                        row['base_job'] = job['base_job']
                    matches.append((changed, folder.name, row))
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    unavailable += 1
        matches.sort(reverse=True)
        end = request.offset + request.limit
        return dict(jobs=[row for _, _, row in matches[request.offset:end]], total=len(matches),
                    next_offset=end if end < len(matches) else None, unavailable_jobs=unavailable)

    def design_job_read(self,request):
        with self.locked():
            job, folder = self.load_design_job(request.job_id)
        outcome = job_outcome(self.root, folder, job)
        references = [dict(job_id=job['job_id'], attempt=a['number'])
                      for a in reversed(job['attempts']) if a['status'] == 'passed']
        if job.get('base_job'):
            references.append(job['base_job'])
        outcome['verified_delivery'] = None
        for ref in references:
            try:
                _, base, _, _ = self.delivered_attempt(DeliveredAttempt(**ref))
            except (OSError, ValueError, TypeError, KeyError, BadZipFile, DomainError):
                continue
            outcome['verified_delivery'] = dict(**ref, **{k: base[k] for k in
                ('project_id', 'revision', 'bundle_path', 'bundle_sha256')})
            break
        messages = []
        current = outcome['current_attempt']
        if current and current['status'] == 'failed':
            messages.append(f"第 {current['number']} 次尝试未通过。")
        if outcome['verified_delivery']:
            messages.append("已通过的版本可从 verified_delivery 打开。")
        elif current and current['status'] == 'failed':
            messages.append("此任务尚无通过验收的交付版本。")
        if outcome['review_drafts']:
            messages.append("可从 review_drafts 打开已导出的 KiCad 审阅稿，各稿标有来源和功能检查范围。")
        summary = dict(message=''.join(messages), verified_delivery=outcome['verified_delivery'],
                       current_attempt=current, review_drafts=outcome['review_drafts'])
        return dict(user_result=summary, **job)

    def load_design_job(self,key):
        folder=self.job_path(key);value=json.loads((folder/'job.json').read_text())
        if registry.sha((folder/'requirement.json').read_bytes())!=value['requirement_sha256'] or registry.sha((folder/'contract.json').read_bytes())!=value['contract_sha256']:raise ValueError('DESIGN_JOB_ACCEPTANCE_CHANGED')
        value.setdefault('attempt_timeout_s', DEFAULT_ATTEMPT_TIMEOUT_S)
        for attempt in value['attempts']:
            if attempt['status']=='passed' and not attempt.get('delivery'):
                delivery_incomplete(attempt, {}, 'DESIGN_JOB_DELIVERY_INCOMPLETE')
        value['status']=design_job_status(value['attempts'],value['max_attempts'])
        return value,folder

    def delivered_attempt(self,reference):
        value,folder=self.load_design_job(reference.job_id)
        record=next((item for item in value['attempts'] if item['number']==reference.attempt),None)
        if record is None or record['status']!='passed':
            raise ValueError('DESIGN_JOB_BASE_ATTEMPT_NOT_DELIVERED')
        run_dir=folder/f'attempt-{reference.attempt:03d}'/'run'
        base,requirement,contract,_=baseline(run_dir)
        if (record.get('delivery',base)!=base or registry.sha(registry.canonical(contract))!=value['contract_sha256']
                or not Path(base['bundle_path']).is_relative_to(self.project_path(base['project_id']).resolve())):
            raise ValueError('DESIGN_JOB_BASE_DELIVERY_CHANGED')
        return run_dir,base,requirement,contract

    def design_job_attempt(self,request):
        # Separate per-job lock: child MCP must acquire the ordinary data lock.
        folder=self.job_path(request.job_id)
        with (folder/'.attempt.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise ValueError('DESIGN_JOB_ATTEMPT_RUNNING')
            value,folder=self.load_design_job(request.job_id)
            fingerprint=registry.sha(registry.canonical(request.model_dump(mode='json')))
            previous=next((a for a in value['attempts'] if a['idempotency_key']==request.idempotency_key),None)
            if previous:
                if previous['request_sha256']!=fingerprint:raise ValueError('DESIGN_JOB_IDEMPOTENCY_CONFLICT')
                if previous['status'] not in ('running','interrupted'):return previous
                if previous is not value['attempts'][-1] or previous.get('runner_holds_attempt_lock') is not True:
                    raise ValueError('DESIGN_JOB_RECOVERY_UNPROVEN: Inspect the retained attempt and runner before continuing.')
            elif value['attempts'] and value['attempts'][-1]['status'] in ('running','interrupted'):
                raise ValueError('DESIGN_JOB_RESUME_EXISTING_ATTEMPT: Resubmit the same candidate and idempotency key.')
            elif len(value['attempts'])>=value['max_attempts']:
                raise ValueError('DESIGN_JOB_ATTEMPT_BUDGET_EXHAUSTED')
            if request.plan.requirement_sha256!=value['requirement_sha256']:raise ValueError('DESIGN_JOB_REQUIREMENT_CHANGED')
            revising='base_job' in value
            revision_plan=isinstance(request.plan,RevisionPlan)
            if revising!=revision_plan:
                raise ValueError('DESIGN_JOB_PLAN_MODE_MISMATCH')
            plan=request.plan.model_dump(mode='json')
            number=previous['number'] if previous else len(value['attempts'])+1
            attempt=folder/f'attempt-{number:03d}'
            run=attempt/'run'
            resume=False
            if previous:
                if (json.loads((attempt/'plan.json').read_bytes())!=plan
                        or json.loads((attempt/'model-bindings.json').read_bytes())!=request.model_bindings):
                    raise ValueError('DESIGN_JOB_RETAINED_INPUT_CHANGED')
                result,pending=runner_evidence(run)
                if result and result['status'] in ('cad_checks_passed','failed') and not pending:
                    return self.finish_attempt(value,folder,previous,run,result)
                resume=(run/'journal.json').is_file()
                if run.exists() and not resume:
                    raise ValueError('DESIGN_JOB_RECOVERY_JOURNAL_MISSING: Inspect the retained attempt.')
            if revising:
                change=json.loads((folder/'requirement.json').read_bytes())
                pullup_changes=i2c_pullup_changes(change)
                if (value.get('require_connectivity_changes') and isinstance(request.plan,ConnectivityRevision)
                        and 'connectivity_changes' not in change):
                    raise ValueError('REVISION_CONNECTIVITY_CHANGES_REQUIRED')
                if isinstance(request.plan,CircuitRemoval)!=('circuit_removal' in change):
                    raise ValueError('CIRCUIT_REMOVAL_PLAN_MISMATCH')
                check_board_revision_mode(request.plan,change)
                if request.model_bindings:
                    if not isinstance(request.plan, CircuitExtension):
                        raise ValueError('REVISION_INHERITS_BASE_FUNCTIONAL_CONTRACT')
                    added_model_bindings(request.plan,request.model_bindings)
                base_run,base,_,_=self.delivered_attempt(DeliveredAttempt.model_validate(value['base_job']))
                if base!=value['base'] or request.plan.base_sha256!=base['base_sha256']:
                    raise ValueError('REVISION_BASE_EVIDENCE_CHANGED')
                check_i2c_pullup_plan(request.plan,pullup_changes,base_run,base)
                if isinstance(request.plan, FunctionalRevision):
                    if not (functional_limit_changes(change) or change.get('functional_additions')
                            or contract_description_change(change) is not None):
                        raise ValueError('REVISION_FUNCTIONAL_LIMIT_CHANGE_REQUIRED')
            if previous:
                record=previous
                if resume:
                    retained=attempt/f'interruption-{uuid.uuid4().hex}'
                    retained.mkdir()
                    for name in ('run.json','journal.json'):
                        if (run/name).is_file():
                            shutil.copyfile(run/name,retained/name)
                    record.setdefault('recovery_snapshots',[]).append(str(retained))
            else:
                attempt.mkdir()
                write_json(attempt/'plan.json',plan)
                write_json(attempt/'model-bindings.json',request.model_bindings)
                record=dict(number=number,idempotency_key=request.idempotency_key,request_sha256=fingerprint,
                            rationale=request.rationale,artifact_directory=str(attempt),runner_holds_attempt_lock=True)
                value['attempts'].append(record)
            record['status']='running'
            value['status']='running'
            write_json(folder/'job.json',value)
            command=[sys.executable,'-m','pcb_mcp_client.run_plan',str(attempt/'plan.json'),
                     '--requirement',str(folder/'requirement.json'),'--output',str(run),'--require-delivery']
            if revising:
                command.extend(['--base-run',str(base_run)])
                if request.model_bindings:
                    command.extend(['--model-bindings',str(attempt/'model-bindings.json')])
            else:
                command.extend(['--functional',str(folder/'contract.json'),'--model-bindings',str(attempt/'model-bindings.json')])
            if resume:
                command.append('--resume')
            try:
                with (attempt/'process.log').open('a') as log:
                    if previous:
                        log.write('\nContinuing the same design-job attempt.\n')
                        log.flush()
                    # A disconnected host may exit while its runner is still live.
                    # The runner inherits the same flock until it exits.
                    proc=subprocess.Popen(command,env={**os.environ,'PCB_MCP_DATA_DIR':str(self.root)},
                                          stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
                                          pass_fds=(lock.fileno(),))
                    try:proc.wait(timeout=value['attempt_timeout_s'])
                    except subprocess.TimeoutExpired:
                        # Let the async stdio context close its separately grouped
                        # MCP child before falling back to exact-marker cleanup.
                        cleanup_errors=stop_timed_out_runner(proc,run)
                        raise
            except subprocess.TimeoutExpired:
                record.update(status='failed',diagnosis=dict(
                    findings=[dict(code='DESIGN_JOB_TIMEOUT',severity='error',
                                   attempt_timeout_s=value['attempt_timeout_s'],cleanup_errors=cleanup_errors)],
                    next_actions=['Inspect the retained process and journal before retrying.'],
                    requires_new_candidate=True))
                return self.save_attempt(value,folder,record)
            result,pending=runner_evidence(run)
            interrupted_before_result=result is None and (proc.returncode<0 or proc.returncode==130)
            if pending or (result and result['status'] in ('running','interrupted')) or interrupted_before_result:
                record.update(status='interrupted',result=result,diagnosis=dict(
                    findings=[dict(code='DESIGN_JOB_INTERRUPTED_ATTEMPT',severity='error')],
                    next_actions=['Resubmit the identical candidate and idempotency key to continue the journal. Inspect process.log if recovery is refused.'],
                    requires_new_candidate=False))
                return self.save_attempt(value,folder,record)
            result=result or dict(status='failed',error='Candidate process returned '+str(proc.returncode))
            return self.finish_attempt(value,folder,record,run,result)

    def finish_attempt(self,value,folder,record,run,result):
        checks_path=run/'checks.json'
        checks=json.loads(checks_path.read_bytes()) if checks_path.is_file() else {}
        complete=result.get('status')=='cad_checks_passed' and result.get('functional_reference_passed') is True
        record.update(status='failed',result=result,diagnosis=diagnose(result,checks))
        if complete and result.get('software_acceptance')!='pass':
            delivery_incomplete(record,checks,'DESIGN_JOB_DELIVERY_INCOMPLETE')
        elif complete:
            try:
                delivery,_,_,_=baseline(run)
                if delivery['contract_sha256']!=value['contract_sha256']:
                    raise ValueError('DESIGN_JOB_DELIVERY_CONTRACT_CHANGED')
            except (OSError,KeyError,ValueError,BadZipFile) as error:
                delivery_incomplete(record,checks,str(error))
            else:
                record.update(status='passed',delivery=delivery)
                value['latest_delivery']=dict(attempt=record['number'],**delivery)
                if SUMMARY_NAME in delivery['file_hashes']:
                    record['revision_summary']=dict(path=str(run / SUMMARY_NAME),
                                                    sha256=delivery['file_hashes'][SUMMARY_NAME])
        return self.save_attempt(value,folder,record)

    def save_attempt(self,value,folder,record):
        value['status']=design_job_status(value['attempts'],value['max_attempts'])
        write_json(folder/'job.json',value)
        return record
