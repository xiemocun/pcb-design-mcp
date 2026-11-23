"""Actual pin connectivity, integrated from our tested read-only add-on."""
from typing import Annotated,Literal
from pydantic import Field
from . import backend
from .common import require
from .models import BuildRef,Pin,Strict,Revision

class TraceRequest(BuildRef):
    pin: Pin
    offset: Annotated[int, Field(ge=0, le=10000)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 30


class TraceResult(Strict):
    project_id: str
    revision: Revision
    build_id: str
    query: dict
    connection: Literal['connected', 'verified_no_connect']
    net: dict | None
    members: list[dict]
    total_members: int
    next_offset: int | None
    evidence: dict
    qualification: Literal['actual_shared_net_only_not_component_conduction_or_functional_approval']


class ConnectivityService:
    def pin_connections(self,request):
        # Avoid nested file locks: verify takes/releases the core project's lock.
        with self.locked():
            state, build, folder = self.target(request)
            require(request.pin.component_id in state['components'], 'UNKNOWN_COMPONENT', '元件不存在。')
            component = state['components'][request.pin.component_id]
            pin = next((p for p in component['pins'] if p['pin_id'] == request.pin.pin_id), None)
            require(pin is not None, 'UNKNOWN_PIN', '引脚不属于该元件。')
        verified = self.verify(request)
        require(verified['verdict'] == 'pass', 'NETLIST_VALIDATION_REQUIRED', '实际原理图连接核对未通过，不能返回可信连接。', report=verified)
        with self.locked():
            state, build, folder = self.target(request)
            require(build['schematic_hash'] == verified['schematic_hash'] and state['revision'] == verified['revision'],
                    'STALE_VALIDATION', '查询期间构建变化。')
            check = build['checks']['verify']
            for name, sha in check['files'].items():
                require((folder / name).is_file() and backend.digest((folder / name).read_bytes()) == sha,
                        'ARTIFACT_CHANGED', '连接核对文件已经改变。')
            physical = pin.get('physical_numbers', backend.expanded_number(pin['number']))
            nets = backend.xml_nets(folder / 'actual.xml')
            names = {n for n, members in nets.items() if any((component['ref'], number) in members for number in physical)}
            intent = state['pin_intents'].get(component['id'] + ':' + pin['pin_id'], {})
            query = dict(component_id=component['id'], reference=component['ref'], pin_id=pin['pin_id'],
                         pin_name=pin['name'], physical_numbers=physical)
            require(len(names) <= 1, 'PIN_NET_CONFLICT', '一个逻辑引脚的物理焊盘不能属于不同网络。')
            result_net = None
            rows = []
            if names:
                name = names.pop()
                declared = [n for n in state['nets'].values() if n['name'] == name]
                require(len(declared) == 1, 'NETLIST_VALIDATION_REQUIRED', '实际网络不能唯一对应规格网络。')
                lookup = {(c['ref'], number): (c, p) for c in state['components'].values()
                          for p in c['pins'] for number in p.get('physical_numbers', backend.expanded_number(p['number']))}
                require(all((component['ref'], n) in nets[name] for n in physical),
                        'PIN_NET_CONFLICT', '分组物理引脚的实际连接不一致。')
                for reference, number in nets[name]:
                    require((reference, number) in lookup, 'NETLIST_VALIDATION_REQUIRED', '实际网络含有未声明的元件引脚。')
                    c, p = lookup[(reference, number)]
                    part = state['part_bindings'].get(c['id'])
                    rows.append(dict(reference=reference, physical_number=number, component_id=c['id'],
                                     pin_id=p['pin_id'], pin_name=p['name'], symbol_id=c['library_id'],
                                     value=c['value'], bound_mpn=part['mpn'] if part else None,
                                     mpn_qualification='record_only_not_requalified_here' if part else 'unbound'))
                result_net = dict(net_id=declared[0]['id'], name=name, kind=declared[0]['kind'])
                connection = 'connected'
            else:
                require(intent.get('intent') == 'no_connect' and verified['no_connect_match'],
                        'PIN_INTENT_UNSPECIFIED', '该引脚既没有连接，也没有核实过的悬空声明。')
                query['no_connect_reason'] = intent['reason']
                connection = 'verified_no_connect'
            end = request.offset + request.limit
            return dict(project_id=state['id'], revision=state['revision'], build_id=build['id'],
                        query=query, connection=connection, net=result_net, members=rows[request.offset:end],
                        total_members=len(rows), next_offset=end if end < len(rows) else None,
                        evidence=dict(engine='KiCad XML netlist', verified=True,
                                      schematic_sha256=build['schematic_hash'], netlist_sha256=check['files']['actual.xml'],
                                      verification_sha256=check['files']['verify.json']),
                        qualification='actual_shared_net_only_not_component_conduction_or_functional_approval')
