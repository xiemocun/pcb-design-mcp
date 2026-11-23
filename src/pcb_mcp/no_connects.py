"""Preserve KiCad's isolated NC pad-net identities without electrical edges.

Pure Python 3.9 compatible for native workers. Names come only from a fresh
schematic export, never from guesses about pin labels or manufacturer numbers.
"""
import re
import xml.etree.ElementTree as ET
from .sexpr import children, one, parse


def numbers(value):return re.findall(r'[A-Za-z0-9]+',value) if value.startswith('[') else [value]


def read(state,path):
    return checked_nets(state, (
        (net.attrib['name'], [(node.attrib['ref'],node.attrib['pin']) for node in net.findall('node')])
        for net in ET.parse(path).findall('./nets/net')
    ))


def read_board_names(state, path):
    """The board-facing KiCad exporter preserves escapes that XML strips."""
    root = parse(path.read_text())
    sections = children(root, 'nets')
    if len(sections) != 1:
        raise ValueError('NATIVE_NC_NETLIST_INVALID')
    return checked_nets(state, (
        (one(net, 'name')[0], [(one(node, 'ref')[0], one(node, 'pin')[0]) for node in children(net, 'node')])
        for net in children(sections[0], 'net')
    ))


def checked_nets(state, nets):
    expected={}
    for intent in state.get('pin_intents',{}).values():
        if intent['intent']!='no_connect':continue
        pin=intent['pin'];component=state['components'][pin['component_id']]
        logical=next(p for p in component['pins'] if p['pin_id']==pin['pin_id'])
        for number in logical.get('physical_numbers',numbers(logical['number'])):expected[component['ref'],number]=(component['id'],logical['pin_id'])
    result=[];seen=set();names=set()
    for name, nodes in nets:
        if not name.lstrip('/').startswith('unconnected-'):continue
        pins=[(ref,number) for ref,pin in nodes if not ref.startswith('#') for number in numbers(pin)]
        if not pins:continue
        if any(pin not in expected for pin in pins):raise ValueError('NATIVE_NC_PIN_NOT_DECLARED')
        if len({expected[pin] for pin in pins})!=1:raise ValueError('NATIVE_NC_NET_JOINS_INDEPENDENT_PINS')
        if name in names or any(pin in seen for pin in pins) or len(set(pins))!=len(pins):raise ValueError('NATIVE_NC_NET_DUPLICATED')
        names.add(name);seen.update(pins);result.append(dict(name=name,pins=[dict(ref=ref,number=number) for ref,number in pins]))
    if seen!=set(expected):raise ValueError('NATIVE_NC_NET_MISSING')
    return result


def pads(nets):return {(pin['ref'],pin['number']):net['name'] for net in nets for pin in net['pins']}
