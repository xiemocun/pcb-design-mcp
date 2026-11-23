"""Isolated source/value failures and typed clearing, without modifying shared evidence."""
from pathlib import Path
from unittest.mock import patch
import copy,json,sys
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts
from pcb_mcp.service import Service
from pcb_mcp.models import PassiveRatings
from pydantic import ValidationError
report=json.loads((R/'acceptance-complete-bom/report.json').read_text());service=Service(R/'data');s=service.load(report['project_id']);cap=next(c for c in s['components'].values() if c['ref']=='C9');bound=s['part_bindings'][cap['id']];original=copy.deepcopy(s)
assert service.bom_check_state(s)['identity_verdict']=='pass'
# No shared evidence files are mutated; source failure is injected in this process.
rows=copy.deepcopy(parts.catalog());next(p for p in rows if p['candidate_id']==bound['candidate_id'])['evidence_status']='missing_or_changed'
with patch.object(parts,'catalog',return_value=rows):assert service.bom_check_state(s)['identity_verdict']=='fail'
rows=copy.deepcopy(parts.catalog());bad=copy.deepcopy(s)
for p in rows:
 if p['candidate_id']==bound['candidate_id']:
  p['facts'].pop('voltage_rating_v');bad['part_bindings'][cap['id']]=copy.deepcopy(p)
with patch.object(parts,'catalog',return_value=rows):
 v=service.bom_check_state(bad);assert v['declared_rating_screen']=='incomplete'
 assert any(x['code']=='PASSIVE_RATING_UNKNOWN' and x.get('reference')=='C9' for x in v['findings'])
# Pin numbers are an adapter convention. Missing mapping must never pass polarity.
rows=copy.deepcopy(parts.catalog());bad=copy.deepcopy(s)
for p in rows:
 if p['candidate_id']==bound['candidate_id']:
  p['facts']['pin_mapping']={};bad['part_bindings'][cap['id']]=copy.deepcopy(p)
with patch.object(parts,'catalog',return_value=rows):
 v=service.bom_check_state(bad);assert next(x for x in v['rows'] if x['reference']=='C9')['rating_status']=='incomplete'
assert s==original
try:PassiveRatings(project_id=s['id'],expected_revision=s['revision'],idempotency_key='invalid',requirements=[],origin='verified_measurement',reason='No fabricated origin allowed');raise AssertionError('Fabricated origin accepted')
except ValidationError:pass
result=dict(passed=True,checks=['Changed source status invalidates capacitor identity','Unknown voltage remains incomplete','Missing polarity map remains incomplete','Fabricated measured origin rejected','All fault injections process-local; shared data/evidence unchanged'],new_gerber_generated=False,manufacturing_approved=False)
(R/'acceptance-complete-evidence.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
