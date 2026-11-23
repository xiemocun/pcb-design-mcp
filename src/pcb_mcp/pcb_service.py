"""Typed, hash-bound physical design experiments using real KiCad + Freerouting."""
import copy,csv,io,json,os,re,subprocess,zipfile,shutil
from pathlib import Path
from . import backend,footprints
from .placement_rules import ESP,reference_plan
from .common import DomainError,PCB_REVIEW_RECEIPT,file_snapshot,require,new_id,request_fingerprint,write_json
from .models import BuildRef
from .config import asset_root,java_path,ROUTER_VERSION,ROUTER_SHA256

PYTHON=Path(os.environ.get('PCB_MCP_PCB_PYTHON','/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3.9'))
ROUTER=Path(os.environ.get('PCB_MCP_ROUTER',str(asset_root()/'vendor'/('freerouting-'+ROUTER_VERSION+'.jar'))))
JAVA=java_path()


def java_runtime():
    """Probe a Java 25+ VM required by the pinned engine, rejecting launch stubs."""
    try:
        r=subprocess.run([str(JAVA),'-version'],capture_output=True,text=True,timeout=5)
        match=re.search(r'(?:openjdk|java) version "(\d+)',r.stdout+r.stderr)
        major=int(match.group(1)) if match else None
        return dict(available=r.returncode==0 and major is not None and major>=25,major=major)
    except (OSError,subprocess.TimeoutExpired):return dict(available=False,major=None)


