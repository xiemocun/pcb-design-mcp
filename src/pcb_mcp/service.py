import contextlib
import copy
import fcntl
import importlib.metadata
import json
import math
import re
import uuid
import zipfile
from pathlib import Path
from . import backend, electrical, parts
from .common import DomainError,require,new_id,write_json,create_project_id,publish_initial_project,request_fingerprint
from .pcb_service import PCBService,ROUTER,PYTHON,JAVA,java_runtime
from .parts_service import PartsService
from .manufacturing import ManufacturingService
from .design_checks import DesignChecks
from . import power_integrity, prototype_policy
from . import __version__
from .editing import EditingService
from .audit import AuditService
from .connectivity import ConnectivityService


from .copper import CopperService
from .bom import BOMService
from .power_budget import PowerBudgetService
from .power_startup import StartupService
from .reset_timing import ResetService
from .catalog_import import CatalogImportService
from .design_contract import DesignContractService
from .functional import FunctionalService
from .verification_models import VerificationModelService
from .design_jobs import DesignJobService
from . import registry


class Service(DesignJobService,FunctionalService,VerificationModelService,DesignContractService,CatalogImportService,ResetService,StartupService,PowerBudgetService,BOMService,CopperService,ConnectivityService,AuditService,EditingService,DesignChecks,ManufacturingService,PartsService,PCBService):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.runtime_source_sha256=backend.digest(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py'))))
        self.catalog = {}

    @contextlib.contextmanager
    def locked(self):
        # Cross-process lock also protects two clients sharing the same data root.
        with registry.using(self.root), (self.root/'.lock').open('a') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            yield

    def candidate(self, cid):
        if cid not in self.catalog:
            with registry.using(self.root):
                self.catalog.update({c['candidate_id']:c for c in backend.catalog()})
        require(cid in self.catalog,'UNKNOWN_CANDIDATE','候选不在本机受管库中。')
        return self.catalog[cid]

    def public_candidate(self, c):
        return {k:v for k,v in c.items() if k!='definition'}

    def module_check(self,request):
        from .module_contracts import evaluate
        with self.locked():
            s=self.load(request.project_id)
            require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            return evaluate(s)

    def usb_power_check(self,request):
        from .usb_power import evaluate
        with self.locked():
            s=self.load(request.project_id)
            require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            return evaluate(s)

    def usb_gate_check(self,request):
        from .usb_gate_contract import evaluate
        with self.locked():
            s=self.load(request.project_id)
            require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            return evaluate(s)

    def capabilities(self):
        with registry.using(self.root):
            return self._capabilities()

    def _capabilities(self):
        versions = {n:importlib.metadata.version(n) for n in ['mcp','skidl','schematizer']}
        kicad = backend.command(['version'],self.root)
        return dict(contract_version=__version__,versions=versions,kicad=kicad,
                    profile='schematic_experiment + legacy LED',library_scope=sorted(backend.library_paths()),
                    transport='stdio',features=dict(esp32_unloaded_reset_reference=True,local_capacitor_startup_reference=True,declared_dc_power_budget=True,polarized_capacitor_checks=True,bom_identity_audit=True,declared_passive_rating_screen=True,incomplete_bom_draft=True,seeded_connectivity_retry=True,per_net_copper_rules=True,actual_copper_geometry=True,conditional_copper_screen=True,schematic=True,erc=True,actual_netlist=True,
                    readable_led_layout=True,generic_single_unit_symbols=True,requirements=True,
                    connectivity_constraints=True,no_connect=True,editing=True,inherited_symbols=True,grouped_pins=True,
                    external_power_declarations=True,source_linked_nominal_rules=True,footprint_geometry=True,pcb=PYTHON.is_file(),freerouting=ROUTER.is_file() and java_runtime()['available'],drc=backend.CLI.is_file(),procurement=False,
                    source_linked_parts=True,bom=True,restricted_dc_stress=True,power_envelope_diagnostics=True,source_correct_sensor_symbol=True,rf_keepout_geometry=True,decoupling_proximity=True,consolidated_readiness=True,project_fork=True,atomic_part_replacement=True,source_linked_sensor_port_contract=True,source_linked_buck_module=True,usb_sink_power_diagnostics=True,source_linked_usb_hardware_interlock=True,conditional_usb_gate_scenarios=True,actual_pin_connections=True,fresh_design_audit=True,signature_gated_gerber=True,gerber=True,orders=False),limitations=[
                    f'{len(parts.catalog())} built-in or imported source-linked MPN identities. Imported documents and pin/package hashes are checked, not their semantic interpretation. Unselected supplier SKUs stay absent; identity is not functional qualification.',
                    'Restricted 25C series-LED DC bounds only; brightness, assembly, fault, transient and production qualification remain human review items.',
                    'Gerber/drill export requires an independent P256 receipt bound to immutable review bytes. No MCP approval/key registration tool. macOS reviewer uses Secure Enclave user-presence authentication.',
                    'Schematic experiment profile: up to 128 components / 100 nets, supported single-unit symbols in the reported local libraries.',
                    'Local inheritance and grouped pin numbers are supported; multi-unit and inherited graphical overrides fail closed.',
                    'Unbound generic symbols have no SKU assurance. Curated parts have source-linked MPN/SKU records, not physical qualification or live stock/price.',
                    'PCB geometry experiments: real local footprints, exact pad number mapping, bounded two-layer grid or server-owned ESP32 reference placement, local Freerouting and actual KiCad DRC. No functional/MPN/manufacturing approval.',
                    'PCB adapter is pinned to KiCad 10 bundled pcbnew/Python 3.9; SWIG is deprecated and requires migration before KiCad 11.',
                    'Requirements are recorded, not independently verified. Seven experimental source-linked packs check nominal topology only.',
                    'Power sources are user/model declarations, not measurements or human approval. Flags are generated only from these declarations.',
                    'Source-family voltage-envelope, current-capacity and reference thermal diagnostics are supported for the ESP32 reference. Full power/thermal/transient, effective capacitance, USB compliance and RF performance proofs remain unsupported and block manufacturing readiness.',
                    'Pinned Freerouting 2.4.1 / Java 25, fanout and optimizer disabled, observed pass count checked; 90-second timeout and 512MB heap.',
                    'Per-net track widths are bound to real DSN classes and KiCad custom DRC minima. Copper checks measure native geometry and report declared IPC-2221 / 20C straight-track reference screens only; no full via/pad/return-path, physical thickness or coupled thermal qualification.',
                    'Synchronous calls bounded by backend timeouts; no asynchronous job API yet.',
                    'Generic schematics use labeled component blocks. Automatic text collision checks remain unsupported.'])

    def search(self, request):
        with self.locked():
            selected=request.library
            prefix=request.query.split(':',1)[0] if ':' in request.query else None
            if selected is None and prefix in backend.library_paths():selected=prefix
            if selected:
                require(selected in backend.library_paths(),'SYMBOL_LIBRARY_NOT_AVAILABLE','本机或资料包中不存在该符号库。')
                path=self.root/'selected_symbol_libraries.json'
                write_json(path,sorted(set(registry.selected_libraries())|{selected}))
            self.catalog={c['candidate_id']:c for c in backend.catalog()}
        q=request.query.casefold()
        results=[self.public_candidate(c) for c in self.catalog.values()
                 if (not request.library or c['library_id'].startswith(request.library+':'))
                 and all(word in (c['library_id']+' '+c['symbol']+' '+c.get('description','')).casefold() for word in q.split())]
        exact=[c for c in results if q in [c['library_id'].casefold(),c['symbol'].casefold()]]
        if exact: results=exact
        results.sort(key=lambda c:(not c['supported'],c['library_id']))
        end=request.offset+request.limit
        return dict(candidates=results[request.offset:end],source_status='local_library_available',
                    total=len(results),next_offset=end if end<len(results) else None)

    def get_candidate(self, request):
        c=self.candidate(request.candidate_id)
        with self.locked():
            path=self.root/'resolved.json'
            resolved=json.loads(path.read_text()) if path.exists() else []
            if request.candidate_id not in resolved:
                resolved.append(request.candidate_id)
                write_json(path,resolved)
        return self.public_candidate(c)

    def project_path(self, pid):
        require(bool(re.fullmatch('[a-f0-9]{32}',pid)),'INVALID_ID','无效项目 ID。')
        return self.root/pid

    def load(self, pid):
        p=self.project_path(pid)/'state.json'
        require(p.is_file(),'PROJECT_NOT_FOUND','项目不存在。')
        state=json.loads(p.read_text())
        for key,default in [('requirements',[]),('constraints',{}),('pin_intents',{}),('power_sources',{}),('footprint_bindings',{}),('board_constraints',{}),('pcb_builds',{}),('part_bindings',{}),('operating_conditions',{}),('manufacturing_reviews',{}),('power_envelope',{}),('pcb_copper_policy',{}),('passive_rating_requirements',{}),('power_budget_policy',{})]: state.setdefault(key,default)
        return state

    def save(self, state):
        write_json(self.project_path(state['id'])/'state.json',state)

    def create(self, request):
        with self.locked():
            key = request.idempotency_key
            pid = create_project_id(key) if key else new_id()
            p=self.project_path(pid)
            result = dict(project_id=pid, revision=0, profile=request.profile)
            if key:
                fingerprint = request_fingerprint(request.model_dump())
                if p.exists():
                    require((p/'state.json').is_file(), 'CREATE_STATE_MISSING',
                            '该创建请求已有项目目录，但没有提交记录。')
                    receipt = self.load(pid).get('_creation_request')
                    require(receipt and receipt['fingerprint'] == fingerprint and receipt['result'] == result,
                            'IDEMPOTENCY_CONFLICT', '创建请求的幂等键已用于其他参数。')
                    return receipt['result']
            state=dict(id=pid,name=request.name,profile=request.profile,revision=0,
                       components={},nets={},builds={},idempotency={},requirements=[],constraints={},pin_intents={},power_sources={},footprint_bindings={},board_constraints={},pcb_builds={},part_bindings={},operating_conditions={},manufacturing_reviews={},power_envelope={})
            if key:
                state['_creation_request'] = dict(fingerprint=fingerprint, result=result,
                                                  state_sha256=request_fingerprint(state))
            publish_initial_project(p,state)
            return result

    def read(self, request):
        with self.locked():
            state=self.load(request.project_id)
            require(request.revision is None or state['revision']==request.revision,'REVISION_CONFLICT','所请求版本已过期。',current_revision=state['revision'])
            return {k:v for k,v in state.items() if k not in ('idempotency', '_creation_request')}

    def mutate(self, operation, request, change):
        with self.locked():
            state=self.load(request.project_id)
            fingerprint=backend.digest(json.dumps([operation,request.model_dump()],sort_keys=True).encode())
            previous=state['idempotency'].get(request.idempotency_key)
            if previous:
                require(previous['fingerprint']==fingerprint,'IDEMPOTENCY_CONFLICT','幂等键已用于另一操作。')
                return previous['result']
            require(state['revision']==request.expected_revision,'REVISION_CONFLICT','电路已被修改，请先读取当前版本。',current_revision=state['revision'])
            result=change(state)
            state['revision']+=1
            result=dict(project_id=state['id'],revision=state['revision'],**result)
            state['idempotency'][request.idempotency_key]=dict(fingerprint=fingerprint,result=result)
            self.save(state)
            return result

    def value(self, symbol, value, profile='led_series_experiment'):
        if profile=='schematic_experiment' and symbol not in ['R','R_US','C','C_Polarized','Battery_Cell','LED']:
            # A fixed library symbol's display value may not pretend to change its identity.
            require(value==symbol,'FIXED_SYMBOL_VALUE','此符号数值必须保持库型号；电气额定参数不能通过改显示名称伪造。')
            return value
        if profile=='schematic_experiment' and symbol in ['R','R_US','C','C_Polarized']:
            suffix='(?:F)?' if symbol.startswith('C') else '(?:Ω|ohm)?'
            match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([pnumkM]?)'+suffix,value)
            require(match is not None,'INVALID_PARAMETER','请输入明确的电阻或电容数值，如 470、4.7k、100nF。')
            scale={'':1,'p':1e-12,'n':1e-9,'u':1e-6,'m':1e-3,'k':1e3,'M':1e6}[match.group(2)]
            number=float(match.group(1))*scale
            require(math.isfinite(number) and 0<number<1e12,'PARAMETER_OUT_OF_RANGE','数值必须为有限正数。')
            return value
        if symbol=='LED':
            require(value=='Red','UNSUPPORTED_PARAMETER','第一版 LED 仅支持 Red。')
            return value
        pattern=r'([0-9]+(?:\.[0-9]+)?)V' if symbol=='Battery_Cell' else r'([0-9]+(?:\.[0-9]+)?)'
        match=re.fullmatch(pattern,value)
        require(match is not None,'INVALID_PARAMETER','电池使用例如 3V，电阻使用例如 330（欧姆）。')
        n=float(match.group(1))
        require(math.isfinite(n) and (0<n<=5 if symbol=='Battery_Cell' else 1<=n<=1_000_000),
                'PARAMETER_OUT_OF_RANGE','参数超出实验配置范围。')
        return f'{n:g}V' if symbol=='Battery_Cell' else f'{n:g}'

    def add(self, request):
        def change(s):
            c=self.candidate(request.candidate_id)
            resolved_path=self.root/'resolved.json'
            resolved=json.loads(resolved_path.read_text()) if resolved_path.exists() else []
            require(c['candidate_id'] in resolved,'CANDIDATE_NOT_RESOLVED','必须先调用 library_get 读取真实引脚。')
            require(not any(x['ref']==request.ref for x in s['components'].values()),'DUPLICATE_REFERENCE','位号已存在。')
            require(c.get('supported',True),'UNSUPPORTED_SYMBOL','符号暂不支持。',reasons=c.get('unsupported_reasons',[]))
            require(not c['library_id'].startswith('power:'),'DECLARATION_REQUIRED','电源标记只能由 power_source_set 的外部电源声明生成。')
            if s['profile']=='led_series_experiment':
                require(c['library_id'] in ['Device:'+n for n in backend.SYMBOLS],'PROFILE_LIMIT','LED 配置只支持原三种符号。')
                require(not any(x['symbol']==c['symbol'] for x in s['components'].values()),'PROFILE_LIMIT','LED 配置每种符号仅支持一个。')
            require(len(s['components'])<128,'PROFILE_LIMIT','每个项目最多 128 个元件，实际布局另受板框容量限制。')
            ident=new_id()
            instance=copy.deepcopy(c)
            instance.update(id=ident,ref=request.ref,value=self.value(c['symbol'],request.value,s['profile']))
            s['components'][ident]=instance
            return dict(component_id=ident,pins=c['pins'],completeness='symbol_only')
        return self.mutate('component_add',request,change)

    def parameters(self, request):
        def change(s):
            require(request.component_id in s['components'],'UNKNOWN_COMPONENT','元件不存在。')
            c=s['components'][request.component_id]
            require(c['id'] not in s['part_bindings'] or request.value==c['value'],'PART_BOUND_VALUE_LOCKED','先解除实物器件绑定，再修改其标称值。')
            c['value']=self.value(c['symbol'],request.value,s['profile'])
            return dict(component_id=c['id'],value=c['value'])
        return self.mutate('component_parameters_set',request,change)

    def create_net(self, request):
        def change(s):
            require(len(s['nets'])<(3 if s['profile']=='led_series_experiment' else 100),'PROFILE_LIMIT','网络数超过项目配置上限。')
            require(not any(n['name']==request.name for n in s['nets'].values()),'DUPLICATE_NET_NAME','网络名已存在。')
            ident=new_id()
            s['nets'][ident]=dict(id=ident,name=request.name,kind=request.kind,pins=[])
            return dict(net_id=ident)
        return self.mutate('net_create',request,change)

    def members(self, request):
        def change(s):
            require(request.net_id in s['nets'],'UNKNOWN_NET','网络不存在。')
            pins=[p.model_dump() for p in request.pins]
            require(len({(p['component_id'],p['pin_id']) for p in pins})==len(pins),'DUPLICATE_PIN','成员列表包含重复引脚。')
            for p in pins:
                require(p['component_id'] in s['components'],'UNKNOWN_COMPONENT','元件不存在。')
                c=s['components'][p['component_id']]
                require(any(x['pin_id']==p['pin_id'] for x in c['pins']),'UNKNOWN_PIN','引脚不属于该元件。',pin=p)
                require(next(x for x in c['pins'] if x['pin_id']==p['pin_id'])['electrical_type']!='no_connect','PIN_FUNCTION_NO_CONNECT','厂家/符号规定的 NC 引脚不可接入网络。')
                require(s['pin_intents'].get(p['component_id']+':'+p['pin_id'],{}).get('intent')!='no_connect',
                        'PIN_MARKED_NO_CONNECT','先移除该引脚的有意悬空标记。')
                conflicts=[n['id'] for n in s['nets'].values() if n['id']!=request.net_id and p in n['pins']]
                require(not conflicts,'PIN_ALREADY_CONNECTED','引脚已经连接到其他网络，先明确解除原连接。',conflicting_nets=conflicts)
            s['nets'][request.net_id]['pins']=pins
            return dict(net_id=request.net_id,members=pins)
        return self.mutate('net_members_set',request,change)

    def validate_led(self, state):
        findings=[]
        def add(code,message): findings.append(dict(code=code,severity='error',message=message))
        comps=list(state['components'].values())
        if sorted(c['symbol'] for c in comps)!=sorted(backend.SYMBOLS):
            add('INCOMPLETE_PROFILE','需要一个电池、一个电阻和一个 LED。')
        elif len(state['nets'])!=3:
            add('INCOMPLETE_NETS','需要三个明确的串联网络。')
        else:
            current={c['candidate_id']:c for c in backend.catalog(['Device'])}
            for c in comps:
                if c['candidate_id'] not in current:
                    add('LIBRARY_CHANGED','本机库已改变，必须重新选择候选。')
            by={c['symbol']:c for c in comps}
            def ref(sym,number):
                c=by[sym]
                p=next(p for p in c['pins'] if p['number']==number)
                return c['id'],p['pin_id']
            actual={frozenset((p['component_id'],p['pin_id']) for p in n['pins']) for n in state['nets'].values()}
            required={frozenset([ref('Battery_Cell','1'),ref('R','1')]),
                      frozenset([ref('R','2'),ref('LED','2')]),
                      frozenset([ref('LED','1'),ref('Battery_Cell','2')])}
            if actual!=required:
                add('LED_SERIES_POLARITY_OR_TOPOLOGY','连接必须为电池+→电阻1，电阻2→LED阳极2，LED阴极1→电池-。')
        return dict(verdict='fail' if findings else 'pass',revision=state['revision'],
                    rulepack='led_series_v1',findings=findings,
                    coverage=[dict(check_id='explicit_series_polarity',status='checked'),
                              dict(check_id='library_definition_current',status='checked' if len(comps)==3 and len(state['nets'])==3 else 'not_checked'),
                              dict(check_id='led_current_thermal_supply_limits',status='unsupported'),
                              dict(check_id='sku_footprint_manufacturing',status='unsupported')],
                    qualification='schematic_experiment_only')

    def check_pin(self,s,p):
        require(p['component_id'] in s['components'],'UNKNOWN_COMPONENT','元件不存在。')
        c=s['components'][p['component_id']]
        require(any(x['pin_id']==p['pin_id'] for x in c['pins']),'UNKNOWN_PIN','引脚不存在。')
        return c

    def validate_state(self,state,replacement_evidence=None):
        if state['profile']=='led_series_experiment':
            report=self.validate_led(state)
            assignment={(p['component_id'],p['pin_id']):n['id'] for n in state['nets'].values() for p in n['pins']}
            for c in state['constraints'].values():
                bound=[assignment.get((p['component_id'],p['pin_id'])) for p in c['pins']]
                ok=all(bound) and (len(set(bound))==1 if c['kind']=='must_connect' else len(set(bound))==len(bound))
                if not ok: report['findings'].append(dict(code='CONSTRAINT_UNSATISFIED',severity='error',message='显式连接约束未满足。',entity_ids=[c['id']]))
            if report['findings']: report['verdict']='fail'
            return report
        findings=[]
        def add(code,message,ids=None): findings.append(dict(code=code,severity='error',message=message,entity_ids=ids or []))
        if not state['components']: add('EMPTY_CIRCUIT','电路没有元件。')
        current={c['candidate_id']:c for c in backend.catalog()}
        pin_net={}
        for net in state['nets'].values():
            if len(net['pins'])<2: add('NET_TOO_SMALL','每个网络至少需要两个引脚。',[net['id']])
            for pin in net['pins']: pin_net[(pin['component_id'],pin['pin_id'])]=net['id']
        for c in state['components'].values():
            if c['candidate_id'] not in current: add('LIBRARY_CHANGED','库定义已变更，需重新选择。',[c['id']])
            groups={}
            for pin in c['pins']:
                key=(c['id'],pin['pin_id']); intent=state['pin_intents'].get(':'.join(key),{}).get('intent')
                if key not in pin_net and intent!='no_connect': add('PIN_INTENT_UNSPECIFIED','每个引脚必须连接或显式说明悬空。',[c['id'],pin['pin_id']])
                if key in pin_net and pin['electrical_type']=='no_connect': add('PIN_FUNCTION_NO_CONNECT','NC 功能引脚被连接。',[c['id'],pin['pin_id']])
                if key in pin_net and intent=='no_connect': add('CONFLICTING_PIN_INTENT','已连接引脚不能标为悬空。',[c['id'],pin['pin_id']])
                # Physically stacked pins cannot independently carry different nets.
                pos=tuple(float(v) for v in pin['at'][:2])
                groups.setdefault(pos,set()).add(pin_net.get(key,'NC' if intent=='no_connect' else 'UNSPECIFIED'))
            if any(len(v)>1 for v in groups.values()): add('STACKED_PIN_CONFLICT','同位置引脚必须有相同网络或悬空意图。',[c['id']])
        for constraint in state['constraints'].values():
            bound=[pin_net.get((p['component_id'],p['pin_id'])) for p in constraint['pins']]
            ok=all(bound) and (len(set(bound))==1 if constraint['kind']=='must_connect' else len(set(bound))==len(bound))
            if not ok: add('CONSTRAINT_UNSATISFIED','显式连接/隔离约束未满足。',[constraint['id']])
        for source in state['power_sources'].values():
            if not self.source_valid(state,source):add('POWER_BOUNDARY_DISCONNECTED','外部电源声明已不再匹配连接器及参考地。',[source['id']])
        from .copper import policy_findings
        findings.extend(policy_findings(state))
        e=electrical.evaluate(state)
        findings.extend(e['findings'])
        if state.get('power_envelope'):findings.extend(prototype_policy.findings_for_geometry(power_integrity.evaluate(state,replacement_evidence)['findings']))
        if state.get('power_budget_policy'):
            from .power_budget import evaluate as budget_evaluate
            findings.extend(f for f in budget_evaluate(state,self.bom_check_state(state),replacement_evidence)['findings'] if f['severity']=='error')
        if state.get('power_envelope') and any(c['library_id']=='PCB_MCP:AM2302_SIP4' for c in state['components'].values()):
            from .module_contracts import evaluate as module_evaluate
            findings.extend(module_evaluate(state)['findings'])
        if state.get('part_bindings') or state.get('operating_conditions'):
            # Preserve the existing fail-closed LED stress gate. Other circuits
            # may produce prototype geometry after identity/nominal checks;
            # full engineering remains a separate manufacturing/readiness gate.
            if (state.get('operating_conditions') or any(c['library_id']=='Device:LED' for c in state['components'].values())
                    or any(p['kind']=='led' for p in state['part_bindings'].values())):
                findings.extend(self.engineering_state(state)['findings'])
            else:
                findings.extend(self.bom_check_state(state)['findings'])
        return dict(verdict='fail' if any(f['severity']=='error' for f in findings) else 'pass',revision=state['revision'],rulepack='structural_and_nominal_electrical_v1',findings=findings,
                    coverage=[dict(check_id='real_library_and_pin_accounting',status='checked'),
                              dict(check_id='explicit_connectivity_constraints',status='checked'),
                              dict(check_id='free_text_functional_requirements',status='unsupported'),
                              dict(check_id='ratings_current_thermal_manufacturing',status='unsupported')]+e['coverage'],qualification=e['qualification'])

    def source_valid(self,s,source):
        boundary=source['boundary_pin']; c=s['components'].get(boundary['component_id'])
        power=s['nets'].get(source['net_id']); ground=s['nets'].get(source['ground_net_id'])
        if c and c['library_id']=='Device:Battery_Cell':
            positive=next(p for p in c['pins'] if p['number']=='1')
            negative=next(p for p in c['pins'] if p['number']=='2')
            if (boundary['pin_id']!=positive['pin_id'] or not ground
                or dict(component_id=c['id'],pin_id=negative['pin_id']) not in ground['pins']
                or electrical.number(c['value'])!=source['nominal_v']):return False
        return bool(c and power and ground and power['kind']=='power' and ground['kind']=='ground'
                    and power['id']!=ground['id'] and boundary in power['pins']
                    and (c['library_id'].startswith(('Connector:','Connector_Generic:')) or c['library_id']=='Device:Battery_Cell')
                    and any(p['component_id']==c['id'] for p in ground['pins']))

    def power_source(self,request):
        def change(s):
            require(s['profile']=='schematic_experiment','PROFILE_LIMIT','外部电源声明用于通用原理图配置。')
            self.check_pin(s,request.boundary_pin.model_dump())
            if request.source_id:require(request.source_id in s['power_sources'],'UNKNOWN_SOURCE','电源声明不存在。')
            ident=request.source_id or new_id()
            source=dict(id=ident,net_id=request.net_id,ground_net_id=request.ground_net_id,
                        boundary_pin=request.boundary_pin.model_dump(),nominal_v=request.nominal_v,reason=request.reason)
            require(self.source_valid(s,source),'INVALID_POWER_BOUNDARY','需使用已连接到电源网络及参考地的真实外部连接器或电池。')
            require(not any(x['id']!=ident and x['net_id']==source['net_id'] for x in s['power_sources'].values()),
                    'DUPLICATE_POWER_SOURCE','同一网络只能有一个外部电源声明。')
            s['power_sources'][ident]=source
            return dict(entity_id=ident)
        return self.mutate('power_source_set',request,change)

    def remove_source(self,request):
        def change(s):
            require(request.source_id in s['power_sources'],'UNKNOWN_SOURCE','电源声明不存在。')
            del s['power_sources'][request.source_id]
            return dict(entity_id=request.source_id)
        return self.mutate('power_source_remove',request,change)

    def rules_list(self):
        source_hash=electrical.curated_sources_hash()
        return dict(rulepacks=[electrical.rulepack(i,source_hash) for i in electrical.PACKS])

    def rules_get(self,request):
        return dict(rulepack=electrical.rulepack(request.rulepack_id))

    def electrical_check(self,request):
        with self.locked():
            s=self.load(request.project_id)
            require(request.revision is None or request.revision==s['revision'],'REVISION_CONFLICT','版本已变化。')
            report=electrical.evaluate(s)
            for source in s['power_sources'].values():
                if not self.source_valid(s,source):
                    report['verdict']='fail'
                    report['findings'].append(dict(code='POWER_BOUNDARY_DISCONNECTED',severity='error',message='外部电源声明与实际连接不符。',entity_ids=[source['id']]))
            return report

    def list_projects(self):
        with self.locked():
            projects=[]
            for path in sorted(self.root.glob('*/state.json')):
                s=self.load(path.parent.name)
                projects.append(dict(project_id=s['id'],name=s['name'],profile=s['profile'],revision=s['revision'],
                                     component_count=len(s['components']),net_count=len(s['nets'])))
            return dict(projects=projects)

    def requirements(self,request):
        def change(s):
            items=[i.model_dump() for i in request.items]
            require(len({i['key'] for i in items})==len(items),'DUPLICATE_REQUIREMENT','需求键不得重复。')
            require(all(not isinstance(i['value'],float) or math.isfinite(i['value']) for i in items),'INVALID_PARAMETER','需求值必须是有限数值。')
            s['requirements']=items
            return dict(items=items,verification='recorded_not_independently_verified')
        return self.mutate('requirements_set',request,change)

    def constraint(self,request):
        def change(s):
            value=request.constraint.model_dump()
            for p in value['pins']: self.check_pin(s,p)
            require(len({(p['component_id'],p['pin_id']) for p in value['pins']})==len(value['pins']),
                    'DUPLICATE_PIN','约束引脚不能重复。')
            if request.constraint_id: require(request.constraint_id in s['constraints'],'UNKNOWN_CONSTRAINT','约束不存在。')
            ident=request.constraint_id or new_id(); value['id']=ident; s['constraints'][ident]=value
            return dict(entity_id=ident)
        return self.mutate('constraint_set',request,change)

    def remove_constraint(self,request):
        def change(s):
            require(request.constraint_id in s['constraints'],'UNKNOWN_CONSTRAINT','约束不存在。')
            del s['constraints'][request.constraint_id]
            return dict(entity_id=request.constraint_id)
        return self.mutate('constraint_remove',request,change)

    def intent(self,request):
        def change(s):
            pin=request.pin.model_dump(); self.check_pin(s,pin)
            key=pin['component_id']+':'+pin['pin_id']
            if request.intent=='no_connect':
                require(bool(request.reason.strip()),'REASON_REQUIRED','显式悬空需要填写理由。')
                require(not any(pin in n['pins'] for n in s['nets'].values()),'PIN_ALREADY_CONNECTED','已连接引脚不能标为悬空。')
                s['pin_intents'][key]=dict(pin=pin,intent='no_connect',reason=request.reason)
            else: s['pin_intents'].pop(key,None)
            return dict(entity_id=key)
        return self.mutate('pin_intent_set',request,change)

    def remove_component(self,request):
        def change(s):
            ident=request.component_id; require(ident in s['components'],'UNKNOWN_COMPONENT','元件不存在。')
            refs=[p for n in s['nets'].values() for p in n['pins']]+[p for c in s['constraints'].values() for p in c['pins']]
            refs.extend(x['boundary_pin'] for x in s['power_sources'].values())
            require(not any(p['component_id']==ident for p in refs),'ENTITY_IN_USE','先解除元件网络和约束引用。')
            require(not any(i['pin']['component_id']==ident for i in s['pin_intents'].values()),'ENTITY_IN_USE','先清除元件引脚意图。')
            require(ident not in s['footprint_bindings'],'ENTITY_IN_USE','先解除封装绑定。')
            require(ident not in s['part_bindings'],'ENTITY_IN_USE','先解除实物器件绑定。')
            require(not any(r['component_id']==ident for r in s.get('passive_rating_requirements',{}).get('requirements',[])),'ENTITY_IN_USE','先清除该元件的额定要求。')
            budget=s.get('power_budget_policy',{})
            require(not any(r['pin']['component_id']==ident for r in budget.get('loads',[])) and not any(r['component_id']==ident for r in budget.get('stages',[])),'ENTITY_IN_USE','先清除该元件的供电预算引用。')
            del s['components'][ident]; return dict(entity_id=ident)
        return self.mutate('component_remove',request,change)

    def remove_net(self,request):
        def change(s):
            require(request.net_id in s['nets'],'UNKNOWN_NET','网络不存在。')
            require(not s['nets'][request.net_id]['pins'],'ENTITY_IN_USE','先清空网络成员。')
            require(not any(request.net_id in [x['net_id'],x['ground_net_id']] for x in s['power_sources'].values()),'ENTITY_IN_USE','先移除电源声明。')
            require(not any(r['net_id']==request.net_id for r in s.get('pcb_copper_policy',{}).get('rules',[])),'ENTITY_IN_USE','先在铜线规则集合中移除该网络。')
            require(not any(r['net_id']==request.net_id for r in s.get('power_budget_policy',{}).get('reserves',[])),'ENTITY_IN_USE','先清除该网络的供电预算余量。')
            del s['nets'][request.net_id]; return dict(entity_id=request.net_id)
        return self.mutate('net_remove',request,change)

    def validate(self, request):
        with self.locked():
            state=self.load(request.project_id)
            require(request.revision is None or state['revision']==request.revision,'REVISION_CONFLICT','版本已变化。')
            return self.validate_state(state)

    def build(self, request):
        with self.locked():
            s=self.load(request.project_id)
            fingerprint=backend.digest(json.dumps(['build',request.model_dump()],sort_keys=True).encode())
            old=s['idempotency'].get(request.idempotency_key)
            if old:
                require(old['fingerprint']==fingerprint,'IDEMPOTENCY_CONFLICT','幂等键冲突。')
                return old['result']
            require(s['revision']==request.expected_revision,'REVISION_CONFLICT','版本已变化。')
            report=self.validate_state(s)
            require(report['verdict']=='pass','SPEC_INVALID','规格未通过；先调用 spec_validate。',report=report)
            require(request.layout_profile!='led_loop' or s['profile']=='led_series_experiment','UNSUPPORTED_LAYOUT','通用电路不能使用 LED 固定回路布局。')
            ident=new_id()
            folder=self.project_path(s['id'])/'builds'/ident
            try:
                sha=backend.build(s,folder,request.layout_profile)
            except (RuntimeError,OSError,ValueError,KeyError,StopIteration) as e:
                raise DomainError('BUILD_FAILED',str(e),build_id=ident) from e
            write_json(folder/'spec_validation.json',report)
            write_json(folder/'electrical_validation.json',electrical.evaluate(s))
            write_json(folder/'rulepacks.json',self.rules_list())
            evidence=['spec.json','skidl.net','spec_validation.json','electrical_validation.json','rulepacks.json']
            if (folder/'fp-lib-table').is_file():evidence.append('fp-lib-table')
            if (folder/'sym-lib-table').is_file():evidence.append('sym-lib-table')
            if (folder/'led.kicad_pro').is_file():evidence.append('led.kicad_pro')
            if (folder/'power_flag_snapshot.json').is_file():evidence.append('power_flag_snapshot.json')
            entry=dict(id=ident,revision=s['revision'],schematic_hash=sha,checks={},
                       source_hashes={name:backend.digest((folder/name).read_bytes())
                                      for name in evidence})
            s['builds'][ident]=entry
            result=dict(project_id=s['id'],revision=s['revision'],build_id=ident,schematic_hash=sha)
            s['idempotency'][request.idempotency_key]=dict(fingerprint=fingerprint,result=result)
            self.save(s)
            return result

    def target(self, request):
        s=self.load(request.project_id)
        require(request.build_id in s['builds'],'UNKNOWN_BUILD','构建不存在或未成功。')
        b=s['builds'][request.build_id]
        require(b['revision']==s['revision'],'STALE_VALIDATION','构建已落后于当前电路版本。',current_revision=s['revision'])
        folder=self.project_path(s['id'])/'builds'/b['id']
        require(backend.digest((folder/'led.kicad_sch').read_bytes())==b['schematic_hash'],
                'ARTIFACT_CHANGED','原理图被外部修改，原验证不得继续使用。')
        for name,sha in b['source_hashes'].items():
            require(backend.digest((folder/name).read_bytes())==sha,'ARTIFACT_CHANGED','构建输入或生成证据被外部修改。')
        if 'rulepacks.json' in b['source_hashes']:
            require(json.loads((folder/'rulepacks.json').read_text())==self.rules_list(),
                    'RULEPACK_CHANGED','电气检查实现或规则证据已变化，需重新构建和校验。')
            try:
                parts.native_cad_fields(s)
            except ValueError as exc:
                raise DomainError('BUILD_PART_EVIDENCE_CHANGED','构建所用器件的来源、符号或封装已变化，需重新构建和校验。',detail=str(exc)) from exc
        return s,b,folder

    def verify(self, request):
        with self.locked():
            s,b,folder=self.target(request)
            run=backend.command(['sch','export','netlist','--format','kicadxml','-o','actual.xml','led.kicad_sch'],folder)
            write_json(folder/'netlist_command.json',run)
            require(run['exit_code']==0,'BACKEND_FAILED','KiCad 网表导出失败。')
            actual=backend.xml_nets(folder/'actual.xml')
            expected=backend.expected_nets(s)
            # Tuples round-trip through JSON as arrays; normalize before comparing.
            normalize=lambda x: json.loads(json.dumps(x,sort_keys=True))
            tree=ET_parse_components(folder/'actual.xml')
            expected_comps={c['ref']:(c['value'],c['library_id'].split(':',1)[0],c['symbol']) for c in s['components'].values()}
            nc_match=backend.no_connect_matches(s,folder/'led.kicad_sch')
            from . import no_connects
            try:native_nc=no_connects.read(s,folder/'actual.xml');nc_error=None
            except ValueError as error:native_nc=[];nc_error=str(error);nc_match=False
            native_fields=backend.native_source_field_report(s,folder/'actual.xml')
            passed=normalize(actual)==normalize(expected) and tree==expected_comps and nc_match and native_fields['matched']
            report=dict(verdict='pass' if passed else 'fail',revision=s['revision'],build_id=b['id'],
                        expected_nets=expected,actual_nets=actual,components_match=tree==expected_comps,
                        no_connect_match=nc_match,
                        native_no_connect_nets=native_nc,native_no_connect_error=nc_error,
                        native_source_fields=native_fields,
                        schematic_hash=b['schematic_hash'],engine='KiCad XML netlist')
            write_json(folder/'verify.json',report)
            b['checks']['verify']=dict(verdict=report['verdict'],files={
                name:backend.digest((folder/name).read_bytes()) for name in ['actual.xml','verify.json']})
            self.save(s)
            return report

    def erc(self, request):
        with self.locked():
            s,b,folder=self.target(request)
            run=backend.command(['sch','erc','--format','json','--severity-all','--exit-code-violations','-o','erc.json','led.kicad_sch'],folder)
            write_json(folder/'erc_command.json',run)
            require(run['exit_code'] in (0,5),'BACKEND_FAILED','KiCad ERC 执行失败。')
            raw=json.loads((folder/'erc.json').read_text())
            findings=[v for sheet in raw['sheets'] for v in sheet['violations']]
            report=dict(verdict='pass' if not findings and run['exit_code']==0 else 'fail',
                        revision=s['revision'],build_id=b['id'],findings=findings,
                        ignored_checks=raw.get('ignored_checks',[]),engine_version=raw['kicad_version'])
            b['checks']['erc']=dict(verdict=report['verdict'],files={'erc.json':backend.digest((folder/'erc.json').read_bytes())})
            self.save(s)
            return report

    def preview(self, request):
        with self.locked():
            s,b,folder=self.target(request)
            run=backend.command(['sch','export','svg','--exclude-drawing-sheet','-o','preview','led.kicad_sch'],folder)
            write_json(folder/'preview_command.json',run)
            require(run['exit_code']==0,'BACKEND_FAILED','预览导出失败。')
            artifact=folder/'preview/led.svg'
            require(artifact.is_file(),'BACKEND_FAILED','预览缺失。')
            report=dict(verdict='incomplete',coverage=[dict(check_id='automatic_text_collision',status='unsupported')],
                        note='后端已导出预览；自动文字碰撞检测未实现，本次图形仍需目视审阅。')
            b['checks']['preview']=dict(verdict='produced',files={'preview/led.svg':backend.digest(artifact.read_bytes())})
            self.save(s)
            return dict(artifact_id='preview',sha256=backend.digest(artifact.read_bytes()),
                        resource_uri=f'pcb://{s["id"]}/{b["id"]}/preview',layout_report=report)

    def export(self, request):
        with self.locked():
            s,b,folder=self.target(request)
            for key,verdict in [('verify','pass'),('erc','pass'),('preview','produced')]:
                check=b['checks'].get(key,{})
                require(check.get('verdict')==verdict,'VALIDATION_REQUIRED',f'缺少当前构建的 {key} 检查。')
                for filename,sha in check['files'].items():
                    require(backend.digest((folder/filename).read_bytes())==sha,'ARTIFACT_CHANGED','校验文件或预览被修改。')
            require(self.validate_state(s)['verdict']=='pass','SPEC_INVALID','当前元件库或规格不再有效。')
            files=['led.kicad_sch','spec.json','skidl.net','actual.xml','erc.json','verify.json','spec_validation.json','preview/led.svg','skidl.log','netlist_command.json','erc_command.json','preview_command.json']
            files.extend(n for n in ['electrical_validation.json','rulepacks.json','power_flag_snapshot.json'] if (folder/n).is_file())
            manifest=dict(profile=s['profile'],revision=s['revision'],build_id=b['id'],
                          qualification='connectivity_and_erc_validated_only',
                          not_manufacturing_ready=True,visual_review_required=True,
                          electrical_qualification=electrical.evaluate(s)['qualification'],
                          unresolved_findings=electrical.evaluate(s)['findings'],
                          limitations=self.capabilities()['limitations'],
                          hashes={name:backend.digest((folder/name).read_bytes()) for name in files})
            write_json(folder/'manifest.json',manifest)
            with zipfile.ZipFile(folder/'review_bundle.zip','w',zipfile.ZIP_DEFLATED) as archive:
                for name in files+['manifest.json']:
                    archive.write(folder/name,name)
            return dict(build_id=b['id'],revision=s['revision'],bundle_path=str(folder/'review_bundle.zip'),
                        schematic_path=str(folder/'led.kicad_sch'),manifest=manifest)

    def artifact(self, request):
        with self.locked():
            s,b,folder=self.target(request)
            names={'schematic':'led.kicad_sch','preview':'preview/led.svg','netlist':'actual.xml','erc':'erc.json','manifest':'manifest.json'}
            p=folder/names[request.artifact_id]
            require(p.is_file(),'ARTIFACT_NOT_READY','请先运行对应生成工具。')
            require(p.stat().st_size<=512_000,'ARTIFACT_TOO_LARGE','文件超过内联读取限制。')
            return dict(artifact_id=request.artifact_id,sha256=backend.digest(p.read_bytes()),content=p.read_text())


def ET_parse_components(path):
    import xml.etree.ElementTree as ET
    result={}
    for c in ET.parse(path).findall('./components/comp'):
        if c.attrib['ref'].startswith('#'):continue
        lib=c.find('libsource')
        result[c.attrib['ref']]=(c.findtext('value'),lib.attrib['lib'],lib.attrib['part'])
    return result


