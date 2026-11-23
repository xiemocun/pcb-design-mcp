"""A rule implementation upgrade invalidates old evidence even if files match."""
import copy,json
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pcb_mcp.service import Service,DomainError
from pcb_mcp.models import BuildRef

ROOT=Path(__file__).resolve().parents[1]
report=json.loads((ROOT/'acceptance-esp32-reference/report.json').read_text())
build=json.loads((ROOT/'acceptance-esp32-reference/successful_build.json').read_text())
service=Service(report['data_root'])
request=BuildRef(project_id=build['project_id'],build_id=build['build']['build_id'])
service.target(request)
rules=copy.deepcopy(service.rules_list());rules['rulepacks'][0]['version']='TEST_CHANGED'
with patch.object(service,'rules_list',return_value=rules):
    try:
        service.target(request)
        raise AssertionError('Changed rule evidence silently accepted')
    except DomainError as e:assert e.payload['code']=='RULEPACK_CHANGED'
service.target(request)
# A battery declaration must match its value and physical positive/negative pins.
generic=json.loads((ROOT/'acceptance-generic/successful_build.json').read_text())
state=json.loads((Path(generic['export']['schematic_path']).parent/'spec.json').read_text())
battery=next(c for c in state['components'].values() if c['library_id']=='Device:Battery_Cell')
vcc=next(n for n in state['nets'].values() if n['name']=='VCC')
ground=next(n for n in state['nets'].values() if n['name']=='GND')
pin=lambda num:dict(component_id=battery['id'],pin_id=next(p['pin_id'] for p in battery['pins'] if p['number']==num))
source=dict(boundary_pin=pin('1'),net_id=vcc['id'],ground_net_id=ground['id'],nominal_v=3.0)
assert service.source_valid(state,source)
assert not service.source_valid(state,dict(source,nominal_v=5.0))
assert not service.source_valid(state,dict(source,boundary_pin=pin('2')))
result=dict(passed=True,checks=['Changed rule implementation/evidence blocks old build without file tampering','Battery nominal voltage and polarity must agree with real boundary pins'])
(ROOT/'acceptance-evidence.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