def worker(action,folder,filename=None):
    args=[str(PYTHON),str(Path(__file__).with_name('pcb_worker.py')),action,str(folder)]
    if filename:args.append(filename)
    try:r=subprocess.run(args,cwd=folder,capture_output=True,text=True,timeout=45)
    except subprocess.TimeoutExpired as e:raise DomainError('PCB_BACKEND_TIMEOUT','KiCad PCB 后端超时。') from e
    write_json(folder/('worker_'+action+'.json'),dict(exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
    require(r.returncode==0,'PCB_BACKEND_FAILED','KiCad PCB 固定后端失败。',diagnostic=r.stderr[-2000:])
    return json.loads(r.stdout) if action in ['inspect','assembly'] else None


class PCBService:
    def pcb_retry_prepare(self,request):
        from .models import PCBRef
        from .copper import dsn_report
        with self.locked():
            s=self.load(request.project_id);fingerprint=backend.digest(json.dumps(['pcb_route_retry_prepare',request.model_dump()],sort_keys=True).encode())
            previous=s['idempotency'].get(request.idempotency_key)
            if previous:
                require(previous['fingerprint']==fingerprint,'IDEMPOTENCY_CONFLICT','修复请求的幂等键冲突。');return previous['result']
            require(s['revision']==request.expected_revision,'REVISION_CONFLICT','规格已经变化。')
            s,parent,source=self.pcb_target(PCBRef(project_id=s['id'],pcb_build_id=request.pcb_build_id))
            check=parent['checks'].get('drc',{})
            require(check.get('verdict')=='fail' and bool(check.get('files')),'FAILED_CONNECTIVITY_REQUIRED','先运行实际 DRC；仅支持存在未连接项的失败结果。')
            for n,sha in check['files'].items():require(backend.digest((source/n).read_bytes())==sha,'ARTIFACT_CHANGED','父构建的 DRC 证据已变化。')
            failure=json.loads((source/'drc_report.json').read_text())
            require(any(f['type']=='unconnected_items' for f in failure['findings']) and all(f['type'] in ['unconnected_items','via_dangling'] for f in failure['findings']),'CONNECTIVITY_RETRY_UNSUPPORTED','本工具不掩盖线宽、间距等其他错误；只准备已有连接缺口的增量布线。')
            ident=new_id();folder=self.project_path(s['id'])/'pcb_builds'/ident;folder.mkdir(parents=True)
            names=['spec.json','preflight.json','unrouted.kicad_pcb','board_initial.json','fp-lib-table','unrouted.kicad_pro','unrouted.kicad_dru','routed.kicad_pro','routed.kicad_dru']
            if (source/'native_nc_nets.json').is_file():names.append('native_nc_nets.json')
            if (source/'native_component_fields.json').is_file():names.append('native_component_fields.json')
            for n in names:shutil.copy2(source/n,folder/n)
            for ext in ['kicad_pcb','kicad_pro','kicad_dru']:shutil.copy2(source/('routed.'+ext),folder/('seed.'+ext));names.append('seed.'+ext)
            provenance=dict(parent_pcb_build_id=parent['id'],parent_board_sha256=parent['sources']['routed.kicad_pcb'],parent_drc_hashes=check['files'],approvals_inherited=False,checks_inherited=False)
            write_json(folder/'route_repair_provenance.json',provenance);worker('reseed',folder)
            isolated=json.loads((folder/'native_nc_nets.json').read_text()) if (folder/'native_nc_nets.json').is_file() else []
            write_json(folder/'dsn_rules.json',dsn_report(s,(folder/'input.dsn').read_text(),isolated))
            names+=['route_repair_provenance.json','input.dsn','dsn_rules.json']
            entry=dict(id=ident,revision=s['revision'],schematic_build_id=parent['schematic_build_id'],checks={},route_repair_parent=parent['id'],sources={n:backend.digest((folder/n).read_bytes()) for n in names})
            s['pcb_builds'][ident]=entry;result=dict(pcb_build_id=ident,revision=s['revision'],board_hash=entry['sources']['unrouted.kicad_pcb'],qualification='seeded_connectivity_repair_requires_fresh_pad_layout_routing_and_DRC_checks')
            s['idempotency'][request.idempotency_key]=dict(fingerprint=fingerprint,result=result);self.save(s);return result

    def footprint_search(self,request):
        with self.locked():
            entries=footprints.entries(request.query,request.library);end=request.offset+request.limit
            results=[footprints.load(ident,path) for ident,path in entries[request.offset:end]]
            path=self.root/'footprint_candidates.json';ledger=json.loads(path.read_text()) if path.exists() else {}
            for c in results:ledger[c['candidate_id']]=dict(candidate=c,resolved=False) if c['candidate_id'] not in ledger else ledger[c['candidate_id']]
            write_json(path,ledger)
            return dict(candidates=results,total=len(entries),next_offset=end if end<len(entries) else None)

    def footprint_candidate(self,ident,resolved=False):
        path=self.root/'footprint_candidates.json';ledger=json.loads(path.read_text()) if path.exists() else {}
        require(ident in ledger,'UNKNOWN_FOOTPRINT','需先查询真实封装候选。')
        row=ledger[ident];c=row['candidate'];actual=footprints.load(c['footprint_id'],Path(c['source']))
        require(actual['candidate_id']==ident,'FOOTPRINT_LIBRARY_CHANGED','封装库已改变，需要重新查询和选择。')
        if resolved:require(row['resolved'],'FOOTPRINT_NOT_RESOLVED','需先读取封装的真实焊盘。')
        return row,ledger,path

    def footprint_get(self,request):
        with self.locked():
            row,ledger,path=self.footprint_candidate(request.candidate_id);row['resolved']=True;write_json(path,ledger)
            return row['candidate']

    def footprint_bind(self,request):
        def change(s):
            require(request.component_id in s['components'],'UNKNOWN_COMPONENT','元件不存在。')
            c=s['components'][request.component_id];row,_,_=self.footprint_candidate(request.candidate_id,True);fp=row['candidate']
            require(c['id'] not in s.get('part_bindings',{}) or s['part_bindings'][c['id']]['footprint_id']==fp['footprint_id'],'PART_FOOTPRINT_CONFLICT','先解除实物器件绑定，不能将已选实物静默换成其他封装。')
            require(fp['supported'],'UNSUPPORTED_FOOTPRINT','封装超出本版范围。',reasons=fp['unsupported_reasons'])
            require(footprints.binding_matches(c,fp),'FOOTPRINT_ROLE_MISMATCH','封装名称不符合真实符号库的封装筛选条件。')
            require(footprints.pins(c)=={p['number'] for p in fp['pads']},'PAD_PIN_MISMATCH','本版要求真实物理引脚编号与全部电气焊盘编号严格一致。',pins=sorted(footprints.pins(c)),pads=sorted({p['number'] for p in fp['pads']}))
            s['footprint_bindings'][c['id']]=dict(copy.deepcopy(fp),mapping_mode=request.mapping_mode,purpose=request.purpose,reason=request.reason,qualification='number_identity_geometry_only_not_mpn_verified')
            return dict(entity_id=c['id'])
        return self.mutate('footprint_bind',request,change)

    def footprint_unbind(self,request):
        def change(s):
            require(request.component_id in s['footprint_bindings'],'UNKNOWN_BINDING','此元件没有封装绑定。')
            require(request.component_id not in s.get('part_bindings',{}),'PART_FOOTPRINT_CONFLICT','先解除实物器件绑定。')
            del s['footprint_bindings'][request.component_id];return dict(entity_id=request.component_id)
        return self.mutate('footprint_unbind',request,change)

    def board_constraints_set(self,request):
        def change(s):
            value=request.model_dump(exclude={'project_id','expected_revision','idempotency_key'})
            require(min(value['width_mm'],value['height_mm'])>2*value['edge_margin_mm']+10,'CONSTRAINT_INFEASIBLE','板框与边缘预留没有留下可布局区域。')
            from .explicit_placement import validate
            try:validate(s,value)
            except ValueError as exc:require(False,'PLACEMENT_CONSTRAINT_INVALID',str(exc))
            s['board_constraints']=value;return dict(entity_id=s['id'])
        return self.mutate('board_constraints_set',request,change)

    def pcb_preflight_state(self,s):
        report=self.validate_state(s);findings=list(report['findings'])
        coverage=[dict(check_id='library_geometry_and_number_identity',status='checked'),dict(check_id='manufacturer_package_and_function',status='unsupported'),dict(check_id='manufacturing_approval',status='not_performed')]
        def add(code,msg):findings.append(dict(code=code,severity='error',message=msg))
        if not s['board_constraints']:add('BOARD_CONSTRAINTS_MISSING','需明确板框、层数、线宽及间距。')
        else:
            from .explicit_placement import validate
            try:validate(s,s['board_constraints'])
            except ValueError as exc:add('PLACEMENT_CONSTRAINT_INVALID',str(exc))
            height_limit=s['board_constraints'].get('component_height_max_mm')
            if height_limit is not None:
                from .assembly_height import evaluate_state
                height=evaluate_state(s,height_limit)
                coverage.append(dict(check_id='source_component_height',status='pass' if height['passed'] else 'fail',evidence=height))
                for code in height['failures']:add(code,'来源尺寸未能证明器件满足声明限高。')
        if any(c['library_id']==ESP for c in s['components'].values()):
            if s['board_constraints'].get('placement_profile')!='esp32_sensor_reference':add('RF_LAYOUT_PROFILE_REQUIRED','ESP32 不允许使用普通网格布局；需选择受管天线/旁路参考布局。')
            else:
                try:
                    plan=reference_plan(s)
                    for c in s['components'].values():
                        if c['id'] not in plan:findings.append(dict(code='ADDITIONAL_COMPONENT_PLACEMENT',severity='warning',message=c['ref']+' 将依据实际封装几何自动放置，必须重新核验布局与 DRC。'))
                except ValueError as e:add('REFERENCE_LAYOUT_UNSUPPORTED',str(e))
        for c in s['components'].values():
            fp=s['footprint_bindings'].get(c['id'])
            if not fp:add('FOOTPRINT_BINDING_MISSING',c['ref']+' 缺少真实封装绑定。');continue
            try:
                row,_,_=self.footprint_candidate(fp['candidate_id'],True)
                if row['candidate']!={k:v for k,v in fp.items() if k not in ['mapping_mode','purpose','reason','qualification']}:add('FOOTPRINT_LIBRARY_CHANGED','绑定数据与真实候选不同。')
                if footprints.pins(c)!={p['number'] for p in fp['pads']}:add('PAD_PIN_MISMATCH','引脚与焊盘不一致。')
            except DomainError as e:add(e.payload['code'],e.payload['message'])
        findings.append(dict(code='PHYSICAL_PACKAGE_AND_PROCUREMENT_UNVERIFIED',severity='warning',message='焊盘编号匹配仅用于几何实验；未确认实物 MPN、额定值、封装适配和制造许可。'))
        return dict(verdict='fail' if any(f['severity']=='error' for f in findings) else 'pass',revision=s['revision'],findings=findings,coverage=coverage)

    def pcb_preflight(self,request):
        with self.locked():
            s=self.load(request.project_id);require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            return self.pcb_preflight_state(s)

    def verified_parent_pcb(self, state, build_id):
        from .editing import spec_hash
        from .models import PCBRef
        provenance = state.get('provenance', {})
        parent_id = provenance.get('parent_project_id')
        require(parent_id, 'ROUTING_BASE_REQUIRES_FORK', '复用布线需要直接父版。')
        parent, build, folder = self.pcb_target(PCBRef(project_id=parent_id, pcb_build_id=build_id))
        require(parent['revision'] == provenance['parent_revision']
                and spec_hash(parent) == provenance['parent_spec_sha256'],
                'ROUTING_BASE_STALE', '父版规格已改变。')
        for name in ('verify_routed', 'drc'):
            check = build['checks'].get(name, {})
            require(check.get('verdict') == 'pass' and check.get('files')
                    and all(backend.digest((folder / filename).read_bytes()) == sha
                            for filename, sha in check['files'].items()),
                    'ROUTING_BASE_UNVERIFIED', '需要未改变的父版布线和 DRC 证据。')
        return parent, build, folder

    def revision_routing_base(self, state, build_id):
        from .editing import SPEC_KEYS
        from .models import BoardConstraints
        parent, build, folder = self.verified_parent_pcb(state, build_id)
        keys = set(SPEC_KEYS) - {'board_constraints', 'requirements'}
        rule_fields = {'project_id', 'expected_revision', 'idempotency_key', 'placements', 'placement_profile'}
        old_rules, new_rules = (BoardConstraints.model_validate(dict(
            project_id=spec['id'], expected_revision=spec['revision'], idempotency_key='routing-base',
            **spec['board_constraints'],
        )).model_dump(exclude=rule_fields) for spec in (parent, state))
        require(state['board_constraints']['placement_profile'] == 'explicit'
                and old_rules == new_rules and all(parent.get(key) == state.get(key) for key in keys),
                'ROUTING_BASE_REQUIRES_PLACEMENT_ONLY', '复用布线要求电路、器件和板规则一致。')
        return folder, dict(parent_project_id=parent['id'], parent_pcb_build_id=build_id,
                            parent_board_sha256=build['sources']['routed.kicad_pcb'])

    def reuse_parent_pcb(self, state, build_id, folder):
        from .editing import SPEC_KEYS
        parent, build, source = self.verified_parent_pcb(state, build_id)
        require(all(parent.get(key) == state.get(key) for key in SPEC_KEYS if key != 'requirements'),
                'PCB_REUSE_REQUIRES_UNCHANGED_DESIGN', '复用已布线 PCB 要求电路、器件、布局及板规则一致。')
        names = ['board_initial.json', 'board_routed.json', 'input.dsn', 'dsn_rules.json']
        names.extend(stem+'.'+ext for stem in ('unrouted', 'routed') for ext in ('kicad_pcb', 'kicad_pro', 'kicad_dru'))
        for name in names:
            raw = (source/name).read_bytes()
            require(backend.digest(raw) == build['sources'].get(name),
                    'ARTIFACT_CHANGED', '父版 PCB 输入或布线证据发生变化。')
            (folder/name).write_bytes(raw)
        provenance = 'pcb_reuse_provenance.json'
        write_json(folder/provenance, dict(parent_project_id=parent['id'], parent_pcb_build_id=build_id,
                   parent_board_sha256=build['sources']['routed.kicad_pcb'], checks_inherited=False))
        return [*names, provenance]

    def build_fresh_pcb(self, s, folder, routing_base):
        c=s['board_constraints'];rulefiles=[]
        if routing_base is not None:
            base_folder, provenance = routing_base
            shutil.copyfile(base_folder/'routed.kicad_pcb', folder/'routing_base.kicad_pcb')
            write_json(folder/'routing_base.json', provenance)
            rulefiles.extend(['routing_base.kicad_pcb', 'routing_base.json', 'revision_routing_seed.json'])
        from .native_fabrication import routing_via_size
        via_drill, via_pad = routing_via_size(c)
        project=dict(meta=dict(version=1),board=dict(design_settings=dict(rule_severities={k:'warning' for k in ['missing_courtyard','footprint_type_mismatch','footprint_filters_mismatch']},rules=dict(min_clearance=c['clearance_mm'],min_track_width=c['track_width_mm'],min_copper_edge_clearance=c['edge_clearance_mm'],min_through_hole_diameter=c.get('min_drill_mm',.3),min_hole_clearance=c.get('hole_clearance_mm',.25),min_hole_to_hole=c.get('hole_to_hole_mm',.25)))),net_settings=dict(classes=[dict(name='Default',clearance=c['clearance_mm'],track_width=c['track_width_mm'],via_diameter=via_pad,via_drill=via_drill)]))
        rules='(version 1)\n'+''.join('(rule "MCP '+name+'" (constraint '+kind+' (min '+str(value)+'mm)))\n' for name,kind,value in [('clearance','clearance',c['clearance_mm']),('track width','track_width',c['track_width_mm']),('edge clearance','edge_clearance',c['edge_clearance_mm'])])
        assignments=[]
        for rule in s.get('pcb_copper_policy',{}).get('rules',[]):
            name='MCP_'+rule['net_id'];netname=s['nets'][rule['net_id']]['name']
            project['net_settings']['classes'].append(dict(name=name,clearance=c['clearance_mm'],track_width=rule['min_width_mm'],via_diameter=via_pad,via_drill=via_drill))
            assignments.append(dict(pattern=netname,netclass=name))
            condition="A.NetName == '"+netname+"'"
            rules+='(rule "MCP '+name+'" (condition '+json.dumps(condition)+') (constraint track_width (min '+str(rule['min_width_mm'])+'mm)))\n'
        if assignments:project['net_settings']['netclass_patterns']=assignments
        from .copper_escape import all_definitions as definitions,area_name
        for definition in definitions(s):
            for pad in definition['pads']:
                netname=s['nets'][definition['net_id']]['name'];name=area_name(definition,pad)
                condition="A.Type == 'Track' && A.Layer == 'F.Cu' && A.NetName == '"+netname+"' && A.enclosedByArea('"+name+"')"
                rules+='(rule "'+name+'" (condition '+json.dumps(condition)+') (constraint track_width (min '+str(definition['min_width_mm'])+'mm)))\n'
        for stem in ['unrouted','routed']:
            write_json(folder/(stem+'.kicad_pro'),project);(folder/(stem+'.kicad_dru')).write_text(rules)
            rulefiles.extend([stem+'.kicad_pro',stem+'.kicad_dru'])
        worker('build',folder)
        from .copper import dsn_report
        write_json(folder/'dsn_rules.json',dsn_report(s,(folder/'input.dsn').read_text(),json.loads((folder/'native_nc_nets.json').read_text())))
        return ['input.dsn','dsn_rules.json','unrouted.kicad_pcb','board_initial.json',*rulefiles]

    def pcb_build(self,request):
        with self.locked():
            s=self.load(request.project_id);fingerprint=backend.digest(json.dumps(['pcb_build',request.model_dump(exclude_none=True)],sort_keys=True).encode())
            old=s['idempotency'].get(request.idempotency_key)
            if old:
                require(old['fingerprint']==fingerprint,'IDEMPOTENCY_CONFLICT','幂等键冲突。');return old['result']
            require(s['revision']==request.expected_revision,'REVISION_CONFLICT','版本已变化。')
            report=self.pcb_preflight_state(s);require(report['verdict']=='pass','PCB_SPEC_INVALID','PCB 前置检查未通过。',report=report)
            _,sb,sfolder=self.target(BuildRef(project_id=s['id'],build_id=request.schematic_build_id))
            for name in ['verify','erc']:
                check=sb['checks'].get(name,{})
                require(check.get('verdict')=='pass','SCHEMATIC_VALIDATION_REQUIRED','需先核验当前原理图网表和 ERC。')
                for filename,sha in check['files'].items():require(backend.digest((sfolder/filename).read_bytes())==sha,'ARTIFACT_CHANGED','原理图验证证据发生变化。')
            # Footprint names in the actual, independently exported schematic
            # must agree with the physical binding before board creation.
            import xml.etree.ElementTree as ET
            names={x.attrib['ref']:x.findtext('footprint','') for x in ET.parse(sfolder/'actual.xml').findall('./components/comp') if not x.attrib['ref'].startswith('#')}
            require(names=={c['ref']:s['footprint_bindings'][c['id']]['footprint_id'] for c in s['components'].values()},'SCHEMATIC_FOOTPRINT_MISMATCH','实际原理图中的封装与所选绑定不同，请重新生成和核验。')
            require(not (request.reuse_parent_pcb_build_id and request.routing_base_pcb_build_id),
                    'PCB_REUSE_AND_ROUTING_SEED_CONFLICT', '复用 PCB 与增量布线种子不能同时指定。')
            routing_base = (self.revision_routing_base(s, request.routing_base_pcb_build_id)
                            if request.routing_base_pcb_build_id else None)
            ident=new_id();folder=self.project_path(s['id'])/'pcb_builds'/ident;folder.mkdir(parents=True)
            write_json(folder/'spec.json',s);write_json(folder/'preflight.json',report)
            from . import no_connects
            isolated = no_connects.read(s, sfolder/'actual.xml')
            nc_sources = []
            if isolated:
                netlist = folder/'board-netlist.net'
                command = backend.command(['sch','export','netlist','--format','kicadsexpr',
                                           '-o',str(netlist.resolve()),'led.kicad_sch'], sfolder)
                write_json(folder/'board-netlist-command.json', command)
                require(command['exit_code'] == 0, 'BACKEND_FAILED', 'KiCad PCB 网表导出失败。')
                native = no_connects.read_board_names(s, netlist)
                groups = lambda nets: {frozenset((p['ref'],p['number']) for p in n['pins']) for n in nets}
                require(groups(isolated) == groups(native), 'NATIVE_NC_NETLIST_MISMATCH',
                        'KiCad 的两份网表对悬空焊盘的分组不一致。')
                isolated = native
                nc_sources = [netlist.name, 'board-netlist-command.json']
            write_json(folder/'native_nc_nets.json',isolated)
            from .parts import native_cad_fields
            write_json(folder/'native_component_fields.json',native_cad_fields(s))
            footprints.write_table(s['footprint_bindings'],folder)
            sources = ['spec.json','preflight.json','native_nc_nets.json','native_component_fields.json','fp-lib-table',*nc_sources]
            if request.reuse_parent_pcb_build_id:
                sources.extend(self.reuse_parent_pcb(s, request.reuse_parent_pcb_build_id, folder))
                board_file = 'routed.kicad_pcb'
            else:
                sources.extend(self.build_fresh_pcb(s, folder, routing_base))
                board_file = 'unrouted.kicad_pcb'
            entry=dict(id=ident,revision=s['revision'],schematic_build_id=sb['id'],checks={},sources={n:backend.digest((folder/n).read_bytes()) for n in sources})
            s['pcb_builds'][ident]=entry
            result=dict(pcb_build_id=ident,revision=s['revision'],board_hash=entry['sources'][board_file],qualification='geometry_experiment_not_manufacturing_approved')
            s['idempotency'][request.idempotency_key]=dict(fingerprint=fingerprint,result=result);self.save(s)
            return result

    def pcb_target(self,request):
        s=self.load(request.project_id);require(request.pcb_build_id in s['pcb_builds'],'UNKNOWN_PCB_BUILD','PCB 构建不存在。')
        b=s['pcb_builds'][request.pcb_build_id];require(b['revision']==s['revision'],'STALE_VALIDATION','PCB 已落后于当前规格版本。')
        _,sb,sfolder=self.target(BuildRef(project_id=s['id'],build_id=b['schematic_build_id']))
        for name in ['verify','erc']:
            check=sb['checks'].get(name,{})
            require(check.get('verdict')=='pass','SCHEMATIC_VALIDATION_REQUIRED','原理图验证证据缺失。')
            for n,sha in check.get('files',{}).items():require(backend.digest((sfolder/n).read_bytes())==sha,'ARTIFACT_CHANGED','原理图验证证据发生变化。')
        folder=self.project_path(s['id'])/'pcb_builds'/b['id']
        for n,sha in b['sources'].items():require(backend.digest((folder/n).read_bytes())==sha,'ARTIFACT_CHANGED','PCB 输入、布线或原始证据发生变化。')
        require(self.pcb_preflight_state(s)['verdict']=='pass','PCB_SPEC_INVALID','当前物理库或规格无效。')
        return s,b,folder

    def pcb_verify(self,request):
        with self.locked():
            s,b,folder=self.pcb_target(request);filename=request.stage+'.kicad_pcb'
            require(filename in b['sources'],'ROUTING_REQUIRED','尚无已完成的布线结果。')
            actual=worker('inspect',folder,filename);expected={}
            from . import no_connects
            isolated=no_connects.pads(json.loads((folder/'native_nc_nets.json').read_text())) if (folder/'native_nc_nets.json').is_file() else {}
            assigned={(p['component_id'],p['pin_id']):n['name'] for n in s['nets'].values() for p in n['pins']}
            for c in s['components'].values():
                fp=s['footprint_bindings'][c['id']];pads=[]
                for p in fp['pads']:
                    pin=next(x for x in c['pins'] if p['number'] in x.get('physical_numbers',backend.expanded_number(x['number'])))
                    pads.append((p['number'],assigned.get((c['id'],pin['pin_id']),isolated.get((c['ref'],p['number']),''))))
                expected[c['ref']]=dict(value=c['value'],footprint=fp['footprint_id'],pads=sorted(pads))
            observed={ref:dict(value=c['value'],footprint=c['footprint'],pads=sorted((p['number'],p['net']) for p in c['pads'])) for ref,c in actual['components'].items()}
            if (folder/'native_component_fields.json').is_file():
                source_fields=json.loads((folder/'native_component_fields.json').read_text())
                for ref in expected:expected[ref]['source_fields']=source_fields.get(ref,{})
                for ref in observed:observed[ref]['source_fields']=actual['components'][ref].get('source_fields',{})
            c=s['board_constraints'];edges=sorted([list(v) for v in [(0,0,c['width_mm'],0),(c['width_mm'],0,c['width_mm'],c['height_mm']),(c['width_mm'],c['height_mm'],0,c['height_mm']),(0,c['height_mm'],0,0)]])
            passed=expected==observed and sorted(actual['outline'])==edges and actual['stackup']['passed']
            report=dict(pcb_build_id=b['id'],revision=s['revision'],verdict='pass' if passed else 'fail',findings=[] if passed else [dict(code='PCB_SPEC_MISMATCH',severity='error',message='实际 PCB 元件、值、封装、焊盘网络或板框与规格不一致。')],details=dict(expected=expected,actual=observed,outline_match=sorted(actual['outline'])==edges,stage=request.stage,track_count=actual['track_count']))
            if c.get('silkscreen_labels'):
                from .explicit_placement import PCB_UNITS_PER_MM,silkscreen_position
                def normalized_labels(rows):
                    return sorted((row['text'],*(pos/PCB_UNITS_PER_MM for pos in silkscreen_position(row))) for row in rows)
                expected_labels=normalized_labels(c['silkscreen_labels'])
                actual_labels=normalized_labels(actual.get('silkscreen_labels',[]))
                report['details']['silkscreen_labels']=dict(expected=expected_labels,actual=actual_labels,match=actual_labels==expected_labels)
                if actual_labels!=expected_labels:
                    report['verdict']='fail'
                    report['findings'].append(dict(code='PCB_SILKSCREEN_LABEL_MISMATCH',severity='error',message='实际顶层丝印与声明的接口标记不一致。'))
            report['details']['stackup']=actual['stackup']
            report['findings'].extend(dict(code=code,severity='error',message='实际 PCB 层数、厚度或叠层与声明不一致。') for code in actual['stackup']['errors'])
            fabrication=actual['fabrication']
            report['details']['fabrication']=fabrication
            if not fabrication['passed']:
                report['verdict']='fail'
                report['findings'].extend(dict(code=code,severity='error',message='实际板框、过孔或铜边距与声明不一致。') for code in fabrication['failures'])
            if actual.get('mounting') is not None:
                mounting=actual['mounting']
                report['details']['mounting']=mounting
                if not mounting['passed']:
                    report['verdict']='fail'
                    report['findings'].extend(dict(code=code,severity='error',message='实际非镀铜安装孔或净空与声明不一致。') for code in mounting['failures'])
            if actual.get('interface_markings') is not None:
                markings=actual['interface_markings']
                report['details']['interface_markings']=markings
                if not markings['passed']:
                    report['verdict']='fail'
                    report['findings'].extend(dict(code=code,severity='error',message='实际接口丝印与焊盘或极性网络不一致。') for code in markings['failures'])
            name='verify_'+request.stage+'.json';write_json(folder/name,report)
            b['checks']['verify_'+request.stage]=dict(verdict=report['verdict'],files={name:backend.digest((folder/name).read_bytes())});self.save(s)
            return report

    def pcb_autoroute(self,request):
        with self.locked():
            s,b,folder=self.pcb_target(request)
            check=b['checks'].get('verify_unrouted',{})
            require(check.get('verdict')=='pass','PCB_VERIFICATION_REQUIRED','需先核验未布线 PCB 的真实焊盘与网络。')
            for n,sha in check.get('files',{}).items():require(backend.digest((folder/n).read_bytes())==sha,'ARTIFACT_CHANGED','PCB 核验证据发生变化。')
            if any(c['library_id']==ESP for c in s['components'].values()):
                layout=b['checks'].get('layout_unrouted',{})
                require(layout.get('verdict')=='pass' and all(backend.digest((folder/n).read_bytes())==sha for n,sha in layout.get('files',{}).items()),'LAYOUT_VALIDATION_REQUIRED','布线前需先通过实际天线/旁路布局检查。')
            require('routed.kicad_pcb' not in b['sources'],'ROUTE_ALREADY_EXISTS','布线结果不可覆盖；如需重新布线请新建构建。')
            require(ROUTER.is_file() and java_runtime()['available'],'ROUTER_UNAVAILABLE','需要已核验的 Freerouting 2.4.1 和 Java 25。')
            require(backend.digest(ROUTER.read_bytes())==ROUTER_SHA256,'ROUTER_ENGINE_CHANGED','布线器与已核验的固定版本不一致。')
            before_state = backend.digest((self.project_path(s['id'])/'state.json').read_bytes())
            before_files = file_snapshot(folder)
            # Server-owned headless settings request disabled listeners/analytics.
            settings=folder/'router_data';settings.mkdir(exist_ok=True);(settings/'tmp').mkdir(exist_ok=True)
            # Fanout generates narrowed segments even when the net has a wider
            # minimum. Keep it disabled; the autorouter may neck down only where
            # necessary. Native DRC/copper checks still enforce managed windows.
            write_json(settings/'freerouting.json',dict(version=ROUTER_VERSION,profile=dict(id='00000000-0000-4000-8000-000000000001',email='',allow_telemetry=False,allow_contact=False),gui=dict(enabled=False),api_server=dict(enabled=False),usage_and_diagnostic_data=dict(disable_analytics=True),router=dict(max_passes=request.max_passes,automatic_neckdown=True,fanout=dict(enabled=False),optimizer=dict(enabled=False))))
            optimizer_threads=1
            args=[str(JAVA),'-Xmx512m','-Djava.awt.headless=true','-Djava.io.tmpdir='+str(settings/'tmp'),'-jar',str(ROUTER),'-de',str(folder/'input.dsn'),'-do',str(folder/'output.ses'),'-mp',str(request.max_passes),'-mt',str(optimizer_threads),'-da','--gui.enabled=false','--api_server.enabled=false','--usage_and_diagnostic_data.disable_analytics=true','--user_data_path='+str(settings)]
            env={k:v for k,v in os.environ.items() if not k.startswith('FREEROUTING__')}
            try:r=subprocess.run(args,cwd=folder,env=env,capture_output=True,text=True,timeout=90)
            except subprocess.TimeoutExpired as e:
                def decoded(value):return value.decode('utf-8',errors='replace') if isinstance(value,bytes) else value or ''
                write_json(folder/'router_timeout.json',dict(args=args,hard_timeout_seconds=90,stdout=decoded(e.stdout),stderr=decoded(e.stderr),partial_diagnostic_only=True,passed=False))
                raise DomainError('ROUTER_TIMEOUT','自动布线超时，未生成通过报告。',diagnostic_file='router_timeout.json') from e
            observed=[int(v) for v in re.findall(r'Auto-routing pass #(\d+)',r.stdout+r.stderr)]
            max_observed=max(observed,default=0)
            write_json(folder/'router_command.json',dict(args=args,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr,engine_sha256=backend.digest(ROUTER.read_bytes()),engine_version=ROUTER_VERSION,hard_timeout_seconds=90,optimizer_threads=optimizer_threads,optimizer_enabled=False,fanout_enabled=False,requested_pass_limit=request.max_passes,observed_max_pass=max_observed,pass_limit_enforced=max_observed<=request.max_passes))
            require(max_observed<=request.max_passes,'ROUTER_PASS_LIMIT','实际布线轮数超过请求上限。')
            require(r.returncode==0 and (folder/'output.ses').is_file(),'ROUTER_FAILED','Freerouting 没有成功生成 SES。',exit_code=r.returncode,diagnostic=(r.stderr+r.stdout)[-2000:])
            worker('import',folder)
            worker('stabilize',folder)
            after_files = file_snapshot(folder)
            for name in ['routed.kicad_pcb','output.ses','router_command.json','board_routed.json','fill_serialization.json','router_data/freerouting.json']:
                b['sources'][name]=after_files[name]
            if 'router_result.json' in after_files:b['sources']['router_result.json']=after_files['router_result.json']
            result = dict(pcb_build_id=b['id'], revision=s['revision'], board_hash=b['sources']['routed.kicad_pcb'],
                          qualification='routed_not_drc_or_manufacturing_approved')
            b['route_receipt'] = dict(
                request_sha256=request_fingerprint(request.model_dump(mode='json')),
                before_state_sha256=before_state, routed_state_sha256=request_fingerprint(s),
                artifacts={name: sha for name, sha in after_files.items() if before_files.get(name)!=sha},
                result=result)
            self.save(s)
            return result

    def pcb_drc(self,request):
        with self.locked():
            s,b,folder=self.pcb_target(request);require('routed.kicad_pcb' in b['sources'],'ROUTING_REQUIRED','需先完成布线。')
            run=backend.command(['pcb','drc','--format','json','--all-track-errors','--severity-all','--exit-code-violations','-o','drc.json','routed.kicad_pcb'],folder)
            write_json(folder/'drc_command.json',run);require(run['exit_code'] in [0,5],'PCB_DRC_BACKEND_FAILED','KiCad DRC 执行失败。')
            raw=json.loads((folder/'drc.json').read_text())
            require(isinstance(raw,dict) and isinstance(raw.get('kicad_version'),str) and all(isinstance(raw.get(k),list) for k in ['violations','unconnected_items','schematic_parity','ignored_checks']), 'PCB_DRC_REPORT_INVALID','KiCad DRC 报告结构不完整，不能作为通过证据。')
            findings=raw['violations']+raw['unconnected_items']+raw['schematic_parity']
            report=dict(pcb_build_id=b['id'],revision=s['revision'],verdict='pass' if run['exit_code']==0 and not findings else 'fail',findings=findings,details=dict(engine=raw.get('kicad_version'),unconnected_count=len(raw.get('unconnected_items',[])),ignored_checks=raw.get('ignored_checks',[]),included_severities=raw.get('included_severities',[]),coverage='KiCad DRC with explicit hashed project constraints and reported ignored checks; independent pad/net comparison is separate; function and manufacturing approval unsupported'))
            write_json(folder/'drc_report.json',report);b['checks']['drc']=dict(verdict=report['verdict'],files={n:backend.digest((folder/n).read_bytes()) for n in ['drc.json','drc_report.json','drc_command.json']});self.save(s);return report

    def pcb_preview(self,request):
        with self.locked():
            s,b,folder=self.pcb_target(request);require('routed.kicad_pcb' in b['sources'],'ROUTING_REQUIRED','需先完成布线。')
            (folder/'preview').mkdir(exist_ok=True)
            from .stackup import copper_layers
            paths={};hashes={}
            for layer in copper_layers(s['board_constraints']):
                silk=['F.Silkscreen'] if layer=='F.Cu' else ['B.Silkscreen'] if layer=='B.Cu' else []
                layers=','.join([layer]+silk+['Edge.Cuts'])
                relative='preview/pcb.svg' if layer=='F.Cu' else 'preview/'+layer.replace('.','-')+'.svg'
                run=backend.command(['pcb','export','svg','--layers',layers,'--mode-single','--page-size-mode','2','--exclude-drawing-sheet','-o',relative,'routed.kicad_pcb'],folder)
                require(run['exit_code']==0 and (folder/relative).is_file(),'PREVIEW_FAILED','PCB SVG 层预览导出失败。',diagnostic=run)
                hashes[relative]=backend.digest((folder/relative).read_bytes());paths[layer]=str(folder/relative)
            primary='preview/pcb.svg';sha=hashes[primary]
            b['checks']['preview']=dict(verdict='produced',primary=primary,files=hashes);self.save(s)
            return dict(pcb_build_id=b['id'],revision=s['revision'],preview_path=str(folder/primary),sha256=sha,layer_paths=paths,resource_uri='pcb-board://'+s['id']+'/'+b['id']+'/preview')

    def pcb_preview_content(self,request):
        with self.locked():
            s,b,folder=self.pcb_target(request);check=b['checks'].get('preview',{})
            require(check.get('verdict')=='produced','PREVIEW_REQUIRED','尚无预览。')
            name=check.get('primary','preview/pcb.svg')
            require(name in check['files'],'PREVIEW_FAILED','缺少主预览。')
            for relative,sha in check['files'].items():
                require(backend.digest((folder/relative).read_bytes())==sha,'ARTIFACT_CHANGED','预览发生变化。')
            return (folder/name).read_text()

    def pcb_review_export(self,request):
        with self.locked():
            s,b,folder=self.pcb_target(request)
            before = file_snapshot(folder)
            state_sha256 = backend.digest((self.project_path(s['id'])/'state.json').read_bytes())
            files=set(b['sources'])
            for name,verdict in [('verify_routed','pass'),('drc','pass'),('preview','produced')]:
                check=b['checks'].get(name,{})
                require(check.get('verdict')==verdict,'PCB_VALIDATION_REQUIRED','缺少当前 PCB 的 '+name+' 检查。')
                for n,sha in check['files'].items():require(backend.digest((folder/n).read_bytes())==sha,'ARTIFACT_CHANGED','PCB 检查或预览被修改。');files.add(n)
            assembly=dict(status='incomplete',reason='Source-linked parts are required for complete BOM and assembly tables.')
            if s['components'] and set(s['components'])<=set(s.get('part_bindings',{})):
                rows=self.bom_state(s)
                write_json(folder/'bom.json',rows)
                output=io.StringIO();writer=csv.DictWriter(output,fieldnames=list(rows[0]))
                writer.writeheader();writer.writerows(rows);(folder/'bom.csv').write_text(output.getvalue())
                from .assembly_files import prepare
                files.update(prepare(s,folder,folder,rows));files.update(['bom.json','bom.csv'])
                assembly=dict(status='complete_source_linked_tables',bom_components=len(rows),
                              scope='native positions and source-linked BOM, requires assembly review; not a manufacturing authorization')
            from .portable import prepare as prepare_portable
            _,_,schematic_source=self.target(BuildRef(project_id=s['id'],build_id=b['schematic_build_id']))
            portable=prepare_portable(s,folder,schematic_source,folder/'portable')
            files.update(str(path.relative_to(folder)) for path in (folder/'portable').rglob('*')
                         if path.is_file() and path.suffix not in ('.kicad_prl','.lck'))
            manifest=dict(pcb_build_id=b['id'],revision=s['revision'],qualification='geometry_connectivity_and_drc_experiment_only',not_manufacturing_ready=True,gerber_exported=False,human_manufacturing_approval=False,
                          assembly=assembly,portable_project=portable,findings=self.pcb_preflight_state(s)['findings'],hashes={n:backend.digest((folder/n).read_bytes()) for n in sorted(files)})
            write_json(folder/'manifest.json',manifest)
            bundle = folder/'pcb_review_bundle.zip'
            temporary = bundle.with_suffix('.zip.tmp')
            try:
                with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as z:
                    for name in sorted(files|{'manifest.json'}):z.write(folder/name,name)
                temporary.replace(bundle)
            finally:
                temporary.unlink(missing_ok=True)
            after = file_snapshot(folder)
            after.pop(PCB_REVIEW_RECEIPT, None)
            write_json(folder/PCB_REVIEW_RECEIPT, dict(
                request_sha256=request_fingerprint(request.model_dump(mode='json')),
                project_state_sha256=state_sha256, before_build_sha256=request_fingerprint(before),
                exported_build_sha256=request_fingerprint(after), bundle_sha256=after[bundle.name]))
            return dict(pcb_build_id=b['id'],revision=s['revision'],bundle_path=str(bundle),manifest=manifest)
