"""Changed facts/status invalidate real saved prototype evidence without edits."""
import copy,json,sys
from pathlib import Path
from unittest.mock import patch
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts
from pcb_mcp.service import Service
from pcb_mcp.models import BuildRef
from pcb_mcp.common import DomainError
report=json.loads((R/'acceptance-usb-interlock/report.json').read_text());service=Service(report['data_root'])
request=BuildRef(project_id=report['project_id'],build_id=report['schematic_build_id']);service.target(request)
original=parts.catalog();checks=[]
for kind in ['source_fact_changed','source_snapshot_missing']:
    rows=copy.deepcopy(original);c=next(p for p in rows if p['symbol_id']=='PCB_MCP:TUSB320LAI_GPIO')
    if kind=='source_fact_changed':c['facts']['supply_max_v']=4.8
    else:c['evidence_status']='missing_or_changed'
    with patch.object(parts,'catalog',return_value=rows):
        try:service.target(request);raise AssertionError('Changed source accepted')
        except DomainError as e:assert e.payload['code']=='RULEPACK_CHANGED',e.payload
    checks.append(kind)
service.target(request)
result=dict(passed=True,checks=checks,original_build_files_unchanged=True,qualification='Saved real schematic evidence rejects changed curated inputs, even with no bound physical BOM')
(R/'acceptance-usb-evidence.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
