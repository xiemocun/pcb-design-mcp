"""Download only pinned manufacturer PDFs for this finite curated catalog."""
import json,hashlib,subprocess,urllib.parse
from pathlib import Path
ROOT=Path(__file__).resolve().parent
for p in json.loads((ROOT/'catalog/parts.json').read_text())['parts']:
 for s in p['sources']:
  if 'local_snapshot_file' not in s:continue
  if s.get('evidence_kind')=='primary_web_pdf_fact_extract_local_snapshot':
   path=ROOT/'evidence-cache'/s['local_snapshot_file']
   if not path.exists():
    path.parent.mkdir(exist_ok=True);path.write_bytes((ROOT/'catalog/evidence'/path.name).read_bytes())
   assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==s['snapshot_sha256'], 'Bundled fact extract changed; manual review required'
   continue
  assert urllib.parse.urlparse(s['url']).hostname in ['www.vishay.com','www.kingbrightusa.com','www.aosong.com','www.ti.com','gct.co','www.we-online.com','industrial.panasonic.com']
  folder=ROOT/'evidence-cache';folder.mkdir(exist_ok=True);path=folder/s['local_snapshot_file'];assert path.parent==folder
  if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==s['pdf_sha256']:continue
  pending=path.with_suffix('.pending');subprocess.run(['curl','--fail','--location','--show-error','--max-time','180','--output',str(pending),s['url']],check=True)
  if hashlib.sha256(pending.read_bytes()).hexdigest()!=s['pdf_sha256']:raise SystemExit('Manufacturer document changed or download corrupted. Manual catalog review required; no silent repinning.')
  pending.replace(path)
print('Pinned local manufacturer evidence is available. Connector record explicitly uses the recorded primary-PDF web extract, not a local PDF hash.')
