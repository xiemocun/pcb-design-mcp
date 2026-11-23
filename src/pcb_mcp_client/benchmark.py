"""Reproducible software-task evidence and paired comparisons.

This grader checks declared software requirements. It cannot certify physical
hardware, independent authorship, or a model's self-reported accounting.
"""
import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import zipfile
import numpy as np
from pydantic import Field,model_validator
from typing import Annotated,Literal
from .plan import Strict
from .journal import save,digest

CATEGORIES=('power','battery','sensing','analog','digital','audio','actuation','combined')
REQUIRED_CAD=('spec_validate','schematic_verify','erc_run','pcb_verify','drc_run','pcb_layout_check')


class Task(Strict):
    id:Annotated[str,Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')]
    category:Literal['power','battery','sensing','analog','digital','audio','actuation','combined']
    split:Literal['development','acceptance']
    origin:Literal['developer_authored','external_public','independent_participant']
    source:Annotated[str,Field(min_length=10,max_length=4000)]
    requirement_sha256:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
    contract_sha256:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]|None=None
    requirement_ids:list[str]=[]
    expected:Literal['complete','reject']='complete'
    expected_error_codes:list[str]=[]
    freeze_sha256:Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]|None=None
    elapsed_limit_s:Annotated[float,Field(gt=0,le=86400)]=1800.
    cluster_id:Annotated[str,Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,99}$')]|None=None

    @model_validator(mode='after')
    def required_proof(self):
        if self.expected=='complete' and (not self.contract_sha256 or not self.requirement_ids):
            raise ValueError('FUNCTIONAL_ACCEPTANCE_REQUIRED')
        if len(set(self.requirement_ids))!=len(self.requirement_ids):raise ValueError('DUPLICATE_REQUIREMENT_ID')
        if self.expected=='reject' and not self.expected_error_codes:raise ValueError('REJECTION_CODES_REQUIRED')
        if self.split=='acceptance' and not self.freeze_sha256:raise ValueError('ACCEPTANCE_FREEZE_REQUIRED')
        if self.split=='acceptance' and not self.cluster_id:raise ValueError('ACCEPTANCE_CLUSTER_REQUIRED')
        return self


def file_sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation_files(root):
    files={}
    for base in ('src','catalog','evidence-cache'):
        for path in (root/base).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
                files[str(path.relative_to(root))]=file_sha(path)
    for name in ('pyproject.toml','setup.py','MANIFEST.in','requirements-lock.txt',
                 'runtime/router.json','vendor/freerouting-2.4.1.jar','reviewer/Reviewer.swift'):
        path=root/name
        if path.is_file():files[name]=file_sha(path)
    return files


def freeze(root):
    packages={}
    for name in ('mcp','skidl','cryptography','jsonschema','numpy','pydantic'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]=None
    return dict(format='pcb-software-freeze-v1',files=implementation_files(root),
                environment=dict(python=platform.python_version(),platform=platform.platform(),packages=packages),
                scope='implementation and bundled assets; planner/model settings must also be fixed by the evaluation coordinator')


def verify_freeze(root,manifest):
    actual=implementation_files(root);expected=manifest['files']
    changed=sorted(k for k in set(actual)|set(expected) if actual.get(k)!=expected.get(k))
    return dict(passed=not changed,changed_files=changed,manifest_sha256=digest(manifest))


