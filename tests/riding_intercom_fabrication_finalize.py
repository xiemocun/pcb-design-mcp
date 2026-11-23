"""Finish vendor ZIP only after existing independent human signature validates."""
import asyncio,json,os,sys,hashlib,zipfile,shutil
from pathlib import Path
from datetime import timedelta
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from riding_intercom_circuit import ROOT,PID
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
async def main():
 r=json.loads((ROOT/'hardware-pcb-10/manufacturing_review_prepare.json').read_text())['structuredContent']['data'];ref=dict(project_id=PID,review_id=r['review_id'])
 server=StdioServerParameters(command=sys.executable,args=['-m','pcb_mcp.server'],env={**os.environ,'PCB_MCP_DATA_DIR':str(ROOT/'data')})
 async with stdio_client(server) as (reader,writer),ClientSession(reader,writer) as session:
  await session.initialize()
  async def call(name,args):
   result=await session.call_tool(name,{'request':args},read_timeout_seconds=timedelta(seconds=300));(ROOT/'hardware-pcb-10'/(name+'.json')).write_text(result.model_dump_json(indent=2))
   if result.isError:raise RuntimeError(str(result)[:2000])
   return result.structuredContent['data']
  status=await call('manufacturing_review_status',ref)
  if status['status']!='approved':print('PENDING:',status['status'],'Independent human review is required. No Gerber exported.');return
  fabrication=await call('fabrication_export',ref)
  assert fabrication['manifest']['real_human_approval'] is True
  review=Path(r['review_path']).parent;out=ROOT/'vendor-review';dest=out/'fabrication';shutil.copytree(review/'fabrication',dest,dirs_exist_ok=True)
  for name in ['fabrication_manifest.json','approval_payload.json','approval.json']:
   shutil.copy2(review/name,out/name)
  (out/'README.txt').write_text('骑行对讲机原型 PCBA / 版本626\n\n本包包含制板Gerber/钻孔、19处填孔盖铜坐标、完整BOM/CPL、装配图、中文制造说明、可移植KiCad工程、原理图及两台固件。\n先阅读《制造与装配说明.pdf》。生产所有工序必须使用同包文件，禁止混用历史版本。\n电池、扬声器、外置天线与外壳按说明另配；本包制造对象为90×65mm主板。\npositions.csv为X右Y上；filled-capped-holes.csv为X右Y下。\n仅导出固定版本的工程样机文件，没有代下单。城市1公里、骑行风噪/回声、最大音量和温升等待实物验证。\n')
  names=[]
  for p in sorted(out.rglob('*')):
   if not p.is_file() or p.name in ['package-manifest.json'] or p.suffix in ['.log','.png'] or p.name in ['schematic-positions.json']:continue
   names.append(p)
  manifest=dict(project_id=PID,revision=626,pcb_build_id=json.loads((review/'approval_payload.json').read_text())['pcb_build_id'],review_id=r['review_id'],scope='engineering_prototype_PCB_fabrication_and_assembly_files',human_file_export_approved=True,ordered=False,physical_verified=False,files={str(p.relative_to(out)):sha(p) for p in names})
  (out/'package-manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False));names.append(out/'package-manifest.json')
  archive=ROOT/'riding-intercom-PCBA-r626.zip'
  with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
   for p in names:z.write(p,p.relative_to(out))
  with zipfile.ZipFile(archive) as z:
   assert z.testzip() is None
   for name,digest in manifest['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==digest
   assert len([n for n in z.namelist() if n.startswith('fabrication/') and n.endswith('.gbr')])==9
   assert any(n.startswith('fabrication/') and n.endswith('.drl') for n in z.namelist())
  (ROOT/'hardware-pcb-10/final-package.json').write_text(json.dumps(dict(path=str(archive),sha256=sha(archive),bytes=archive.stat().st_size,files=len(names),manufacturing_files_present=True,physical_verified=False),indent=2));print('COMPLETE',archive,sha(archive),flush=True)
if __name__=='__main__':asyncio.run(main())
