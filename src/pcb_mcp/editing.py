"""Reviewable, atomic edits; no build or approval is inherited by a fork."""
import copy
import json
from pathlib import Path
from . import backend, footprints, parts
from .common import fork_project_id, publish_initial_project, require, request_fingerprint, write_json

SPEC_KEYS = ('profile', 'components', 'nets', 'requirements', 'constraints',
             'pin_intents', 'power_sources', 'footprint_bindings',
             'board_constraints', 'part_bindings', 'operating_conditions',
             'power_envelope','pcb_copper_policy','passive_rating_requirements','power_budget_policy')


def replacement_target(request):
    if hasattr(request, 'replacements'):
        return [row.model_dump() for row in request.replacements]
    return {key: getattr(request, key) for key in ('component_id', 'candidate_id', 'footprint_candidate_id')}


def spec_hash(state):
    return backend.digest(json.dumps({k: state.get(k) for k in SPEC_KEYS},
                                     sort_keys=True).encode())


def initial_fork_state(source, request):
    state = copy.deepcopy(source)
    state.pop('_creation_request', None)
    state.update(id=fork_project_id(request.project_id, request.idempotency_key),
                 name=request.name, revision=0, builds={}, pcb_builds={},
                 manufacturing_reviews={}, idempotency={})
    state['provenance'] = dict(parent_project_id=source['id'], parent_revision=source['revision'],
                              parent_spec_sha256=spec_hash(source), approvals_inherited=False)
    return state


def untouched_initial_fork(folder, state):
    try:
        builds = folder/'builds'
        state_file = folder/'state.json'
        return (not folder.is_symlink() and {path.name for path in folder.iterdir()}=={'builds','state.json'}
                and not builds.is_symlink() and builds.is_dir() and not any(builds.iterdir())
                and not state_file.is_symlink() and json.loads(state_file.read_bytes())==state)
    except (OSError, ValueError):
        return False


def moved_net_pins(state, moves):
    """Resolve all moves against the same snapshot before updating any net."""
    original = state['nets']
    seen = set()
    for move in moves:
        source, destination = move['from_net_id'], move['to_net_id']
        require(source in original and destination in original, 'UNKNOWN_NET', '网络不存在。')
        require(source != destination, 'PIN_MOVE_SAME_NET', '源网络与目标网络必须不同。')
        pin = move['pin']
        key = (pin['component_id'], pin['pin_id'])
        require(key not in seen, 'DUPLICATE_PIN', '同一引脚不能重复迁移。', pin=pin)
        require([net_id for net_id, net in original.items() if pin in net['pins']] == [source],
                'PIN_MOVE_SOURCE_CHANGED', '引脚未唯一连接到声明的源网络。', pin=pin, from_net_id=source)
        seen.add(key)
    affected = {move[key] for move in moves for key in ('from_net_id', 'to_net_id')}
    nets = original.copy()
    for net_id in affected:
        kept = [pin for pin in original[net_id]['pins'] if (pin['component_id'], pin['pin_id']) not in seen]
        incoming = [move['pin'] for move in moves if move['to_net_id'] == net_id]
        pins = [*kept, *incoming]
        require(2 <= len(pins) <= 200, 'PIN_MOVE_NET_SIZE', '改线后的网络必须包含 2 至 200 个引脚。',
                net_id=net_id, pin_count=len(pins))
        nets[net_id] = dict(original[net_id], pins=pins)
    return nets


def references_removed(value, removed):
    if isinstance(value, str):
        return value in removed
    if isinstance(value, dict):
        return any(references_removed(item, removed) for item in value.values())
    if isinstance(value, list):
        return any(references_removed(item, removed) for item in value)
    return False


