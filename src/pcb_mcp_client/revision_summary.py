"""Describe differences between verified revision deliveries in Markdown."""

from html import escape
import json
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from pcb_mcp.registry import sha


SUMMARY_NAME = 'revision-summary.md'


def delivered_board(export, placements):
    manifest = export['manifest']
    with ZipFile(export['bundle_path']) as bundle:
        contents = {name: bundle.read(name) for name in (
            'bom.json', 'portable/netlist.xml', 'verify_routed.json')}
        for name, data in contents.items():
            if sha(data) != manifest['hashes'][name]:
                raise ValueError('REVISION_SUMMARY_DELIVERY_CHANGED: ' + name)

    details = json.loads(contents['verify_routed.json'])['details']
    fabrication = details['fabrication']
    bounds = fabrication.get('rectangle_bounds_mm')
    stackup = details['stackup']
    geometry = {'板厚': f"{stackup['thickness_mm']:g} mm",
                '铜层': '、'.join(stackup['copper_layers'])}
    for layer in stackup['native_stackup']:
        geometry[f"叠层 {layer['name']}"] = ' · '.join(
            f'{label} {layer[key]}' for key, label in (
                ('kind', '类型'), ('material', '材料'), ('thickness_mm', '厚度 mm'),
                ('epsilon_r', '介电常数'), ('loss_tangent', '损耗角正切')) if layer.get(key) is not None)
    if bounds is not None:
        x1, y1, x2, y2 = bounds
        geometry['板框尺寸'] = f'{x2-x1:g} × {y2-y1:g} mm'
    holes = details.get('mounting', {}).get('holes', [])
    parts = {row['reference']: ' · '.join(str(row[key]) for key in (
        'value', 'manufacturer', 'mpn', 'footprint') if row.get(key)) for row in json.loads(contents['bom.json'])}
    positions = {ref: f"({pose['x_mm']:g}, {pose['y_mm']:g}) mm，旋转 {pose['rotation_deg']:g}°"
                 for ref, pose in placements.items()}
    nets = {net.attrib['name']: '、'.join(sorted(
        f"{node.attrib['ref']}.{node.attrib['pin']}" for node in net.findall('node')))
        for net in ET.fromstring(contents['portable/netlist.xml']).findall('./nets/net')}
    return geometry, parts, positions, nets, holes


def mounting_values(previous, current, change):
    def label(hole):
        return '安装孔 ' + hole['ref']

    def dimensions(hole):
        return (f"({hole['x_mm']:g}, {hole['y_mm']:g}) mm，孔径 {hole['diameter_mm']:g} mm，"
                f"孔壁净空 {hole['keepout_mm']:g} mm")

    pairs = (change.hole_mapping(required=False) if change is not None else
             {index: index for index in range(min(len(previous), len(current)))})
    before = {label(hole): dimensions(hole) for hole in previous}
    if pairs is None:
        return ({'原版' + name: value for name, value in before.items()},
                {'新版' + label(hole): dimensions(hole) for hole in current})
    after = {label(previous[old]): dimensions(current[new]) for old, new in pairs.items()}
    retained = set(pairs.values())
    for index, hole in enumerate(current):
        if index not in retained:
            name = label(hole)
            after['新增' + name if name in before else name] = dimensions(hole)
    return before, after


def differences(before, after):
    for key in sorted(before.keys() | after.keys()):
        if before.get(key) != after.get(key):
            yield key, before.get(key, '—'), after.get(key, '—')


def one_line(value):
    return re.sub(r'([\\`*_\[\]#!])', r'\\\1', escape(' '.join(str(value).split()), quote=False))