def grade(task,candidate,implementation_root=None,frozen=None,run_relative='run'):
    """Grade a normal runner directory and its actual structured RPC evidence."""
    failures=[]
    def need(condition,code):
        if not condition:failures.append(code)
    if task.split=='acceptance':
        need(implementation_root is not None and frozen is not None,'ACCEPTANCE_FREEZE_NOT_VERIFIED')
        if implementation_root is not None and frozen is not None:
            verified=verify_freeze(implementation_root,frozen)
            need(verified['passed'],'IMPLEMENTATION_CHANGED_AFTER_FREEZE')
            need(verified['manifest_sha256']==task.freeze_sha256,'ACCEPTANCE_FREEZE_IDENTITY_MISMATCH')
    run_dir=candidate/run_relative
    if not run_dir.resolve().is_relative_to(candidate.resolve()):
        raise ValueError('RUN_OUTSIDE_ISOLATED_CANDIDATE')
    try:
        result=json.loads((run_dir/'run.json').read_text())
        checks=json.loads((run_dir/'checks.json').read_text())
        journal=json.loads((run_dir/'journal.json').read_text())
        elapsed=result.get('elapsed_seconds')
        need(type(elapsed) in (int,float) and math.isfinite(elapsed) and 0<=elapsed<=task.elapsed_limit_s,'EXECUTION_TIME_BUDGET_UNPROVEN_OR_EXCEEDED')
        need(file_sha(run_dir/'requirement.json')==task.requirement_sha256,'REQUIREMENT_CONTENT_CHANGED')
        need(result.get('requirement_sha256')==task.requirement_sha256,'RUN_REQUIREMENT_CHANGED')
        responses={}
        for call in journal['calls']:
            response=call.get('response',{})
            if not response.get('isError') and isinstance(response.get('structuredContent'),dict):
                responses[call['tool']]=response['structuredContent'].get('data')
        for name,report in checks.items():
            if name.endswith('_unrouted'):continue
            need(name in responses and responses[name]==report,'REPORT_RPC_MISMATCH:'+name)
        observed_codes={f.get('code') for report in checks.values() for f in report.get('findings',[]) if f.get('severity')=='error'}
        if task.expected=='reject':
            need(result.get('status')=='failed','KNOWN_BAD_TASK_NOT_REJECTED')
            need(bool(observed_codes & set(task.expected_error_codes)),'EXPECTED_REJECTION_NOT_PROVEN')
        else:
            need(result.get('status')=='cad_checks_passed','CAD_PIPELINE_INCOMPLETE')
            need(result.get('started_from_empty') is True and not result.get('template_used'),'EMPTY_PROJECT_ORIGIN_UNPROVEN')
            for name in REQUIRED_CAD:
                need(checks.get(name,{}).get('verdict')=='pass','MANDATORY_CHECK_INCOMPLETE:'+name)
            need(checks.get('pcb_copper_check',{}).get('geometry_verdict')=='pass','COPPER_GEOMETRY_INCOMPLETE')
            need(checks.get('drc_run',{}).get('details',{}).get('unconnected_count')==0,'UNCONNECTED_COPPER')
            need(not observed_codes,'ERROR_FINDINGS_REMAIN')
            functional=checks.get('functional_check',{})
            need(functional.get('verdict')=='pass','FUNCTIONAL_REQUIREMENTS_INCOMPLETE')
            need(functional.get('contract_sha256')==task.contract_sha256,'FUNCTIONAL_CONTRACT_CHANGED')
            measured=functional.get('measurements',[])
            need({m.get('requirement') for m in measured}==set(task.requirement_ids),'FUNCTIONAL_REQUIREMENT_COVERAGE_MISMATCH')
            need(bool(measured) and all(m.get('passed') is True for m in measured),'FUNCTIONAL_MEASUREMENT_FAILED')
            need(functional.get('coverage',{}).get('required')==len(measured)==functional.get('coverage',{}).get('executed'),'FUNCTIONAL_SCENARIO_COVERAGE_MISMATCH')
            need(result.get('software_acceptance')=='pass','EXPLICIT_SOFTWARE_ACCEPTANCE_INCOMPLETE')
            exported=checks.get('pcb_review_export',{})
            manifest=exported.get('manifest',{})
            need(manifest.get('assembly',{}).get('status')=='complete_source_linked_tables','ASSEMBLY_DELIVERY_INCOMPLETE')
            need(manifest.get('portable_project',{}).get('passed') is True,'PORTABLE_PROJECT_INCOMPLETE')
            bundle=Path(exported.get('bundle_path',''))
            need(bundle.resolve().is_relative_to(candidate.resolve()),'DELIVERY_OUTSIDE_ISOLATED_CANDIDATE')
            if bundle.is_file() and bundle.resolve().is_relative_to(candidate.resolve()):
                with zipfile.ZipFile(bundle) as archive:
                    need(archive.testzip() is None,'DELIVERY_ZIP_INVALID')
                    for name,sha in manifest.get('hashes',{}).items():
                        need(hashlib.sha256(archive.read(name)).hexdigest()==sha,'DELIVERY_ARTIFACT_CHANGED:'+name)
                    need(bool(manifest.get('hashes')),'DELIVERY_MANIFEST_EMPTY')
            else:need(False,'DELIVERY_BUNDLE_MISSING')
    except (OSError,KeyError,ValueError,TypeError,zipfile.BadZipFile) as error:
        failures.append('EVIDENCE_UNAVAILABLE:'+str(error))
    return dict(task_id=task.id,category=task.category,split=task.split,origin=task.origin,cluster_id=task.cluster_id,expected=task.expected,
                task_sha256=digest(task.model_dump(mode='json')),passed=not failures,failures=failures,
                scope='declared software evidence only',physical_verified=False,
                total_planning_time_and_cost_verified=False,independent_authorship_verified=False)


