"""Assess saved native faults and source/rule/evidence boundaries."""
from pathlib import Path
import copy,json,sys,math
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp.copper import evaluate,dsn_report
from pcb_mcp.common import DomainError
rep=json.loads((R/'acceptance-bom/report.json').read_text());p=Path(rep['data_root'])/rep['project_id']/'pcb_builds'/rep['pcb_build_id'];s=json.loads((p/'spec.json').read_text());out=R/'acceptance-copper-native';results=[]
for case in json.loads((out/'cases.json').read_text()):
 actual=json.loads(Path(case['actual_file']).read_text());v=evaluate(s,actual,rep['pcb_build_id'],False)
 assert v['verdict']=='fail' and case['expected'] in {f['code'] for f in v['findings']},(case,v)
 results.append(dict(name=case['name'],caught=True,expected=case['expected'],**{k:v for k,v in case.items() if k.startswith('real_') or k.startswith('same_')}))
actual=json.loads((p/'copper_geometry.json').read_text());original=copy.deepcopy(actual)
actual.pop('escape_isolation');v=evaluate(s,actual,rep['pcb_build_id'],True);assert 'COPPER_ESCAPE_BACKBONE_UNPROVEN' in {f['code'] for f in v['findings']}
# One metre of 1mm x 10um copper at 20C: known 1.72-ohm dimensional check.
small=copy.deepcopy(s);net=next(n for n in small['nets'].values() if n['name']=='V3V3');small['nets']={net['id']:net};small['pcb_copper_policy']['rules']=[dict(net_id=net['id'],min_width_mm=.3,design_current_a=.1,reference_drop_budget_v=1.0)];small['pcb_copper_policy']['copper_thickness_um']=10.0
sample=dict(components={},copper_zone_count=0,conductors=[dict(uuid='TEST',kind='track',layer='F.Cu',net=net['name'],length_mm=1000.0,width_mm=1.0)])
v=evaluate(small,sample,rep['pcb_build_id'],True);assert math.isclose(v['nets'][0]['track_reference']['series_sum_resistance_20c_ohm'],1.72,rel_tol=1e-12)
assert v['verdict']=='incomplete' and not v['manufacturing_approved']
# Manufacturer/material assumptions cannot turn the screen into qualification.
small['pcb_copper_policy'].update(origin='user_statement',copper_thickness_um=140.0)
v=evaluate(small,sample,rep['pcb_build_id'],True);assert v['verdict']=='incomplete' and not v['thermal_qualified'] and not v['full_path_drop_qualified']
text=(p/'input.dsn').read_text();good=dsn_report(s,text);bad=text.replace('(width 300)','(width 150)',1)
try:dsn_report(s,bad);raise AssertionError('Tampered DSN passed')
except DomainError as e:assert e.payload['code']=='DSN_RULE_MISMATCH'
result=dict(passed=True,cases=results,missing_native_isolation_rejected=True,resistance_units_reference_verified=True,declared_assumptions_never_physical_approval=True,tampered_dsn_class_rejected=True)
(out/'report.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