def removed_circuit(state, component_ids):
    removed = set(component_ids)
    require(len(removed) == len(component_ids), 'DUPLICATE_COMPONENT', '移除的元件不得重复。')
    require(removed <= state['components'].keys(), 'UNKNOWN_COMPONENT', '移除的元件不存在。')
    require(state['components'].keys() - removed, 'CIRCUIT_REMOVE_ALL_COMPONENTS', '必须保留可验证的电路。')
    nets, removed_nets = {}, set()
    for net_id, net in state['nets'].items():
        pins = [pin for pin in net['pins'] if pin['component_id'] not in removed]
        if pins != net['pins']:
            if not pins:
                removed_nets.add(net_id)
                continue
            require(len(pins) >= 2, 'CIRCUIT_REMOVE_DANGLING_NET', '保留的网络至少需要两个引脚。', net=net['name'])
        nets[net_id] = dict(net, pins=pins)
    board = state['board_constraints'].copy()
    if 'placements' in board:
        board['placements'] = [row for row in board['placements'] if row['component_id'] not in removed]
    for field in ('constraints', 'power_sources', 'operating_conditions', 'power_envelope',
                  'pcb_copper_policy', 'power_budget_policy'):
        require(not references_removed(state.get(field), removed | removed_nets),
                'CIRCUIT_REMOVE_DEPENDENCY', '待移除电路仍被其他设计约束引用。', field=field)
    require(not references_removed(board, removed | removed_nets),
            'CIRCUIT_REMOVE_DEPENDENCY', '待移除电路仍被布板约束引用。', field='board_constraints')
    proposed = dict(state, nets=nets, board_constraints=board)
    for field in ('components', 'part_bindings', 'footprint_bindings'):
        proposed[field] = {key: value for key, value in state[field].items() if key not in removed}
    proposed['pin_intents'] = {key: value for key, value in state['pin_intents'].items()
                               if value['pin']['component_id'] not in removed}
    ratings = state['passive_rating_requirements']
    if 'requirements' in ratings:
        proposed['passive_rating_requirements'] = dict(ratings, requirements=[row for row in ratings['requirements']
                                                                           if row['component_id'] not in removed])
    return proposed