def write_summary(out, base_run, base, current_export, current_placements,
                  request, base_contract, contract, model_changes, base_criteria, acceptance,
                  *, project_id, retained_pcb, mounting_change=None):
    previous_checks = json.loads((base_run / 'checks.json').read_bytes())
    previous = delivered_board(previous_checks['pcb_review_export'], base['placements'])
    current = delivered_board(current_export, current_placements)
    before_holes, after_holes = mounting_values(previous[4], current[4], mounting_change)
    previous[0].update(before_holes)
    current[0].update(after_holes)
    sections = zip(('板框与安装孔', '器件与封装', '器件位置', '原理图网络'), previous, current)
    lines = ['# 改版说明', '', '## 改版请求', '', one_line(request), '',
             f"基准工程 `{base['project_id'][:12]}` · 版本 {base['revision']}", '',
             f"新版工程 `{project_id[:12]}` · 版本 {current_export['manifest']['revision']}"]
    for heading, before, after in sections:
        changes = [f'- {one_line(key)} · {one_line(old)} → {one_line(new)}'
                   for key, old, new in differences(before, after)]
        if changes:
            lines.extend(['', f'## {heading}', '', *changes])
    functional = []
    if base_contract['description'] != contract['description']:
        functional.append(f"- 契约说明 · {one_line(base_contract['description'])} → {one_line(contract['description'])}")
    inherited_checks = 0
    for key, label in (('requirements', '功能限值'), ('i2c_pullups', 'I²C 上拉检查'), ('scenarios', '验证工况')):
        old = {row['id']: row for row in base_contract.get(key, [])}
        new = {row['id']: row for row in contract.get(key, [])}
        if key != 'scenarios':
            inherited_checks += sum(row == new.get(id_) for id_, row in old.items())
        for id_, before, after in differences(old, new):
            if (key == 'requirements' and isinstance(before, dict) and isinstance(after, dict)
                    and (before['minimum'], before['maximum']) != (after['minimum'], after['maximum'])):
                functional.append(f"- {one_line(id_)} · {before['minimum']} 至 {before['maximum']} → "
                                  f"{after['minimum']} 至 {after['maximum']}（{after['quantity']}）")
            elif (key == 'i2c_pullups' and isinstance(before, dict) and isinstance(after, dict)
                    and all(before[field] == after[field] for field in ('sda', 'scl', 'supply'))):
                for line, field in (('SDA', 'sda_ohm'), ('SCL', 'scl_ohm')):
                    if before[field] != after[field]:
                        functional.append(f"- {one_line(id_)} · {line} 上拉 {before[field]:g} Ω → {after[field]:g} Ω")
            else:
                action = '新增' if before == '—' else '移除' if after == '—' else '调整'
                functional.append(f'- {action}{label} {one_line(id_)}')
    functional.extend(f'- {one_line(ref)} 仿真模型 · {before} → {after}'
                      for ref, before, after in model_changes)
    fields = ('report', 'pointer', 'operator', 'expected')
    previous_criteria, current_criteria = ({row['id']: tuple(row.get(field) for field in fields) for row in criteria}
                                          for criteria in (base_criteria, acceptance['criteria']))
    for id_, before, after in differences(previous_criteria, current_criteria):
        if isinstance(before, tuple) and isinstance(after, tuple) and before[:3] == after[:3]:
            functional.append(f'- 交付检查 {one_line(id_)} · {one_line(before[3])} → {one_line(after[3])}')
        else:
            action = '新增' if before == '—' else '移除' if after == '—' else '调整'
            functional.append(f'- {action}交付检查 {one_line(id_)}')
    if functional:
        lines.extend(['', '## 验收条件与仿真模型', '', *functional])
    inherited_criteria = sum(row == current_criteria.get(id_) for id_, row in previous_criteria.items())
    lines.extend(['', '## 软件复验', '',
                  f"新版通过声明功能工况、原理图与 PCB 检查及 {len(acceptance['criteria'])} 项交付验收。",
                  f'其中 {inherited_checks} 项功能检查和 {inherited_criteria} 项交付条件沿用基准。', '',
                  '沿用基准 PCB 文件并完成复验。' if retained_pcb else '使用新版 PCB 文件完成复验。',
                  '', f"基准交付 SHA-256 `{base['bundle_sha256']}`",
                  '', f"新版 KiCad PCB SHA-256 `{current_export['manifest']['hashes']['portable/design.kicad_pcb']}`", ''])
    path = out / SUMMARY_NAME
    contents = '\n'.join(lines).encode()
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(contents)
    temporary.replace(path)
    return sha(contents)
