"""Independent rules must reject native controller/correct-pin proximity faults."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp.design_checks import layout_report
report=json.loads((R/'acceptance-usb-interlock/report.json').read_text());folder=Path(report['data_root'])/report['project_id']/'pcb_builds'/report['pcb_build_id'];s=json.loads((folder/'spec.json').read_text());out=R/'acceptance-usb-native'
results=[]
for case in json.loads((out/'cases.json').read_text()):
    actual=json.loads(Path(case['actual_file']).read_text());r=layout_report(s,actual,report['pcb_build_id'],'routed')
    assert r['verdict']=='fail' and case['expected'] in {f['code'] for f in r['findings']},(case,r)
    if case['name']=='aux_input_pad_far_enable_unchanged':
        assert any(f['code']=='DECOUPLING_DISTANCE' for f in r['findings'])
    results.append(dict(name=case['name'],caught=True))
result=dict(passed=True,cases=results,cleanup=json.loads((out/'cleanup.json').read_text()))
(out/'report.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
