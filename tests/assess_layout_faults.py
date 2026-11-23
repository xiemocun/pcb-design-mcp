"""Assess real native fault-board summaries with the current independent rules."""
import json,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp.design_checks import layout_report
old=json.loads((R/'acceptance-esp32-reference/report.json').read_text());sf=Path(old['data_root'])/old['project_id']/'pcb_builds'/old['pcb']['pcb_build_id'];s=json.loads((sf/'spec.json').read_text());cases=json.loads((R/'acceptance-layout-faults/cases.json').read_text())
results=[]
for case in cases:
    report=layout_report(s,json.loads(Path(case['actual_file']).read_text()),old['pcb']['pcb_build_id'],'routed')
    assert report['verdict']=='fail' and case['expected'] in {f['code'] for f in report['findings']},(case,report)
    results.append(dict(name=case['name'],expected=case['expected'],caught=True))
result=dict(passed=True,native_fault_boards=len(results),cases=results,cleanup=json.loads((R/'acceptance-layout-faults/cleanup.json').read_text()))
(R/'acceptance-layout-faults/report.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
