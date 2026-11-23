"""Quasi-static charge-demand propagation; not a converter startup simulation.

Propagate only incremental capacitor current. The DC budget already includes
downstream DC loads and converter overhead, so adding either again is wrong.
All converter inputs must remain inside the declared enabled voltage envelope.
"""
import math


def evaluate(rows, stages, source_net_id, budget_complete):
    result = dict(verdict='incomplete', rails=[], transfers=[], findings=[],
                  input_charge_reference_a=None, dc_plus_charge_input_reference_a=None,
                  full_startup_qualified=False,
                  operating_condition='enabled_quasi_static_voltage_envelopes_and_declared_efficiency; not_0V_startup',
                  overlap_assumption='all_declared_positive_slews_overlap',
                  input_overhead='already_in_DC_baseline; no_second_addition')

    def error(code, message):
        result['findings'].append(dict(code=code, severity='error', message=message))
        result['verdict'] = 'fail'

    def finite(value, positive=False):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and (value > 0 if positive else value >= 0))

    by_id = {r['net_id']: r for r in rows}
    names = {r['name']: r['net_id'] for r in rows}
    if len(by_id) != len(rows) or len(names) != len(rows):
        error('STARTUP_TRANSFER_RAIL_AMBIGUOUS', '电源网络身份或名称重复，无法唯一追踪充电通路。')
        return result
    if not budget_complete or source_net_id not in by_id or not rows:
        return result
    parent = {}; edges = []
    for stage in stages:
        vi, vo = names.get(stage['input_net']), names.get(stage['output_net'])
        if vi is None or vo is None or vi == vo or vo in parent:
            error('STARTUP_TRANSFER_TOPOLOGY', '供电级缺少唯一输入/输出、输入输出重合或存在多驱动。')
            return result
        kind = stage['kind']; factor = 1.0
        if kind == 'buck':
            iv = (by_id[vi].get('voltage_reference_v') or {}).get('min_v')
            ov = (by_id[vo].get('voltage_reference_v') or {}).get('max_v')
            eta = stage.get('efficiency_assumption')
            if not finite(iv, True) or not finite(ov, True) or not finite(eta, True) or eta > 1:
                error('STARTUP_TRANSFER_PARAMETERS', '转换级电压或效率声明无效，不能将充电需求折算为零。')
                return result
            factor = ov / (iv * eta)
        elif kind not in ('ldo', 'load_switch'):
            error('STARTUP_TRANSFER_KIND', '没有此供电级的电流传递模型。')
            return result
        parent[vo] = vi
        edges.append(dict(reference=stage['reference'], kind=kind, input_net_id=vi,
                          output_net_id=vo, current_transfer_factor=factor))
    # Require a single rooted tree; an isolated rail or cycle must not disappear.
    if set(by_id) - set(parent) != {source_net_id}:
        error('STARTUP_TRANSFER_TOPOLOGY', '电源网络未全部由唯一声明入口驱动。')
        return result
    for nid in by_id:
        seen = set(); node = nid
        while node in parent:
            if node in seen:
                error('STARTUP_TRANSFER_CYCLE', '电源路径成环，不能折算充电需求。')
                return result
            seen.add(node); node = parent[node]
        if node != source_net_id:
            error('STARTUP_TRANSFER_TOPOLOGY', '存在无法追踪到声明入口的电源网络。')
            return result
    if not all(finite(r.get('local_capacitor_current_reference_a'))
               and finite(r.get('declared_dc_baseline_a'))
               and finite((r.get('capacity_reference') or {}).get('current_a'), True)
               and r['capacitor_inventory_complete'] for r in rows):
        return result
    # Each capacitor rail keeps its own contribution through the entire path.
    contributions = {nid: {nid: r['local_capacitor_current_reference_a']} for nid, r in by_id.items()}
    pending = list(edges)
    while pending:
        for edge in list(pending):
            vi, vo = edge['input_net_id'], edge['output_net_id']
            if any(e['input_net_id'] == vo for e in pending):
                continue
            output = sum(contributions[vo].values())
            incoming = output * edge['current_transfer_factor']
            for origin, current in contributions[vo].items():
                contributions[vi][origin] = current * edge['current_transfer_factor']
            result['transfers'].append(dict(edge, output_charge_reference_a=output,
                                           input_charge_reference_a=incoming))
            pending.remove(edge)
    for nid, row in by_id.items():
        charge = sum(contributions[nid].values())
        total = row['declared_dc_baseline_a'] + charge
        capacity = row['capacity_reference']['current_a']; margin = capacity - total
        result['rails'].append(dict(net_id=nid, name=row['name'],
            local_charge_reference_a=row['local_capacitor_current_reference_a'],
            downstream_charge_reference_a=charge-row['local_capacitor_current_reference_a'],
            total_charge_reference_a=charge, dc_baseline_a=row['declared_dc_baseline_a'],
            dc_plus_charge_reference_a=total, capacity_reference_a=capacity,
            current_margin_reference_a=margin,
            contributions=[dict(origin_net_id=origin, origin_name=by_id[origin]['name'], current_reference_a=value)
                           for origin, value in sorted(contributions[nid].items())],
            screen='exceeds_declared_transfer_reference' if margin < 0 else 'within_declared_transfer_reference'))
        if margin < 0:
            error('STARTUP_TRANSFER_CURRENT_EXCEEDED', row['name']+' 加入下游充电折算后超过声明容量；此准静态场景不自洽。')
    result['input_charge_reference_a'] = sum(contributions[source_net_id].values())
    result['dc_plus_charge_input_reference_a'] = by_id[source_net_id]['declared_dc_baseline_a'] + result['input_charge_reference_a']
    if result['verdict'] != 'fail':
        result['verdict'] = 'conditional'
    return result