def paired_comparison(ours,theirs,look_index=1):
    if type(look_index) is not int or not 1<=look_index<=1000000:raise ValueError('COMPARISON_LOOK_INVALID')
    if len({r['task_id'] for r in ours})!=len(ours) or len({r['task_id'] for r in theirs})!=len(theirs):
        raise ValueError('DUPLICATE_COMPARISON_TASK')
    a={r['task_id']:r for r in ours};b={r['task_id']:r for r in theirs}
    if set(a)!=set(b) or not a:raise ValueError('COMPARISON_TASK_SETS_DIFFER')
    keys=sorted(a)
    if any(a[k]['task_sha256']!=b[k]['task_sha256'] for k in keys):raise ValueError('COMPARISON_REQUIREMENTS_DIFFER')
    if any(type(r.get('passed')) is not bool for r in [*a.values(),*b.values()]):raise ValueError('COMPARISON_OUTCOME_NOT_BOOLEAN')
    if any(r.get('expected')=='reject' for r in [*a.values(),*b.values()]):raise ValueError('REJECTION_CASES_NOT_SUCCESS_RATE_TASKS')
    if any(a[k].get('cluster_id')!=b[k].get('cluster_id') for k in keys):raise ValueError('COMPARISON_CLUSTERS_DIFFER')
    delta=np.array([int(a[k]['passed'])-int(b[k]['passed']) for k in keys])
    declared=all(a[k].get('cluster_id') for k in keys)
    groups={}
    for key,value in zip(keys,delta):groups.setdefault(a[key].get('cluster_id') or 'task:'+key,[]).append(int(value))
    scores=np.array([sum(g) for g in groups.values()]);sizes=np.array([len(g) for g in groups.values()])
    rng=np.random.default_rng(20261109)
    indices=rng.integers(0,len(groups),size=(10000,len(groups)))
    samples=scores[indices].sum(axis=1)/sizes[indices].sum(axis=1)
    low,high=np.quantile(samples,[.025,.975])
    alpha=.05/(look_index*(look_index+1))
    decision_low,decision_high=np.quantile(samples,[alpha/2,1-alpha/2])
    # Exact whole-cluster label swaps under paired exchangeability. This prevents
    # a handful of discordant wins (or repeated variants of one family) from
    # producing a claim just because a percentile bootstrap hits its boundary.
    distribution={0:1};nonzero=[abs(int(v)) for v in scores if v]
    for weight in nonzero:
        updated={}
        for value,count in distribution.items():
            for sign in (-1,1):updated[value+sign*weight]=updated.get(value+sign*weight,0)+count
        distribution=updated
    observed=abs(int(scores.sum()))
    probability=sum(count for value,count in distribution.items() if abs(value)>=observed)/(2**len(nonzero))
    return dict(tasks=len(keys),ours_passed=sum(a[k]['passed'] for k in keys),
                theirs_passed=sum(b[k]['passed'] for k in keys),paired_difference=float(delta.mean()),
                paired_bootstrap_95_interval=[float(low),float(high)],
                bootstrap_unit='predeclared task-family cluster' if declared else 'individual task; clustering not established',clusters=len(groups),cluster_metadata_complete=bool(declared),
                exact_cluster_label_swap_two_sided_p=probability,
                sequential_look_index=look_index,alpha_for_this_look=alpha,alpha_spending_rule='0.05/(look_index*(look_index+1)); total across all formal looks <=0.05',
                decision_bootstrap_interval=[float(decision_low),float(decision_high)],decision_bootstrap_confidence=1-alpha,
                positive_success_rate_evidence=bool(declared and decision_low>0 and probability<alpha),
                scope='Cluster percentile interval is approximate; exact label-swap test assumes whole-cluster paired exchangeability. Conditional on registered task families, not global product ranking or cost evidence.')


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('freeze');p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('verify-freeze');p.add_argument('--root',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True)
    p=sub.add_parser('grade');p.add_argument('--task',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--run-relative',default='run',help='Runner evidence path inside the isolated candidate directory.')
    p.add_argument('--root',type=Path);p.add_argument('--freeze',type=Path)
    p=sub.add_parser('compare');p.add_argument('--ours',type=Path,required=True);p.add_argument('--theirs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--look-index',type=int,default=1)
    args=parser.parse_args()
    if getattr(args,'output',None) and args.output.exists():raise ValueError('BENCHMARK_OUTPUT_ALREADY_EXISTS')
    if args.command=='freeze':result=freeze(args.root.resolve())
    elif args.command=='verify-freeze':result=verify_freeze(args.root.resolve(),json.loads(args.manifest.read_text()))
    elif args.command=='grade':result=grade(Task.model_validate_json(args.task.read_text()),args.candidate.resolve(),args.root.resolve() if args.root else None,json.loads(args.freeze.read_text()) if args.freeze else None,args.run_relative)
    else:result=paired_comparison(json.loads(args.ours.read_text()),json.loads(args.theirs.read_text()),args.look_index)
    if getattr(args,'output',None):args.output.parent.mkdir(parents=True,exist_ok=True);save(args.output,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result.get('passed') is False else 0


if __name__=='__main__':raise SystemExit(main())
