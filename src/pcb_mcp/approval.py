"""Verify receipts from an independently configured human approval authority."""
import base64,json,os,time
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature
from .common import require
from . import backend
from .config import user_root
ROOT=user_root()
AUTHORITY=Path(os.environ.get('PCB_MCP_APPROVAL_AUTHORITY',str(ROOT/'.approval-authority/public.json')))
DOMAIN=b'PCB-MCP-FABRICATION-APPROVAL-v1\n'

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()

def authority():
    require(AUTHORITY.is_file(),'APPROVAL_AUTHORITY_UNCONFIGURED','尚未建立本机真人审批身份；MCP 没有初始化或替换审批密钥的工具。')
    value=json.loads(AUTHORITY.read_text());require(value.get('algorithm')=='P256_SHA256','APPROVAL_AUTHORITY_INVALID','审批身份算法不正确。')
    raw=base64.b64decode(value['public_key_x963_base64'],validate=True);require(backend.digest(raw)==value.get('key_id'),'APPROVAL_AUTHORITY_INVALID','审批公钥身份不一致。')
    return value,ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),raw)

def verify(folder,payload,sha):
    value,key=authority();path=folder/'approval.json'
    require(path.is_file(),'HUMAN_APPROVAL_REQUIRED','需要在独立本机审批界面审阅并经系统身份验证签字；模型参数不能代替签字。')
    receipt=json.loads(path.read_text());require(set(receipt)=={'envelope','signature_der_base64'},'APPROVAL_INVALID','审批回执结构无效。')
    e=receipt['envelope'];require(isinstance(e,dict) and set(e)=={'action','key_id','payload_sha256','review_id','signed_at'},'APPROVAL_INVALID','审批签署对象不完整。')
    require(e['action']=='export_gerber' and e['review_id']==payload['review_id'] and e['payload_sha256']==sha and e['key_id']==value['key_id'],'APPROVAL_SCOPE_MISMATCH','回执不属于当前固定版本、用途或审批身份。')
    if payload.get('authority_key_id'):require(payload['authority_key_id']==value['key_id'],'APPROVAL_AUTHORITY_CHANGED','审批身份已变更，需重新准备审阅包。')
    require(type(e['signed_at']) is int and payload['created_at']<=e['signed_at']<=int(time.time())+30 and e['signed_at']<=payload['expires_at'],'APPROVAL_TIME_INVALID','签署时间无效。')
    require(int(time.time())<=payload['expires_at'],'APPROVAL_EXPIRED','本次审批已过期。')
    try:key.verify(base64.b64decode(receipt['signature_der_base64'],validate=True),DOMAIN+canonical(e),ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature,ValueError) as exc:
        from .common import DomainError
        raise DomainError('APPROVAL_SIGNATURE_INVALID','审批签名无法验证。') from exc
    return receipt
