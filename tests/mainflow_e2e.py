"""Real stdio MCP + KiCad/SKiDL/Freerouting. TEST Gerbers isolated from pending HUMAN reviews."""
import asyncio,base64,copy,hashlib,json,os,shutil,sys,time,uuid,zipfile
from pathlib import Path
from datetime import timedelta
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes,serialization
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
R=Path(__file__).resolve().parents[1];W=R.parents[1]/'work/mainflow-inplace';sys.path.insert(0,str(R/'src'))
from pcb_mcp import approval,parts
SOURCE='7b1ac6b23fd14e1ba887b4bc2cd25a0a'
DECL='仅申请固定版本原型文件导出；接受审阅中逐项列明的未验证实物、铜线压降、动态、复位、射频与功能事项；不代表下单或生产资格。'
LIVE_AUTH=R.parent/'pcb-design-mcp-v05/.approval-authority/public.json'

async def main():
 W.mkdir(exist_ok=True,parents=True);run=W/('e2e-'+uuid.uuid4().hex);run.mkdir();(W/'latest-run.txt').write_text(str(run))
 transcript=[];completed=[];start=time.monotonic();frozen=parts.rules_hash()
 baseline=json.loads((W/'baseline/old-state-hashes.json').read_text())
 def save():
  (run/'transcript.json').write_text(json.dumps(transcript,ensure_ascii=False,indent=2))
  (run/'progress.json').write_text(json.dumps(dict(completed=completed,elapsed_seconds=round(time.monotonic()-start,2)),ensure_ascii=False,indent=2))
 def params(data,auth):return StdioServerParameters(command=sys.executable,args=[str(R/'run_server.py')],env={**os.environ,'PCB_MCP_DATA_DIR':str(data),'PCB_MCP_APPROVAL_AUTHORITY':str(auth)})
 async def connect_flow(session,data,test_key=None):
  async def call(name,args=None,error=None):
   t=time.monotonic();print(name,'START',flush=True)
   result=await session.call_tool(name,{} if args is None else {'request':args},read_timeout_seconds=timedelta(seconds=240))
   item=dict(tool=name,arguments=args,elapsed_seconds=round(time.monotonic()-t,3),result=result.model_dump(mode='json'));transcript.append(item);save()
   if error:
    assert result.isError and error in '\n'.join(x.text for x in result.content if hasattr(x,'text')),(name,error,result)
    print(name,error,item['elapsed_seconds'],flush=True);return
   assert not result.isError,(name,result)
   print(name,'OK',item['elapsed_seconds'],flush=True);return result.structuredContent['data']
  if test_key is not None:
   for row in completed:
    pt=dict(project_id=row['project_id'],pcb_build_id=row['pcb_build_id'])
    review=await call('manufacturing_review_prepare',dict(**pt,qualification_declaration=DECL))
    folder=Path(review['review_path']).parent;raw=(folder/'approval_payload.json').read_bytes();payload=json.loads(raw)
    auth=json.loads((data/'TEST-AUTHORITY.json').read_text())
    envelope=dict(action='export_gerber',key_id=auth['key_id'],payload_sha256=hashlib.sha256(raw).hexdigest(),review_id=review['review_id'],signed_at=int(time.time()))
    receipt=dict(envelope=envelope,signature_der_base64=base64.b64encode(test_key.sign(approval.DOMAIN+approval.canonical(envelope),ec.ECDSA(hashes.SHA256()))).decode())
    (folder/'approval.json').write_text(json.dumps(receipt))
    ref=dict(project_id=row['project_id'],review_id=review['review_id'])
    assert (await call('manufacturing_review_status',ref))['status']=='approved'
    # Actual tamper gate before real command dispatch.
    original=(folder/'pcb/routed.kicad_pcb').read_bytes();(folder/'pcb/routed.kicad_pcb').write_bytes(original+b'\n')
    await call('fabrication_export',ref,'REVIEW_CHANGED');(folder/'pcb/routed.kicad_pcb').write_bytes(original)
    exported=await call('fabrication_export',ref);manifest=exported['manifest']
    assert manifest['approval_authority_kind']=='software_test_fixture' and not manifest['real_human_approval'] and not manifest['ordered']
    with zipfile.ZipFile(exported['bundle_path']) as z:
     assert len([n for n in z.namelist() if n.endswith('.gbr')])==9 and any(n.endswith('.drl') for n in z.namelist())
     assert 'fabrication/TEST_ONLY.txt' in z.namelist()
    row['isolated_test_export']=exported
    assert await call('fabrication_export',ref)==exported
   return
  tools=await session.list_tools();required={'project_fork','component_add','pcb_autoroute','drc_run','manufacturing_review_prepare','manufacturing_review_status','fabrication_export'};assert required<={x.name for x in tools.tools}
  caps=await call('system_capabilities');assert caps['contract_version']=='0.16.1' and caps['features']['freerouting']
  for count in [32,33]:
   source=await call('project_read',dict(project_id=SOURCE))
   fork=await call('project_fork',dict(project_id=SOURCE,expected_revision=source['revision'],name=f'原型流程验证 {count} 元件',idempotency_key=uuid.uuid4().hex));pid=fork['project_id'];rev=0
   def m(**kw):return dict(project_id=pid,expected_revision=rev,idempotency_key=uuid.uuid4().hex,**kw)
   if count==33:
    c=next(c for c in source['components'].values() if c['ref']=='C4')
    await call('library_get',dict(candidate_id=c['candidate_id']))
    added=await call('component_add',m(candidate_id=c['candidate_id'],ref='C14',value=c['value']));rev=added['revision'];cid=added['component_id']
    p=source['part_bindings'][c['id']];fs=await call('footprint_search',dict(query=p['footprint_id']));assert fs['total']==1
    fp=await call('footprint_get',dict(candidate_id=fs['candidates'][0]['candidate_id']))
    rev=(await call('footprint_bind',m(component_id=cid,candidate_id=fp['candidate_id'],reason='Add real source-linked 100nF decoupler, actual native geometry and fresh DRC required')))['revision']
    ps=await call('parts_search',dict(query=p['mpn']));assert ps['total']==1
    part=await call('part_get',dict(candidate_id=ps['candidates'][0]['candidate_id']))
    rev=(await call('part_bind',m(component_id=cid,candidate_id=part['candidate_id'])))['revision']
    for pin in c['pins']:
     n=next(n for n in source['nets'].values() if dict(component_id=c['id'],pin_id=pin['pin_id']) in n['pins'])
     newpin=dict(component_id=cid,pin_id=next(x['pin_id'] for x in added['pins'] if x['number']==pin['number']))
     rev=(await call('net_members_set',m(net_id=n['id'],pins=n['pins']+[newpin])))['revision']
    ratings=copy.deepcopy(source['passive_rating_requirements']);req=copy.deepcopy(next(x for x in ratings['requirements'] if x['component_id']==c['id']));req['component_id']=cid;ratings['requirements'].append(req)
    rev=(await call('passive_ratings_set',m(**ratings)))['revision']
   assert (await call('spec_validate',dict(project_id=pid)))['verdict']=='pass'
   assert (await call('bom_check',dict(project_id=pid)))['counts']['identity_checked']==count
   sch=await call('schematic_build',m());st=dict(project_id=pid,build_id=sch['build_id'])
   assert (await call('schematic_verify',st))['verdict']=='pass'
   erc=await call('erc_run',st);assert erc['verdict']=='pass' and not erc['findings']
   await call('schematic_preview',st);await call('schematic_export',st)
   pcb=await call('pcb_build',m(schematic_build_id=sch['build_id']));pt=dict(project_id=pid,pcb_build_id=pcb['pcb_build_id'])
   v=await call('pcb_verify',dict(**pt,stage='unrouted'));assert v['verdict']=='pass' and len(v['details']['actual'])==count
   assert (await call('pcb_layout_check',dict(**pt,stage='unrouted')))['verdict']=='pass'
   await call('pcb_autoroute',pt)
   assert (await call('pcb_verify',pt))['verdict']=='pass'
   drc=await call('drc_run',pt);assert drc['verdict']=='pass',drc
   assert (await call('pcb_layout_check',pt))['verdict']=='pass'
   copper=await call('pcb_copper_check',pt);assert copper['geometry_verdict']=='pass' and not any(f['severity']=='error' for f in copper['findings'])
   preview=await call('pcb_preview',pt);await call('pcb_review_export',pt)
   ready=await call('design_readiness',dict(project_id=pid));assert ready['status']=='ready_for_human_review' and not ready['manufacturing_approved'],ready
   await call('manufacturing_review_prepare',pt,'PROTOTYPE_DECLARATION_REQUIRED')
   review=await call('manufacturing_review_prepare',dict(**pt,qualification_declaration=DECL))
   ref=dict(project_id=pid,review_id=review['review_id'])
   assert (await call('manufacturing_review_status',ref))['status']=='pending_human_signature'
   await call('fabrication_export',ref,'HUMAN_APPROVAL_REQUIRED')
   completed.append(dict(component_count=count,project_id=pid,revision=rev,schematic_build_id=sch['build_id'],pcb_build_id=pcb['pcb_build_id'],preview=preview,review=review,ready=ready,erc_findings=len(erc['findings']),drc_findings=len(drc['findings']),copper_warnings=copper['findings']));save()
 async with stdio_client(params(R/'data',LIVE_AUTH)) as (r,w):
  async with ClientSession(r,w) as session:await session.initialize();await connect_flow(session,R/'data')
 # Only the two fresh projects are copied. No old snapshots, environments, or human private keys.
 testdata=run/'ISOLATED-TEST-DATA';testdata.mkdir()
 for row in completed:shutil.copytree(R/'data'/row['project_id'],testdata/row['project_id'])
 for name in ['part_candidates.json','footprint_candidates.json']:shutil.copy2(R/'data'/name,testdata/name)
 key=ec.generate_private_key(ec.SECP256R1());public=key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
 (testdata/'TEST-AUTHORITY.json').write_text(json.dumps(dict(algorithm='P256_SHA256',key_id=hashlib.sha256(public).hexdigest(),public_key_x963_base64=base64.b64encode(public).decode(),test_fixture_only=True)))
 async with stdio_client(params(testdata,testdata/'TEST-AUTHORITY.json')) as (r,w):
  async with ClientSession(r,w) as session:await session.initialize();await connect_flow(session,testdata,test_key=key)
 assert parts.rules_hash()==frozen,'Implementation changed during acceptance; results stale'
 for name,sha in baseline.items():assert hashlib.sha256((R.parents[1]/name).read_bytes()).hexdigest()==sha,name
 result=dict(passed=True,contract_version='0.16.1',source_evidence_hash=frozen,elapsed_seconds=round(time.monotonic()-start,2),call_count=len(transcript),old_projects_preserved=len(baseline),boards=completed,actual_test_gerbers_generated=True,real_human_approval=False,real_reviews_pending=True,ordered=False,scope='Real native backend E2E with isolated software TEST exports; live review signatures remain absent')
 (run/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));(W/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='boards'},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':asyncio.run(main())
