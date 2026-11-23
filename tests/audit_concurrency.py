"""A mid-audit edit cannot publish a mixed-revision report."""
import json,shutil,sys,uuid
from pathlib import Path
from unittest.mock import patch
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp.service import Service
from pcb_mcp.models import AuditRequest,OperatingConditions
from pcb_mcp.common import DomainError
fixture=json.loads((R/'acceptance-editing/report.json').read_text());out=R/'acceptance-audit-concurrency';out.mkdir(exist_ok=True)
data=out/('run-'+uuid.uuid4().hex);shutil.copytree(fixture['data_root'],data);service=Service(data)
pid=fixture['project_id'];state=service.load(pid);before=set((data/pid/'audits').glob('*'));verify=service.verify

def verify_and_edit(request):
    result=verify(request)
    s=service.load(pid)
    service.conditions_set(OperatingConditions(project_id=pid,expected_revision=s['revision'],idempotency_key=uuid.uuid4().hex,**s['operating_conditions']))
    return result

with patch.object(service,'verify',side_effect=verify_and_edit):
    try:
        service.design_audit(AuditRequest(project_id=pid,revision=state['revision'],pcb_build_id=fixture['pcb_build_id']))
        raise AssertionError('Mixed revision audit was published')
    except DomainError as e:
        assert e.payload['code']=='REVISION_CONFLICT',e.payload
assert set((data/pid/'audits').glob('*'))==before
result=dict(passed=True,checks=['Actual check then injected edit changes revision; subsequent audit fails REVISION_CONFLICT','No mixed-revision audit files published'],actual_gerber_generated=False)
(out/'report.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
