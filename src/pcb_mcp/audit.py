"""Re-run actual checks against explicit artifacts; never build, route or approve."""
from datetime import datetime, timezone
from . import backend
from .common import DomainError, require, new_id, write_json
from .editing import spec_hash
from .models import Read, BuildRef, PCBRef, PCBStage
from .placement_rules import ESP, SENSOR, LDO, USB, BUCK


def led_engineering_applies(state):
    # This particular checker only implements LED DC stress. A board without
    # LEDs still has its specification, source identities, power and copper
    # checks; absence of a LED is not a missing LED supply declaration.
    return (state['profile']=='led_series_experiment'
            or any(c['library_id']=='Device:LED' for c in state['components'].values())
            or any(p.get('kind')=='led' for p in state.get('part_bindings',{}).values()))


class AuditService:
    def design_audit(self, request):
        initial = self.read(Read(project_id=request.project_id, revision=request.revision))
        revision = initial['revision']
        digest = spec_hash(initial)
        stages = []

        def run(name, operation):
            try:
                result = operation()
                status = result.get('verdict', 'pass')
                stages.append(dict(check_id=name, status=status, result=result))
            except DomainError as error:
                stages.append(dict(check_id=name, status='blocked', error=error.payload))

        current_sch = [b['id'] for b in initial['builds'].values() if b['revision'] == revision]
        current_pcb = [b['id'] for b in initial['pcb_builds'].values() if b['revision'] == revision]
        pcb_id = request.pcb_build_id
        sch_id = request.schematic_build_id
        if pcb_id is not None:
            require(pcb_id in initial['pcb_builds'], 'UNKNOWN_PCB_BUILD', '指定 PCB 不存在。')
            linked = initial['pcb_builds'][pcb_id]['schematic_build_id']
            require(sch_id is None or sch_id == linked, 'AUDIT_BUILD_MISMATCH', '原理图与 PCB 不属于同一条构建链。')
            sch_id = linked
        elif len(current_pcb) == 1 and sch_id is None:
            pcb_id = current_pcb[0]
            sch_id = initial['pcb_builds'][pcb_id]['schematic_build_id']
        elif len(current_pcb) > 1 and sch_id is None:
            require(False, 'AUDIT_TARGET_AMBIGUOUS', '有多个当前 PCB，请明确选择 pcb_build_id。')
        if sch_id is None:
            require(len(current_sch) <= 1, 'AUDIT_TARGET_AMBIGUOUS', '有多个当前原理图，请明确选择 schematic_build_id。')
            sch_id = current_sch[0] if current_sch else None
        if sch_id is not None:
            require(sch_id in initial['builds'], 'UNKNOWN_BUILD', '指定原理图不存在。')
        if pcb_id is None and sch_id is not None:
            linked_boards = [p for p in current_pcb if initial['pcb_builds'][p]['schematic_build_id'] == sch_id]
            require(len(linked_boards) <= 1, 'AUDIT_TARGET_AMBIGUOUS', '该原理图有多个当前 PCB，请明确选择 pcb_build_id。')
            pcb_id = linked_boards[0] if linked_boards else None
        read = Read(project_id=initial['id'], revision=revision)
        run('SPECIFICATION', lambda: self.validate(read))
        run('ELECTRICAL', lambda: self.electrical_check(read))
        if led_engineering_applies(initial):
            run('ENGINEERING', lambda: self.engineering_check(read))
        else:
            stages.append(dict(check_id='ENGINEERING',status='not_applicable',result=dict(qualification='LED_DC_stress_checker_not_applicable_no_LEDs'),reason='This checker only models LED branches; no LED profile, symbol or bound LED part is present. Other engineering and physical checks retain their own results.'))
        def bom_identity():
            report=self.bom_check(read)
            return dict(verdict=report['identity_verdict'],report=report)
        run('BOM_IDENTITY',bom_identity)
        # The separate series-LED engineering pack covers DC bounds. The
        # ESP32 nominal rule pack has no applicable component on that circuit;
        # retain its incomplete result and state why that pack is inapplicable.
        electrical_stage = next(s for s in stages if s['check_id'] == 'ELECTRICAL')
        engineering_stage = next(s for s in stages if s['check_id'] == 'ENGINEERING')
        if (initial.get('operating_conditions', {}).get('profile') == 'led_indicator_dc_v1'
                and engineering_stage['status'] == 'pass'
                and electrical_stage.get('result', {}).get('qualification') == 'no_applicable_electrical_rules'):
            electrical_stage['status'] = 'not_applicable'
            electrical_stage['reason'] = 'No ESP32 nominal pack applies; restricted LED DC checks are reported separately under ENGINEERING.'
        complex_board = any(c['library_id'] in [ESP, SENSOR, LDO, USB, BUCK, 'Sensor:AM2302']
                            for c in initial['components'].values())
        if complex_board:
            run('POWER_INTEGRITY', lambda: self.power_check(read))
        if any(c['library_id']==USB for c in initial['components'].values()):
            run('USB_SINK_POWER', lambda: self.usb_power_check(read))
            from .usb_gate_contract import present
            if present(initial):run('USB_HARDWARE_INTERLOCK',lambda: self.usb_gate_check(read))
        if any(c['library_id']=='Logic_LevelTranslator:TXS0102DCT' for c in initial['components'].values()):
            run('MODULE_PORT_CONTRACT', lambda: self.module_check(read))
        if sch_id:
            target = BuildRef(project_id=initial['id'], build_id=sch_id)
            run('ACTUAL_NETLIST', lambda: self.verify(target))
            run('ACTUAL_ERC', lambda: self.erc(target))
        else:
            stages.append(dict(check_id='CURRENT_SCHEMATIC', status='blocked',
                               error=dict(code='SCHEMATIC_REQUIRED', message='先生成当前原理图，再运行审计。')))
        if pcb_id:
            target = PCBRef(project_id=initial['id'], pcb_build_id=pcb_id)
            stage = PCBStage(**target.model_dump())
            run('ACTUAL_PCB_NETS', lambda: self.pcb_verify(stage))
            run('ACTUAL_DRC', lambda: self.pcb_drc(target))
            if complex_board or initial.get('pcb_copper_policy'):run('ACTUAL_COPPER',lambda: self.copper_check(target))
            if any(c['library_id'] == ESP for c in initial['components'].values()):
                run('ACTUAL_RF_AND_DECOUPLING', lambda: self.layout_check(stage))
        else:
            stages.append(dict(check_id='CURRENT_PCB', status='blocked',
                               error=dict(code='PCB_REQUIRED', message='尚无当前、已布线的 PCB。')))
        # Individual tools lock their own artifact/state writes. Recheck the
        # snapshot under the project lock before publishing an audit report.
        with self.locked():
            final = self.load(initial['id'])
            require(final['revision'] == revision and spec_hash(final) == digest,
                    'REVISION_CONFLICT', '审计期间规格变化；不发布混合版本报告。')
        readiness = self.readiness(read)
        # Readiness can refer to the newest board. An audit of another explicit
        # board must still require preview evidence on that exact board.
        if pcb_id:
            with self.locked():
                state = self.load(initial['id'])
                board = state['pcb_builds'][pcb_id]
                preview = board['checks'].get('preview', {})
                folder = self.project_path(initial['id']) / 'pcb_builds' / pcb_id
                valid = preview.get('verdict') == 'produced' and bool(preview.get('files'))
                valid = valid and all((folder / n).is_file() and backend.digest((folder / n).read_bytes()) == sha
                                      for n, sha in preview.get('files', {}).items())
            stages.append(dict(check_id='EXACT_PCB_PREVIEW', status='pass' if valid else 'blocked'))
        blockers = [s for s in stages if s['status'] not in ('pass', 'not_applicable')]
        report = dict(project_id=initial['id'], revision=revision, audit_id=new_id(),
                      created_at=datetime.now(timezone.utc).isoformat(), spec_sha256=digest,
                      schematic_build_id=sch_id, pcb_build_id=pcb_id, stages=stages,
                      readiness=readiness,
                      status='blocked' if blockers or readiness['status'] != 'ready_for_human_review'
                      else 'ready_for_human_review',
                      manufacturing_approved=False, gerber_generated=False, ordered=False,
                      qualification='snapshot_of_fresh_checks_not_physical_or_production_qualification')
        with self.locked():
            final = self.load(initial['id'])
            require(final['revision'] == revision and spec_hash(final) == digest,
                    'REVISION_CONFLICT', '审计期间规格变化；不发布混合版本报告。')
            # Bind the published snapshot to the checked artifacts. Do not
            # claim ready if files changed after the individual backend calls.
            integrity = []
            if sch_id:
                try:
                    _, b, source = self.target(BuildRef(project_id=initial['id'], build_id=sch_id))
                    for key in ('verify', 'erc'):
                        check = b['checks'].get(key, {})
                        for name, sha in check.get('files', {}).items():
                            require((source / name).is_file() and backend.digest((source / name).read_bytes()) == sha,
                                    'ARTIFACT_CHANGED', '审计中的原理图报告已经变化。')
                    integrity.append(dict(artifact='schematic', source_hashes=b['source_hashes'],
                                          schematic_hash=b['schematic_hash'], checks=b['checks']))
                except DomainError as error:
                    stages.append(dict(check_id='FINAL_SCHEMATIC_INTEGRITY', status='blocked', error=error.payload))
                    report['status'] = 'blocked'
            if pcb_id:
                try:
                    _, b, source = self.pcb_target(PCBRef(project_id=initial['id'], pcb_build_id=pcb_id))
                    for check in b['checks'].values():
                        for name, sha in check.get('files', {}).items():
                            require((source / name).is_file() and backend.digest((source / name).read_bytes()) == sha,
                                    'ARTIFACT_CHANGED', '审计中的 PCB 报告或预览已经变化。')
                    integrity.append(dict(artifact='pcb', source_hashes=b['sources'], checks=b['checks']))
                except DomainError as error:
                    stages.append(dict(check_id='FINAL_PCB_INTEGRITY', status='blocked', error=error.payload))
                    report['status'] = 'blocked'
            report['artifact_evidence'] = integrity
            folder = self.project_path(initial['id']) / 'audits' / report['audit_id']
            folder.mkdir(parents=True)
            write_json(folder / 'report.json', report)
            lines = ['# PCB 检查报告', '', '项目：' + initial['name'],
                     '规格版本：' + str(revision), '生成时间：' + report['created_at'],
                     '结果：' + ('可以进入独立人工审阅' if report['status'] == 'ready_for_human_review' else '仍有未通过或缺失的检查'),
                     '', '原理图：' + (sch_id or '未生成'), 'PCB：' + (pcb_id or '未生成'), '',
                     '| 检查 | 结果 |', '| --- | --- |']
            names = dict(BOM_IDENTITY='实物器件身份覆盖',ACTUAL_COPPER='电源走线与参考载流/压降',USB_HARDWARE_INTERLOCK='USB 稳态硬件互锁',USB_SINK_POWER='USB-C 电流与浪涌条件',MODULE_PORT_CONTRACT='传感器端口与电压范围',SPECIFICATION='规格与引脚', ELECTRICAL='ESP32 专用标称规则',
                         ENGINEERING='元件选型与电气边界', POWER_INTEGRITY='供电能力',
                         ACTUAL_NETLIST='原理图实际连接', ACTUAL_ERC='原理图电气检查',
                         ACTUAL_PCB_NETS='电路板实际连接', ACTUAL_DRC='电路板设计规则',
                         ACTUAL_RF_AND_DECOUPLING='天线禁布与电容位置',
                         EXACT_PCB_PREVIEW='电路板预览', CURRENT_SCHEMATIC='当前原理图',
                         CURRENT_PCB='当前电路板', FINAL_SCHEMATIC_INTEGRITY='原理图文件完整性',
                         FINAL_PCB_INTEGRITY='电路板文件完整性')
            labels = dict(pass_='通过', fail='未通过', incomplete='资料不完整',
                          not_applicable='本电路不适用', blocked='需处理')
            labels['pass'] = labels.pop('pass_')
            lines += ['| ' + names.get(s['check_id'], s['check_id']) + ' | ' +
                      labels.get(s['status'], s['status']) + ' |' for s in stages]
            if electrical_stage['status'] == 'not_applicable':
                lines += ['', 'ESP32 专用规则不适用于本 LED 电路；LED 的电流和功耗边界由单独的元件工程检查覆盖。']
            lines += ['', '未完成事项：', '']
            if report['status'] == 'ready_for_human_review':
                lines += ['已支持的自动检查没有发现阻碍。下面的人工审阅事项仍需完成。']
            for stage in stages:
                if stage['status'] in ('pass', 'not_applicable'):
                    continue
                lines.append('- ' + names.get(stage['check_id'], stage['check_id']) + '：' +
                             stage.get('error', {}).get('message', labels.get(stage['status'], stage['status'])))
                for item in stage.get('result', {}).get('findings', []):
                    if item['severity'] == 'error':
                        lines.append('  - ' + item['message'])
            lines += ['', '仍需人工审阅的事项：', '']
            warnings = {(item['code'], item['message']) for stage in stages
                        for item in stage.get('result', {}).get('findings', [])
                        if item['severity'] == 'warning'}
            lines += ['- ' + message for code, message in sorted(warnings)]
            lines += ['', '本报告重新调用实际网表、ERC、PCB 和 DRC 检查（有对应构建时）。',
                      '未执行布局布线、人工签字、Gerber 导出或下单。规格修改后须重新检查。',
                      '零 ERC/DRC 不等于实际功能、热性能、射频性能或量产资格通过。', '']
            (folder / '检查报告.md').write_text('\n'.join(lines))
        return dict(**report, json_path=str(folder / 'report.json'),
                    report_path=str(folder / '检查报告.md'),
                    sha256=backend.digest((folder / 'report.json').read_bytes()))
