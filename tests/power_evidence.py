"""Changes to the curated source or pin identity fail closed in isolation."""
import copy,hashlib,json,sys,tempfile
from pathlib import Path
from unittest.mock import patch
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'src'))
from pcb_mcp import parts,electrical
report=json.loads((R/'acceptance-power-module/report.json').read_text())
s=json.loads((Path(report['data_root'])/report['project_id']/'state.json').read_text())
snapshot=R/'evidence-cache/r78k-extract.json';before=hashlib.sha256(snapshot.read_bytes()).hexdigest()
assert electrical.evaluate(s)['verdict']=='pass'
with tempfile.TemporaryDirectory(dir=R/'work') as tmp:
    root=Path(tmp);(root/'catalog').mkdir();(root/'evidence-cache').mkdir()
    (root/'catalog/parts.json').write_bytes((R/'catalog/parts.json').read_bytes())
    # Other catalog records are irrelevant to this source-specific countercase.
    copied=root/'evidence-cache'/snapshot.name;copied.write_bytes(snapshot.read_bytes()+b'\n')
    with patch.object(parts,'ROOT',root):
        candidate=next(p for p in parts.catalog() if p['symbol_id']=='PCB_MCP:R78K3V3_1A')
        assert candidate['evidence_status']=='missing_or_changed'
        bad=electrical.evaluate(s)
        assert bad['verdict']=='fail' and 'BUCK_SOURCE_CHANGED' in {f['code'] for f in bad['findings']}
bad_state=copy.deepcopy(s);c=next(c for c in bad_state['components'].values() if c['library_id']=='PCB_MCP:R78K3V3_1A')
for p in c['pins']:
    if p['number'] in ['1','3']:p['name']='OUT' if p['number']=='1' else 'IN'
bad=electrical.evaluate(bad_state)
assert bad['verdict']=='fail' and 'BUCK_PIN_SOURCE_CONFLICT' in {f['code'] for f in bad['findings']}
assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==before
result=dict(passed=True,checks=['Changed source extract is unavailable and rejected by nominal validation','Pin-map mutation is rejected against primary-source identity','Original evidence snapshot unchanged'],actual_manufacturing_approval=False)
(R/'acceptance-power-evidence.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
