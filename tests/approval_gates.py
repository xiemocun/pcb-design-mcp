"""CryptoKit/Python interoperability and gates. NO real Gerber backend invocation."""
import base64,copy,json,shutil,subprocess,uuid,time
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pcb_mcp import approval,backend,parts
from pcb_mcp.service import Service
from pcb_mcp.common import DomainError
from pcb_mcp.models import ReviewRef,ReviewPrepare,OperatingConditions
ROOT=Path(__file__).resolve().parents[1]
report=json.loads((ROOT/'acceptance-parts/report.json').read_text());out=ROOT/'acceptance-approval';out.mkdir(exist_ok=True);data=out/('run-'+uuid.uuid4().hex);shutil.copytree(report['data_root'],data)
service=Service(data);fresh=service.review_prepare(ReviewPrepare(project_id=report['project_id'],pcb_build_id=report['pcb_build_id']));request=ReviewRef(project_id=report['project_id'],review_id=fresh['review_id']);s,row,folder,payload=service.review_target(request);checks=[];backend_calls=[]
class ReachedAuthorizedDispatch(Exception):pass

def stop_backend(args,folder):
 backend_calls.append(args)
 raise ReachedAuthorizedDispatch('Stopped BEFORE real Gerber generation')

def reject(code,fn):
 before=len(backend_calls)
 try:fn();raise AssertionError('Expected '+code)
 except DomainError as e:assert e.payload['code']==code,e.payload
 assert len(backend_calls)==before
 checks.append(code)

subprocess.run(['xcrun','swift','-module-cache-path','/private/tmp/pcb-swift-cache',str(ROOT/'tests/signature_fixture.swift'),str(folder)],check=True,capture_output=True,text=True,timeout=45)
receipt=json.loads((folder/'approval.json').read_text());path=folder/'approval.json'
with patch.object(approval,'AUTHORITY',folder/'test-authority.json'),patch.object(backend,'command',side_effect=stop_backend):
 assert service.review_status(request)['status']=='approved';checks.append('Independent Swift CryptoKit DER/P256/SHA256 receipt accepted by Python cryptography; software TEST identity only')
 path.unlink();reject('HUMAN_APPROVAL_REQUIRED',lambda:service.fabrication_export(request));path.write_text(json.dumps(receipt))
 bad=copy.deepcopy(receipt);bad['envelope']['payload_sha256']='0'*64;path.write_text(json.dumps(bad));reject('APPROVAL_SCOPE_MISMATCH',lambda:service.fabrication_export(request))
 bad=copy.deepcopy(receipt);bad['envelope']['review_id']='0'*32;path.write_text(json.dumps(bad));reject('APPROVAL_SCOPE_MISMATCH',lambda:service.fabrication_export(request))
 bad=copy.deepcopy(receipt);bad['signature_der_base64']=base64.b64encode(b'not a signature').decode();path.write_text(json.dumps(bad));reject('APPROVAL_SIGNATURE_INVALID',lambda:service.fabrication_export(request))
 bad=copy.deepcopy(receipt);bad['envelope']['signed_at']+=100000;path.write_text(json.dumps(bad));reject('APPROVAL_TIME_INVALID',lambda:service.fabrication_export(request))
 path.write_text(json.dumps(receipt))
 original=(folder/'review.html').read_bytes();(folder/'review.html').write_bytes(original+b'\n');reject('REVIEW_CHANGED',lambda:service.fabrication_export(request));(folder/'review.html').write_bytes(original)
 with patch('pcb_mcp.approval.time.time',return_value=payload['expires_at']+1):reject('APPROVAL_EXPIRED',lambda:service.fabrication_export(request))
 with patch.object(parts,'rules_hash',return_value='changed'):reject('ENGINEERING_EVIDENCE_CHANGED',lambda:service.fabrication_export(request))
 try:service.fabrication_export(request);raise AssertionError('Expected dispatch sentinel')
 except ReachedAuthorizedDispatch:pass
 assert len(backend_calls)==1 and backend_calls[0][:3]==['pcb','export','gerbers']
 (folder/'rejected.json').write_text('{}');reject('REVIEW_REJECTED',lambda:service.fabrication_export(request));assert service.review_status(request)['status']=='rejected';(folder/'rejected.json').unlink()
 assert not list((folder/'fabrication').iterdir());checks.append('Valid TEST receipt reaches gated dispatch, deliberately stopped before creating ANY Gerber or drill file')
 s=service.load(request.project_id);cond=s['operating_conditions'];req=OperatingConditions(project_id=s['id'],expected_revision=s['revision'],idempotency_key=uuid.uuid4().hex,**cond);service.conditions_set(req)
 reject('STALE_REVIEW',lambda:service.fabrication_export(request))
result=dict(passed=True,checks=checks,real_gerber_backend_invocations=0,real_human_approval=False,test_scope='Software TEST key used only in isolated copied data; live authority unchanged; actual manufacturing export intentionally unrun without human signature')
(out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
