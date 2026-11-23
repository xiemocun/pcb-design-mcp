"""Final ZIP must contain the exact independently approved PCB/BOM/CPL."""
from pathlib import Path
import json,zipfile,hashlib,csv,io
R=Path('work/riding-intercom');O=R/'cost-down';info=json.loads((O/'pcb-01/final-package.json').read_text());p=Path(info['path']);sha=lambda b:hashlib.sha256(b).hexdigest()
assert sha(p.read_bytes())==info['sha256']
with zipfile.ZipFile(p) as z:
 assert z.testzip() is None
 manifest=json.loads(z.read('package-manifest.json'));payload=json.loads(z.read('approval_payload.json'));assert manifest['revision']==payload['revision']==27
 assert manifest['review_id']==payload['review_id']=='b218981dac064627b4877ff6496d1e98'
 for name,h in manifest['files'].items():assert sha(z.read(name))==h,name
 for archive,signed in [('CAD/PCB/routed.kicad_pcb','pcb/routed.kicad_pcb'),('CAD/PCB/routed.kicad_pro','pcb/routed.kicad_pro'),('CAD/PCB/routed.kicad_dru','pcb/routed.kicad_dru'),('bom.csv','bom.csv'),('bom-grouped.csv','bom-grouped.csv'),('positions.csv','positions.csv'),('assembly-process.json','assembly-process.json'),('assembly-geometry.json','assembly-geometry.json'),('filled-capped-holes.csv','filled-capped-holes.csv')]:assert sha(z.read(archive))==payload['files'][signed],(archive,signed)
 process=json.loads(z.read('assembly-process.json'));assert process['filled_capped_hole_count']==0
 for label,count in [('SMT',84),('THT',7)]:assert len(list(csv.DictReader(io.StringIO(z.read('positions-'+label+'.csv').decode()))))==count
 validation=json.loads(z.read('fabrication-validation.json'));assert validation['passed'] and validation['drill_holes']==213 and validation['exported_module_mask_has_exactly_nine_native_windows']
 assert sha((R/'riding-intercom-PCBA-r626.zip').read_bytes())=='9b50cf774ee7619a4294f7f0ed7e3586044af41208554373e770ad0a302d56b3'
result=dict(passed=True,zip_sha256=info['sha256'],archive=str(p),files=len(manifest['files'])+1,revision=27,approved_PCB_BOM_CPL_and_process_bytes_match=True,original_r626_archive_preserved=True,physical_verified=False)
(O/'release-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
