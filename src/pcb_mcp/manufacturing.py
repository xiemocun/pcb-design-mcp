"""Immutable review packs and signature-gated manufacturing file export."""
import csv,html,io,json,shutil,time,zipfile
from pathlib import Path
from . import approval,backend,parts,prototype_policy,copper
from .placement_rules import ESP,SENSOR,LDO,USB
from .common import require,new_id,write_json,DomainError
from .models import PCBRef,ReviewRef

class ManufacturingService:
    def _pcb_checks(self,b,folder):
        names=set(b['sources'])
        extra=[('layout_routed','pass')] if any(c['library_id']==ESP for c in json.loads((folder/'spec.json').read_text())['components'].values()) else []
        for name,verdict in [('verify_routed','pass'),('drc','pass'),('preview','produced')]+extra:
            check=b['checks'].get(name,{})
            require(check.get('verdict')==verdict,'PCB_VALIDATION_REQUIRED','缺少当前 PCB 的 '+name+' 检查。')
            for n,sha in check['files'].items():
                require(backend.digest((folder/n).read_bytes())==sha,'ARTIFACT_CHANGED','PCB 检查证据已改变。');names.add(n)
        return names

    def export_assessment(self,s,b,source):
        report=prototype_policy.assessment(self,s)
        require(report['verdict']=='eligible_for_prototype_review','PROTOTYPE_CHECKS_REQUIRED','当前已知错误或必要声明仍阻止原型文件审阅。',report=report)
        if s.get('pcb_copper_policy'):
            required={n['id'] for n in s['nets'].values() if n['kind'] in ('power','ground')}
            require(required<={r['net_id'] for r in s['pcb_copper_policy']['rules']},'COPPER_RULE_COVERAGE_REQUIRED','已设置铜线策略时，必须覆盖所有电源及地网络。')
            check=b['checks'].get('copper',{})
            require(bool(check.get('files')) and 'copper_geometry.json' in check['files'] and 'copper_report.json' in check['files'],'COPPER_VALIDATION_REQUIRED','已设置铜线规则，需运行当前实际 PCB 铜线检查。')
            for name,sha in check['files'].items():
                require((source/name).is_file() and backend.digest((source/name).read_bytes())==sha,'ARTIFACT_CHANGED','铜线检查证据已变化。')
            actual=json.loads((source/'copper_geometry.json').read_text())
            result=copper.evaluate(s,actual,b['id'],connectivity_verified=True)
            require(result['geometry_verdict']=='pass' and not any(f['severity']=='error' for f in result['findings']),'COPPER_VALIDATION_REQUIRED','当前线宽、受管引出线或声明载流参考筛选失败。',report=result)
            # Conservative whole-net track sums are warning-level screens in
            # copper.evaluate, not measured path-drop contradictions. Preserve
            # every such unresolved finding in the immutable signed review.
            report['reports']['copper']=result;report['findings'].extend(result['findings'])
        else:
            report['findings'].append(dict(code='COPPER_POLICY_UNDECLARED',severity='warning',message='未声明逐网络铜厚、电流和压降规则；只有全局几何 DRC，载流与压降未经验证。'))
        return report

    def review_prepare(self,request):
        with self.locked():
            s,b,source=self.pcb_target(request);names=self._pcb_checks(b,source)
            engineering=self.export_assessment(s,b,source)
            complex_board=s['profile']!='led_series_experiment'
            require(not complex_board or request.qualification_declaration is not None,'PROTOTYPE_DECLARATION_REQUIRED','需要明确声明本次仅审阅原型文件，尚有实物、铜线、动态和功能资格未验证；声明将交由独立真人签字。')
            if s.get('pcb_copper_policy'):names.update(b['checks']['copper']['files'])
            rows=self.bom_state(s);ident=new_id();folder=self.project_path(s['id'])/'reviews'/ident;folder.mkdir(parents=True)
            for name in sorted(names):
                require(not Path(name).is_absolute() and '..' not in Path(name).parts,'INVALID_ARTIFACT_PATH','生成证据路径无效。')
                dest=folder/'pcb'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,dest)
            write_json(folder/'bom.json',rows);write_json(folder/'engineering.json',engineering)
            output=io.StringIO();writer=csv.DictWriter(output,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows);(folder/'bom.csv').write_text(output.getvalue())
            from .assembly_files import prepare as prepare_assembly_files
            prepare_assembly_files(s,source,folder,rows)
            warnings=[f for f in engineering['findings'] if f['severity']=='warning']
            warnings.extend([dict(code='FABRICATOR_CAPABILITY_UNVERIFIED',severity='warning',message='尚未核验具体板厂的工艺、钻孔公差和叠层能力；导出不代表可以下单。'),dict(code='PROCUREMENT_NOT_VERIFIED',severity='warning',message='MPN/SKU 为有来源的目录记录，库存、价格、实物真伪和样品适配未核验。')])
            write_json(folder/'review_findings.json',warnings)
            envelope=s.get('power_envelope') or s.get('operating_conditions') or {}
            summary='PCB 原型文件导出审阅\n\n项目：'+s['name']+'\n版本：'+str(s['revision'])+'\nPCB：'+b['id']+'\n用途：prototype_files_only\n\n供电声明（非实测）：'+json.dumps(envelope,ensure_ascii=False,indent=2)+'\n\n申请声明：'+(request.qualification_declaration or 'LED 受限工程筛选，授权范围仅为固定版本的文件导出。')+'\n\nBOM\n'+'\n'.join(c['reference']+'：'+c['manufacturer']+' '+c['mpn']+'；'+c['footprint'] for c in rows)+'\n\n未解决事项\n'+'\n'.join(f['code']+'：'+f['message'] for f in warnings)+'\n\n同意仅授权从此固定版本导出 Gerber 与钻孔文件，不下单。实物、完整铜线/电源动态、热、射频及功能资格仍未验证。全部具体检查与原始诊断见 engineering.json；任何未知项均未改写为物理通过。'
            led=engineering['reports'].get('led_engineering')
            if led:
                summary+='\n\nLED 受限筛选计算（非实测）：'+json.dumps(led['calculations'],ensure_ascii=False,indent=2)
            (folder/'review_summary.txt').write_text(summary)
            svg=(source/next(iter(b['checks']['preview']['files']))).read_text();page='<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data: file:"><style>body{font:15px system-ui;margin:24px;color:#17202a}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}svg{width:100%;height:280px;background:#eee}h1{font-size:22px}</style><h1>待人工审阅：制造文件导出</h1><pre>'+html.escape(summary)+'</pre>'+svg
            page+='<h2>来源资料（点击会在浏览器中打开）</h2><ul>'+''.join('<li>'+html.escape(c['reference'])+'：<a href="'+html.escape(c['datasheet_url'],quote=True)+'">'+html.escape(c['manufacturer']+' '+c['mpn'])+'</a></li>' for c in rows)+'</ul>'
            (folder/'review.html').write_text(page)
            created=int(time.time());key_id=None
            if approval.AUTHORITY.is_file():key_id=approval.authority()[0]['key_id']
            hashes={str(p.relative_to(folder)):backend.digest(p.read_bytes()) for p in sorted(folder.rglob('*')) if p.is_file()}
            payload=dict(schema='pcb-human-review-1',action='export_gerber',review_id=ident,project_id=s['id'],revision=s['revision'],pcb_build_id=b['id'],created_at=created,expires_at=created+request.validity_hours*3600,authority_key_id=key_id,engineering_evidence_hash=engineering['evidence_hash'],purpose=request.purpose,qualification_declaration=request.qualification_declaration,physical_qualified=False,export_policy_hash=backend.digest(Path(__file__).read_bytes()),accepted_warning_codes=sorted(f['code'] for f in warnings),scope='files_only_no_order_no_production_qualification',files=hashes)
            (folder/'approval_payload.json').write_bytes(approval.canonical(payload));sha=backend.digest((folder/'approval_payload.json').read_bytes())
            s['manufacturing_reviews'][ident]=dict(id=ident,pcb_build_id=b['id'],revision=s['revision'],payload_sha256=sha,expires_at=payload['expires_at']);self.save(s)
            return self._review_data(s,s['manufacturing_reviews'][ident],folder,payload,'pending_human_signature' if key_id else 'authority_not_configured')

    def _review_data(self,s,row,folder,payload,status):
        from .config import reviewer_binary
        findings=json.loads((folder/'review_findings.json').read_text()) if (folder/'review_findings.json').is_file() else json.loads((folder/'engineering.json').read_text())['findings']
        return dict(project_id=s['id'],revision=s['revision'],review_id=row['id'],status=status,payload_sha256=row['payload_sha256'],review_path=str(folder/'review.html'),approval_command=[str(reviewer_binary()),'--review',str(folder),'--authority-dir',str(approval.AUTHORITY.parent)],expires_at=payload['expires_at'],findings=findings)

    def review_target(self,request):
        s=self.load(request.project_id);require(request.review_id in s['manufacturing_reviews'],'UNKNOWN_REVIEW','审阅不存在。');row=s['manufacturing_reviews'][request.review_id]
        require(row['revision']==s['revision'],'STALE_REVIEW','规格已变化，旧人工审阅失效。')
        _,b,source=self.pcb_target(PCBRef(project_id=s['id'],pcb_build_id=row['pcb_build_id']));self._pcb_checks(b,source)
        folder=self.project_path(s['id'])/'reviews'/row['id']
        require(not (folder/'rejected.json').exists(),'REVIEW_REJECTED','该审阅已被拒绝或撤回；需要重新准备审阅并重新签字。')
        raw=(folder/'approval_payload.json').read_bytes();require(backend.digest(raw)==row['payload_sha256'],'REVIEW_CHANGED','审阅签署对象被修改。');payload=json.loads(raw)
        require(payload['review_id']==row['id'] and payload['project_id']==s['id'] and payload['revision']==s['revision'] and payload['pcb_build_id']==b['id'] and payload['action']=='export_gerber','REVIEW_SCOPE_INVALID','审阅对象与构建不一致。')
        for name,sha in payload['files'].items():
            require(not Path(name).is_absolute() and '..' not in Path(name).parts,'INVALID_ARTIFACT_PATH','审阅文件路径无效。')
            require(backend.digest((folder/name).read_bytes())==sha,'REVIEW_CHANGED','审阅中展示的 PCB/BOM/检查/页面已变化。')
        require(payload['engineering_evidence_hash']==parts.rules_hash(),'ENGINEERING_EVIDENCE_CHANGED','工程检查实现或器件证据已变化。')
        require(payload.get('export_policy_hash')==backend.digest(Path(__file__).read_bytes()),'EXPORT_POLICY_CHANGED','导出操作实现已变化，需要按当前实现重新审阅。')
        require(payload.get('purpose')=='prototype_files_only' and payload.get('physical_qualified') is False,'REVIEW_SCOPE_INVALID','需要当前显式原型文件策略的审阅。')
        self.export_assessment(s,b,source)
        return s,row,folder,payload

    def review_status(self,request):
        with self.locked():
            state=self.load(request.project_id);item=state['manufacturing_reviews'].get(request.review_id)
            require(item is not None,'UNKNOWN_REVIEW','审阅不存在。')
            path=self.project_path(state['id'])/'reviews'/request.review_id
            if (path/'rejected.json').exists():return self._review_data(state,item,path,dict(expires_at=item['expires_at']),'rejected')
            s,row,folder,payload=self.review_target(request)
            if int(time.time())>payload['expires_at']:status='expired'
            elif not approval.AUTHORITY.is_file():status='authority_not_configured'
            elif not (folder/'approval.json').is_file():status='pending_human_signature'
            else:approval.verify(folder,payload,row['payload_sha256']);status='approved'
            return self._review_data(s,row,folder,payload,status)

    def fabrication_export(self,request):
        with self.locked():
            s,row,folder,payload=self.review_target(request)
            require(int(time.time())<=payload['expires_at'],'APPROVAL_EXPIRED','审批时限已过。')
            receipt=approval.verify(folder,payload,row['payload_sha256'])
            if row.get('fabrication'):
                old=row['fabrication'];path=folder/'fabrication_bundle.zip'
                require(path.is_file() and backend.digest(path.read_bytes())==old['sha256'],'ARTIFACT_CHANGED','已导出的制造包发生变化。');return old['result']
            output=folder/'fabrication'
            require(not output.exists(),'EXPORT_RETRY_REQUIRES_REVIEW','先前导出不完整；重新准备审阅，不能覆盖半成品。');output.mkdir()
            stage=folder/'fabrication_input';stage.mkdir()
            for name in ['routed.kicad_pcb','routed.kicad_pro','routed.kicad_dru','fp-lib-table']:
                path=folder/'pcb'/name
                if path.is_file():shutil.copy2(path,stage/name)
            require(backend.digest((stage/'routed.kicad_pcb').read_bytes())==payload['files']['pcb/routed.kicad_pcb'],'ARTIFACT_CHANGED','导出暂存板与批准版本不一致。')
            from .stackup import fabrication_layers
            export_layers=fabrication_layers(s['board_constraints'])
            run=backend.command(['pcb','export','gerbers','--layers',','.join(export_layers),'--no-protel-ext','-o','fabrication/','fabrication_input/routed.kicad_pcb'],folder)
            write_json(folder/'gerber_command.json',run);require(run['exit_code']==0,'GERBER_BACKEND_FAILED','KiCad Gerber 导出失败。')
            drill=backend.command(['pcb','export','drill','--format','excellon','--excellon-units','mm','-o','fabrication/','fabrication_input/routed.kicad_pcb'],folder)
            write_json(folder/'drill_command.json',drill);require(drill['exit_code']==0,'DRILL_BACKEND_FAILED','KiCad 钻孔导出失败。')
            # Re-read the signed source and reports after the backend call.
            self.review_target(request);approval.verify(folder,payload,row['payload_sha256'])
            require(backend.digest((stage/'routed.kicad_pcb').read_bytes())==payload['files']['pcb/routed.kicad_pcb'],'ARTIFACT_CHANGED','导出后暂存板已变化。')
            files=list(output.iterdir());gerbers=[p for p in files if p.suffix=='.gbr'];drills=[p for p in files if p.suffix=='.drl']
            require(len(gerbers)==len(export_layers) and drills and all(p.is_file() and p.stat().st_size>0 for p in gerbers+drills),'FABRICATION_FILES_INCOMPLETE','Gerber 层或钻孔文件不完整。')
            from .stackup import inspect_gerbers
            try:layer_report=inspect_gerbers(output,s['board_constraints'])
            except ValueError as error:require(False,'FABRICATION_LAYER_MISMATCH',str(error))
            write_json(folder/'gerber_layers.json',layer_report)
            test_identity=approval.authority()[0].get('test_fixture_only') is True
            if test_identity:
                (output/'TEST_ONLY.txt').write_text('AUTOMATED TEST AUTHORITY. No human approval. Not an approved fabrication order.\n')
                files=list(output.iterdir())
            manifest=dict(review_id=row['id'],project_id=s['id'],revision=s['revision'],payload_sha256=row['payload_sha256'],approval=receipt,scope=payload['scope'],approval_authority_kind='software_test_fixture' if test_identity else 'independent_human_authority',real_human_approval=not test_identity,qualification='test_export_no_human_approval' if test_identity else 'human_authorized_files_only_not_production_qualification',ordered=False,files={p.name:backend.digest(p.read_bytes()) for p in sorted(files) if p.is_file()})
            write_json(folder/'fabrication_manifest.json',manifest)
            with zipfile.ZipFile(folder/'fabrication_bundle.zip','w',zipfile.ZIP_DEFLATED) as z:
                for p in files:z.write(p,'fabrication/'+p.name)
                for name in ['fabrication_manifest.json','approval_payload.json','approval.json','bom.json','bom.csv','engineering.json','review_findings.json','review_summary.txt','gerber_command.json','gerber_layers.json','drill_command.json','positions.csv','positions_command.json','bom-grouped.csv','assembly-process.json','assembly-geometry.json','filled-capped-holes.csv']:z.write(folder/name,name)
            result=dict(review_id=row['id'],revision=s['revision'],bundle_path=str(folder/'fabrication_bundle.zip'),manifest=manifest)
            row['fabrication']=dict(sha256=backend.digest((folder/'fabrication_bundle.zip').read_bytes()),result=result);self.save(s);return result