class EditingService:
    def remove_circuit(self, request):
        def change(state):
            proposed = removed_circuit(state, request.component_ids)
            require(all(self.source_valid(proposed, source) for source in state['power_sources'].values()),
                    'POWER_BOUNDARY_DISCONNECTED', '移除电路会破坏现有的外部电源连接。')
            state.update(proposed)
            return dict(entity_id=state['id'])
        return self.mutate('circuit_remove', request, change)

    def move_net_pins(self, request):
        def change(state):
            for move in request.moves:
                self.check_pin(state, move.pin.model_dump())
            proposed = dict(state, nets=moved_net_pins(state, [move.model_dump() for move in request.moves]))
            require(all(self.source_valid(proposed, source) for source in state['power_sources'].values()),
                    'POWER_BOUNDARY_DISCONNECTED', '改线破坏现有的外部电源连接。')
            state['nets'] = proposed['nets']
            return dict(entity_id=state['id'])
        return self.mutate('net_pins_move', request, change)

    def project_fork(self, request):
        with self.locked():
            source = self.load(request.project_id)
            ledger_path = self.root / 'fork_requests.json'
            ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
            require(isinstance(ledger, dict), 'FORK_LEDGER_INVALID', '改版分支回执格式无效。')
            key = request.project_id + ':' + request.idempotency_key
            fingerprint = request_fingerprint(request.model_dump())
            if key in ledger:
                old = ledger[key]
                require(old['fingerprint'] == fingerprint, 'IDEMPOTENCY_CONFLICT', '副本请求的幂等键已用于其他参数。')
                require((self.project_path(old['result']['project_id']) / 'state.json').is_file(),
                        'FORK_MISSING', '原请求创建的副本已丢失，不能静默重新创建。')
                return old['result']
            require(source['revision'] == request.expected_revision,
                    'REVISION_CONFLICT', '原项目版本已变化。', current_revision=source['revision'])
            # A fork carries specifications, never source files or receipts.
            state = initial_fork_state(source, request)
            folder = self.project_path(state['id'])
            if folder.exists() or folder.is_symlink():
                require(untouched_initial_fork(folder,state), 'FORK_STATE_CHANGED',
                        '未登记的改版分支与原请求不一致，无法恢复。')
            else:
                publish_initial_project(folder,state)
            result = dict(project_id=state['id'], revision=0, profile=state['profile'],
                          provenance=state['provenance'])
            ledger[key] = dict(fingerprint=fingerprint, result=result,
                               state_sha256=request_fingerprint(state))
            write_json(ledger_path, ledger)
            return result

    def replacement_snapshot(self, state, component_id, candidate_id, footprint_candidate_id):
        require(component_id in state['components'], 'UNKNOWN_COMPONENT', '元件不存在。')
        part, _, _ = self.part_candidate(candidate_id, True)
        row, _, _ = self.footprint_candidate(footprint_candidate_id, True)
        fp = row['candidate']
        component = state['components'][component_id]
        require(part['evidence_status'] == 'available', 'PART_EVIDENCE_UNAVAILABLE', '器件资料快照缺失或已变化。')
        require(component['library_id'] == part['symbol_id'], 'REPLACEMENT_SYMBOL_MISMATCH',
                '本工具仅支持同一真实符号的替换，不重新猜测引脚功能。')
        current = {c['candidate_id']: c for c in backend.catalog()}
        require(component['candidate_id'] in current, 'LIBRARY_CHANGED', '元件符号库已改变。')
        require(fp['supported'] and fp['footprint_id'] == part['footprint_id'] and
                fp['definition_hash'] == part['facts']['footprint_definition_hash'],
                'PART_BINDING_MISMATCH', '替换必须使用该实物型号指定且已读取的封装。')
        require(footprints.binding_matches(component, fp), 'FOOTPRINT_ROLE_MISMATCH', '封装不符合符号库的筛选条件。')
        require(footprints.pins(component) == {p['number'] for p in fp['pads']},
                'PAD_PIN_MISMATCH', '替换封装的引脚编号不完全相同。')
        proposed = copy.deepcopy(state)
        value = (format(part['facts']['value_ohm'], '.12g') if part['kind'] == 'resistor'
                 else format(part['facts']['capacitance_f'], '.12f').rstrip('0')+'F' if part['kind']=='capacitor'
                 else component['symbol'])
        proposed['components'][component_id]['value'] = self.value(component['symbol'], value, state['profile'])
        proposed['part_bindings'][component_id] = copy.deepcopy(part)
        proposed['footprint_bindings'][component_id] = dict(
            copy.deepcopy(fp), mapping_mode='number_identity', purpose='geometry_experiment',
            reason='Atomic source-linked replacement; physical fit remains subject to review.',
            qualification='number_identity_geometry_only_not_mpn_verified')
        before = dict(value=component['value'], part=state['part_bindings'].get(component_id),
                      footprint=state['footprint_bindings'].get(component_id))
        after = dict(value=value, part=part, footprint=proposed['footprint_bindings'][component_id])
        return proposed, before, after

    def replacement_group_snapshot(self, state, targets):
        require(len({target['component_id'] for target in targets}) == len(targets),
                'DUPLICATE_REPLACEMENT_COMPONENT', '一个器件只能替换一次。')
        proposed, changes = state, []
        for target in targets:
            proposed, before, after = self.replacement_snapshot(proposed, **target)
            component_id = target['component_id']
            changes.append(dict(component_id=component_id, reference=state['components'][component_id]['ref'],
                                before=before, after=after))
        return proposed, changes

    def replacement_evidence(self, state, proposed, target, run_id, backend_sha256):
        from . import model_power, verification_models
        folder=self.project_path(state['id'])
        index=folder/'functional_index.json'
        entries=json.loads(index.read_text()) if index.is_file() else []
        matching=[entry for entry in entries if entry.get('scope')=='replacement' and entry['run_id']==run_id]
        require(len(matching)==1,'REPLACEMENT_FUNCTIONAL_RUN_NOT_FOUND',
                '未找到本工程的候选替换功能运行。')
        entry=matching[0]
        report=model_power.read_report(folder,entry)
        key = 'replacements' if isinstance(target, list) else 'replacement'
        require(report.get('run_id')==run_id and report.get('project_id')==state['id']
                and report.get('revision')==state['revision'] and report.get(key)==target
                and report.get('base_spec_sha256')==spec_hash(state)
                and report.get('spec_sha256')==spec_hash(proposed)
                and report.get('replacement_base_current') is True,
                'REPLACEMENT_FUNCTIONAL_SNAPSHOT_CHANGED','验证证据与当前替换快照不一致。')
        require(report['verdict']=='pass','REPLACEMENT_FUNCTIONAL_CHECK_FAILED',
                '候选功能契约未通过。',findings=report['findings'])
        models=verification_models.packages()
        require(report.get('backend_sha256')==backend_sha256
                and all(model['model_id'] in models for run in report['runs']
                        for model in run.get('models',[]) if model.get('model_id'))
                and all(model_power.runtime_matches(run) for run in report['runs']),
                'REPLACEMENT_FUNCTIONAL_ENVIRONMENT_CHANGED','候选求解器、资料或模型已变化，需重新验证。')
        return entry, report

    def replacement_plan(self, state, target, functional_run_id=None):
        grouped = isinstance(target, list)
        proposed, changes = self.replacement_group_snapshot(state, target if grouped else [target])
        backend_sha256=parts.rules_hash()
        if functional_run_id is None:
            report=self.validate_state(proposed)
        else:
            evidence=self.replacement_evidence(state,proposed,target,functional_run_id,backend_sha256)
            report=self.validate_state(proposed,evidence)
            report['replacement_functional_evidence']=dict(
                run_id=functional_run_id,report_sha256=evidence[0]['report_sha256'],
                contract_sha256=evidence[1]['contract_sha256'])
        plan = dict(project_id=state['id'], revision=state['revision'],
                    original_spec_sha256=spec_hash(state), proposed_spec_sha256=spec_hash(proposed),
                    engineering_evidence_hash=backend_sha256,
                    policy_sha256=backend.digest(Path(__file__).read_bytes()),
                    connectivity_preserved=state['nets'] == proposed['nets'],
                    specification=report, compatible=report['verdict'] == 'pass',
                    requires_rebuild_and_new_approval=True,
                    qualification='source_linked_replacement_not_physical_fit_or_manufacturing_approval')
        if grouped:
            plan['replacements'] = changes
        else:
            plan.update(changes[0])
        plan['plan_sha256'] = backend.digest(json.dumps(plan, sort_keys=True).encode())
        return plan, proposed

    def replacement_preview(self, request):
        with self.locked():
            state = self.load(request.project_id)
            require(state['revision'] == request.revision, 'REVISION_CONFLICT', '版本已变化。')
            return self.replacement_plan(state, replacement_target(request), request.functional_run_id)[0]

    def replacement_apply(self, request):
        def change(state):
            target = replacement_target(request)
            plan, proposed = self.replacement_plan(state, target, request.functional_run_id)
            require(plan['plan_sha256'] == request.plan_sha256, 'REPLACEMENT_PLAN_CHANGED',
                    '预览对象、元件证据或检查规则已变化；需重新预览。')
            require(plan['compatible'], 'REPLACEMENT_VALIDATION_FAILED',
                    '替换后的规格/电气检查未通过；原项目没有修改。', report=plan['specification'])
            for key in ('components', 'part_bindings', 'footprint_bindings'):
                state[key] = proposed[key]
            # Revision invalidation is performed once by mutate, including for
            # old PCB checks and manufacturing signatures. Preserve their history.
            result = dict(plan_sha256=plan['plan_sha256'], connectivity_preserved=True, approvals_invalidated=True)
            if isinstance(target, list):
                result['replacements'] = [dict(component_id=row['component_id'], value=row['after']['value'],
                                               mpn=row['after']['part']['mpn']) for row in plan['replacements']]
            else:
                result.update(entity_id=target['component_id'], value=plan['after']['value'],
                              mpn=plan['after']['part']['mpn'])
            return result
        return self.mutate('component_part_replace', request, change)
