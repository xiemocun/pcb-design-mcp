"""Materialize data-only source packages from local, bounded asset references."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def bind_geometry(raw):
    """Resolve missing hashes from the supplied or installed real libraries."""
    from pcb_mcp import backend, footprints
    with tempfile.TemporaryDirectory(prefix='pcb-package-') as directory:
        stage=Path(directory);symbols={}
        for asset in raw.get('assets',[]):
            if asset['kind'] not in ('symbol','footprint'):continue
            relative=Path(asset['path'])
            if relative.is_absolute() or '..' in relative.parts:raise ValueError('INVALID_ASSET_PATH')
            target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(base64.b64decode(asset['content_base64'],validate=True))
            if asset['kind']=='symbol':
                stat=target.stat()
                symbols.update({s['library_id']:s for s in backend._catalog_library(str(target),stat.st_mtime_ns,stat.st_size)})
        for part in raw.get('parts',[]):
            if 'symbol_definition_hash' not in part:
                identity=part['symbol_id'];symbol=symbols.get(identity)
                if symbol is None:symbol=next((s for s in backend.catalog([identity.split(':',1)[0]]) if s['library_id']==identity),None)
                if not symbol or not symbol['supported']:raise ValueError('PACKAGE_SYMBOL_UNAVAILABLE: '+identity)
                part['symbol_definition_hash']=symbol['definition_hash']
            if 'footprint_definition_hash' not in part:
                identity=part['footprint_id'];library,name=identity.split(':',1)
                target=stage/'footprints'/(library+'.pretty')/(name+'.kicad_mod')
                entries=[(identity,target)] if target.is_file() else footprints.entries(identity,library=library)
                if len(entries)!=1:raise ValueError('PACKAGE_FOOTPRINT_UNAVAILABLE: '+identity)
                footprint=footprints.load(*entries[0])
                if not footprint['supported']:raise ValueError('PACKAGE_FOOTPRINT_UNSUPPORTED: '+identity)
                part['footprint_definition_hash']=footprint['definition_hash']


def materialize(path,kind):
    root=path.resolve().parent
    raw=json.loads(path.read_text())
    for asset in raw.get('assets',[]):
        if 'file' not in asset and 'files' not in asset:continue
        if 'content_base64' in asset:raise ValueError('ASSET_FILE_AND_CONTENT_CONFLICT')
        if 'file' in asset and 'files' in asset:raise ValueError('ASSET_FILE_LIST_CONFLICT')
        if 'files' in asset:
            names=asset.pop('files')
            if asset['kind']!='spice_model' or not isinstance(names,list) or not 1<=len(names)<=16 or not all(isinstance(n,str) for n in names):raise ValueError('INVALID_SPICE_SOURCE_LIST')
        else:names=[asset.pop('file')]
        chunks=[];sources=[]
        for name in names:
            source=(root/name).resolve()
            if not source.is_relative_to(root) or not source.is_file():raise ValueError('LOCAL_ASSET_OUTSIDE_PACKAGE')
            if source.stat().st_size>20_000_000:raise ValueError('LOCAL_ASSET_SIZE_LIMIT')
            chunk=source.read_bytes();chunks.append(chunk);sources.append(source)
        if len(chunks)>1 and any(not c.endswith(b'\n') for c in chunks[:-1]):raise ValueError('SPICE_SOURCE_REQUIRES_NEWLINE')
        content=b''.join(chunks)
        if len(content)>20_000_000:raise ValueError('LOCAL_ASSET_SIZE_LIMIT')
        actual=hashlib.sha256(content).hexdigest()
        if 'sha256' in asset and asset['sha256']!=actual:raise ValueError('LOCAL_ASSET_HASH_MISMATCH')
        if asset['kind']=='document' and Path(asset['path']).suffix.lower()=='.pdf':
            if not content.startswith(b'%PDF-') or b'%%EOF' not in content[-2048:]:raise ValueError('DOCUMENT_IS_NOT_COMPLETE_PDF')
            for tool,args in [('pdfinfo',[str(source)]),('pdftotext',['-layout',str(source),'-'])]:
                executable=shutil.which(tool)
                if not executable:raise ValueError('PDF_VALIDATOR_UNAVAILABLE: '+tool)
                result=subprocess.run([executable]+args,capture_output=True,timeout=20)
                if result.returncode or b'syntax error' in result.stderr.lower():raise ValueError('PDF_PARSER_REJECTED_DOCUMENT: '+tool)
        asset.update(sha256=actual,content_base64=base64.b64encode(content).decode('ascii'))
    if kind=='catalog':
        from pcb_mcp.catalog_import import CatalogPackage
        bind_geometry(raw)
        result=CatalogPackage.model_validate(raw)
    elif kind=='model':
        from pcb_mcp.verification_models import VerificationModel
        result=VerificationModel.model_validate(raw)
    else:raise ValueError('UNKNOWN_SOURCE_PACKAGE_KIND')
    return result.model_dump(mode='json')
