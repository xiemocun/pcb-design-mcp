"""New policy, input evidence and revision boundaries in isolated saved data."""
from pathlib import Path
import copy,json,shutil,sys,uuid
from unittest.mock import patch
from pydantic import ValidationError
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp.service import Service
from pcb_mcp.models import CopperRules,PCBRef,BoardConstraints,Members,RemoveNet
from pcb_mcp.common import DomainError
from pcb_mcp.editing import spec_hash
from pcb_mcp import parts
rep=json.loads((R/'acceptance-bom/report.json').read_text());out=R/'acceptance-copper-evidence';out.mkdir(exist_ok=True);data=out/('run-'+uuid.uuid4().hex);shutil.copytree(rep['data_root'],data);service=Service(data);pid=rep['project_id'];state=service.load(pid);checks=[]
def reject(code,fn):
 try:fn();raise AssertionError('Expected '+code)
 except DomainError as e:assert e.payload['code']==code,e.payload
 checks.append(code)
pt=PCBRef(project_id=pid,pcb_build_id=rep['pcb_build_id']);service.pcb_target(pt)
p=data/pid/'pcb_builds'/rep['pcb_build_id']/'routed.kicad_pcb';old=p.read_bytes();p.write_bytes(old+b'\n');reject('ARTIFACT_CHANGED',lambda:service.copper_check(pt));p.write_bytes(old)
# Curated source changes invalidate this new module's real saved build too.
rows=copy.deepcopy(parts.catalog());rows[0]['evidence_status']='missing_or_changed'
with patch.object(parts,'catalog',return_value=rows):reject('RULEPACK_CHANGED',lambda:service.pcb_target(pt))
service.pcb_target(pt)
policy=copy.deepcopy(state['pcb_copper_policy']);key=uuid.uuid4().hex
request=CopperRules(project_id=pid,expected_revision=state['revision'],idempotency_key=key,**policy)
a=service.copper_rules_set(request);assert service.copper_rules_set(request)==a
reject('STALE_VALIDATION',lambda:service.copper_check(pt))
reject('REVISION_CONFLICT',lambda:service.copper_rules_set(CopperRules(project_id=pid,expected_revision=state['revision'],idempotency_key=uuid.uuid4().hex,**policy)))
current=service.load(pid);oldhash=spec_hash(current);other=copy.deepcopy(current);other['pcb_copper_policy']['copper_thickness_um']=70.0;assert spec_hash(other)!=oldhash
bad=copy.deepcopy(policy);bad['rules'][0]['escape_design_current_a']=.05;bad['rules'][0]['escape_profile']='none'
reject('COPPER_RULE_INVALID',lambda:service.copper_rules_set(CopperRules(project_id=pid,expected_revision=current['revision'],idempotency_key=uuid.uuid4().hex,**bad)))
current['board_constraints']['track_width_mm']=.3;service.save(current)
reject('COPPER_RULE_INVALID',lambda:service.copper_rules_set(CopperRules(project_id=pid,expected_revision=current['revision'],idempotency_key=uuid.uuid4().hex,**policy)))
# Restore global constraint in this isolated test, then show active-rule net removal is blocked.
current['board_constraints']['track_width_mm']=.15;service.save(current);net=next(n for n in current['nets'].values() if n['name']=='VCC_AUX')
a=service.members(Members(project_id=pid,expected_revision=current['revision'],idempotency_key=uuid.uuid4().hex,net_id=net['id'],pins=[]))
reject('ENTITY_IN_USE',lambda:service.remove_net(RemoveNet(project_id=pid,expected_revision=a['revision'],idempotency_key=uuid.uuid4().hex,net_id=net['id'])))
result=dict(passed=True,checks=checks,idempotent_policy_retry=True,policy_in_spec_hash=True,accepted_original_data_unchanged=True,new_gerber_generated=False,real_human_approval=False)
(R/'acceptance-copper-evidence.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
