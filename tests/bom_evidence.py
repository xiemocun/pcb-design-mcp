"""Source freshness, missing ratings, completeness and revision binding."""
import copy,json,sys,uuid,shutil
from pathlib import Path
from unittest.mock import patch
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp.service import Service
from pcb_mcp.models import Read,BuildRef,PassiveRatings
from pcb_mcp import parts
from pcb_mcp.editing import spec_hash
from pcb_mcp.common import DomainError
r=json.loads((R/'acceptance-bom/report.json').read_text());out=R/'acceptance-bom-evidence';out.mkdir(exist_ok=True);root=out/('run-'+uuid.uuid4().hex)
shutil.copytree(Path(r['data_root'])/r['project_id'],root/r['project_id']);service=Service(root);s=service.load(r['project_id']);q=Read(project_id=s['id']);cap=next(c for c in s['components'].values() if c['ref']=='C1');p=s['part_bindings'][cap['id']]
service.target(BuildRef(project_id=s['id'],build_id=r['schematic_build_id']))
evidence_root=out/('evidence-'+uuid.uuid4().hex);evidence_root.mkdir()
shutil.copytree(R/'catalog',evidence_root/'catalog');shutil.copytree(R/'evidence-cache',evidence_root/'evidence-cache')
# Module root is process-local: deliberate source corruption never touches the
# installed service or evidence used by another client/test process.
parts.ROOT=evidence_root
snapshot=evidence_root/'evidence-cache'/p['sources'][0]['local_snapshot_file'];before=snapshot.read_bytes()
try:
 snapshot.write_bytes(before+b'\n')
 report=service.bom_check(q);assert report['identity_verdict']=='fail' and report['verdict']=='fail'
 assert any(f['code']=='PART_EVIDENCE_CHANGED' and f.get('reference')=='C1' for f in report['findings'])
 assert service.validate(q)['verdict']=='fail'
 try:service.target(BuildRef(project_id=s['id'],build_id=r['schematic_build_id']));raise AssertionError('Changed part source accepted')
 except DomainError as e:assert e.payload['code']=='RULEPACK_CHANGED'
finally:snapshot.write_bytes(before)
assert service.bom_check(q)['counts']['identity_checked']==30
fresh=parts.catalog();unknown=copy.deepcopy(s)
for part in fresh:
 if part['candidate_id']==p['candidate_id']:
  del part['facts']['voltage_rating_v'];unknown['part_bindings'][cap['id']]=copy.deepcopy(part)
with patch('pcb_mcp.parts.catalog',return_value=fresh):
 report=service.bom_check_state(unknown);row=next(x for x in report['rows'] if x['reference']=='C1')
 assert row['identity_status']=='checked' and row['rating_status']=='incomplete' and report['declared_rating_screen']=='incomplete'
 assert any(f['code']=='PASSIVE_RATING_UNKNOWN' and f.get('reference')=='C1' for f in report['findings'])
changed=copy.deepcopy(s);changed['passive_rating_requirements']['requirements'][0]['max_applied_voltage_v']=6.0
assert spec_hash(changed)!=spec_hash(s)
wrong=copy.deepcopy(s);wrong['components'][cap['id']]['value']='100nF';assert service.bom_check_state(wrong)['identity_verdict']=='fail'
wrong=copy.deepcopy(s);wrong['footprint_bindings'][cap['id']]['footprint_id']='Capacitor_SMD:C_0805_2012Metric';assert service.bom_check_state(wrong)['identity_verdict']=='fail'
orphan=copy.deepcopy(s);orphan['part_bindings']['0'*32]=copy.deepcopy(p);assert service.bom_check_state(orphan)['identity_verdict']=='fail'
# A one-component BOM is complete as an inventory, not a design. No invented SKU.
single=copy.deepcopy(s);single['components']={cap['id']:cap};single['part_bindings']={cap['id']:p};single['passive_rating_requirements']={}
ledger={p['candidate_id']:dict(definition_hash=p['definition_hash'],resolved=True)};(root/'part_candidates.json').write_text(json.dumps(ledger))
rows=service.bom_state(single);assert len(rows)==1 and rows[0]['supplier_sku']=='' and rows[0]['supplier_selection']=='not_selected'
assert service.engineering_state(single)['verdict']!='pass'
report=dict(passed=True,checks=['Changed primary snapshot -> identity/spec/build rejected; original bytes restored','Missing voltage fact -> unknown, never a rating pass','Declared requirements enter specification hash','Changed capacitor value and orphan binding rejected','Complete inventory with absent supplier leaves SKU blank, engineering still unsupported'],new_gerber_generated=False,private_keys_accessed=False)
(R/'acceptance-bom-evidence.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
