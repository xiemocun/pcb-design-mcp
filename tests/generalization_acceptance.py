"""Consolidate existing real evidence; do not rerun or reinterpret failed checks."""
import hashlib,json
from pathlib import Path

root=Path('work/generalization');held=root/'heldout-01'
def read(p):return json.loads(p.read_text())
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
freeze=root/'freeze-01.json';frozen=read(freeze)
assert digest(freeze)=='867ac4471745e8d7dc39934dd5002921fdbe9263966c840a6d105f83797865ac'
assert all(digest(Path(p))==h for p,h in frozen.items())
baseline=read(root/'baseline-hashes.json');preserved={p:h for p,h in baseline.items() if p.startswith(('data/','catalog/'))}
assert all(digest(Path(p))==h for p,h in preserved.items())
cases=[]
for folder in ('power-run-01','bandpass-run-01','interlock-run-03','bipolar-run-01'):
    path=held/folder;verification=read(path/'verification.json');result=verification['attempt']['result']
    checks_path=next(path.glob('data/design_jobs/*/attempt-001/run/checks.json'));checks=read(checks_path)
    assert checks['functional_check']['verdict']=='pass' and all(m['passed'] for m in checks['functional_check']['measurements'])
    assert checks['schematic_verify']['verdict']==checks['erc_run']['verdict']=='pass'
    repaired=None;geometry=checks
    if folder=='interlock-run-03':
        assert not verification['passed'] and checks['drc_run']['verdict']=='fail'
        repaired=read(held/'interlock-route-repair-01/verification.json');assert repaired['passed'] and repaired['electrical_design_unchanged'] and repaired['parent_pcb_build_id']==result['pcb_build_id']
        geometry=repaired['checks']
    else:assert verification['passed'] and result['status']=='cad_checks_passed'
    for tool in ('pcb_verify','drc_run','pcb_layout_check'):assert geometry[tool]['verdict']=='pass'
    assert geometry['pcb_copper_check']['geometry_verdict']=='pass'
    assert geometry['drc_run']['details']['unconnected_count']==0
    assert not any(x['severity']=='error' for report in geometry.values() for x in report.get('findings',[]))
    assert 'pcb_review_export' in geometry and 'bom_export' in geometry
    cases.append(dict(case=verification['case'],functional_passed=True,cad_geometry_passed=True,project_id=result['project_id'],schematic_build_id=result['schematic_build_id'],pcb_build_id=repaired['pcb_build_id'] if repaired else result['pcb_build_id'],measurements=checks['functional_check']['measurements'],original_attempt_passed=verification['passed'],routing_repaired=bool(repaired),verification_path=str(path/'verification.json'),repair_verification_path=str(held/'interlock-route-repair-01/verification.json') if repaired else None,remaining_report_verdicts={k:v.get('verdict') for k,v in checks.items() if v.get('verdict')=='incomplete'},physical_verified=False))
assert read(held/'bandpass-continuous-reference.json')['passed']
assert read(held/'interlock-run-03/composition.json')['verdict']=='pass'
new_parts=[read(held/n/'part-package.json')['parts'][0]['mpn'] for n in ('power-run-01','interlock-run-03','bipolar-run-01')]
assert len(set(new_parts))>=2
assert read(root/'design-job-protocol-02/verification.json')['passed']
installed=read(root/'installed-power-01/verification.json');assert installed['passed']
package=read(root/'package-verification.json');assert digest(Path(package['wheel']))==package['wheel_sha256'] and package['all_frozen_source_bytes_match']
tools_path=next((root/'installed-power-01').glob('data/design_jobs/*/attempt-001/run/tools.json'));tools=read(tools_path)
(root/'tool-contract-current.json').write_text(json.dumps(tools,indent=2)+'\n')
inputs={str(p):digest(p) for p in held.glob('sources/*') if p.is_file()}
(root/'preservation-final.json').write_text(json.dumps(dict(passed=True,checked_original_files=len(preserved),files=preserved),indent=2)+'\n')
report=dict(passed=True,scope='Six-stage general software architecture acceptance with four post-freeze source/model reference designs; not universal circuit success or physical qualification',core_freeze_sha256=digest(freeze),frozen_core_unchanged=True,heldout_requirements_sha256=digest(held/'requirements.json'),cases=cases,new_manufacturer_parts=new_parts,composed_modules=5,source_inputs_sha256=inputs,original_files_preserved=len(preserved),package=package,installed_reference_design_passed=True,tool_schema_count=len(tools['tools']),physical_verified=False)
(root/'acceptance-final.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
print(json.dumps(dict(passed=True,cases=len(cases),new_parts=new_parts,preserved=len(preserved),wheel_sha256=package['wheel_sha256']),indent=2))
