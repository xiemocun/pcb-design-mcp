"""Development regression of existing logic/pulse plans through four-layer MCP.

These are deliberately known tasks, not v1.0 independent acceptance cases.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser();p.add_argument('--case',choices=['logic','pulse'],required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--max-passes',type=int,default=20)
    p.add_argument('--attempt-limit',type=int,default=2);args=p.parse_args()
    root=Path(__file__).resolve().parents[1];out=args.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    os.environ['PCB_MCP_DATA_DIR']=str(out/'data')
    from pcb_mcp_client.run_plan import execute
    fixture=root/'tests/fixtures/software-release'
    plan=json.loads((fixture/(args.case+'-plan.json')).read_text())
    requirement=json.loads((fixture/(args.case+'-requirement.json')).read_text())
    construction=json.loads((root/'tests/fixtures/multilayer/stackup-four.json').read_text())
    requirement['origin']='known_task_four_layer_development_regression_not_heldout'
    requirement['board'].update(layers=4,thickness_mm=1.6)
    requirement['software_acceptance'].extend([
        dict(id='native-stackup',report='pcb_verify',pointer='/details/stackup/passed',operator='equals',expected=True),
        dict(id='inner-layer-one',report='pcb_verify',pointer='/details/stackup/copper_layers/1',operator='equals',expected='In1.Cu'),
        dict(id='inner-layer-two',report='pcb_verify',pointer='/details/stackup/copper_layers/2',operator='equals',expected='In2.Cu')])
    save(out/'requirement.json',requirement)
    digest=hashlib.sha256((out/'requirement.json').read_bytes()).hexdigest()
    plan['requirement_sha256']=digest;plan['name']='Four-layer development regression '+args.case
    plan['routing_max_passes']=args.max_passes;plan['routing_attempt_limit']=args.attempt_limit
    for setting in plan['settings']:
        if setting['tool']=='board_constraints_set':setting['args'].update(construction)
        if setting['tool']=='requirements_set':
            for item in setting['args']['items']:
                if item['key']=='frozen_requirement_sha256':item['value']=digest
    save(out/'plan.json',plan)
    result=asyncio.run(execute(out/'plan.json',out/'requirement.json',out/'run'))
    passed=result['status']=='cad_checks_passed' and result['software_acceptance']=='pass'
    save(out/'verification.json',dict(passed=passed,result=result,physical_verified=False,
         scope='known-task development regression, not independent v1 acceptance'))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
