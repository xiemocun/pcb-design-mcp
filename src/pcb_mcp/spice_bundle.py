"""Closed model dependencies and lexical isolation without changing source data.

Vendor helper names, parameters, functions and primitive models stay within an
instance-local enclosing subcircuit. This avoids global name collisions without
renaming tokens inside behavioral expressions or modifying immutable assets.
"""
import re

from . import spice


def logical_cards(lines):
    result=[]
    for line in lines:
        if line.startswith('+'):
            if not result:raise ValueError('SPICE_BUNDLE_ORPHAN_CONTINUATION')
            result[-1]+=' '+line[1:].strip()
        else:result.append(line)
    return result


_VOLTAGE = r'V\([a-z_0-9]+\)'
_THRESHOLD = r'(?:\{[a-z_][a-z_0-9]*\}|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))'
_COMPARISON = rf'{_VOLTAGE}\s*>\s*{_THRESHOLD}'
_PSPICE_COMPARISON_XOR = re.compile(
    rf'\bIF\(\s*({_COMPARISON})\s*\^\s*({_COMPARISON})\s*,', re.I
)


def source_cards(raw, model):
    dialect = model.get('spice_dialect', 'native')
    cards = logical_cards(spice.model_lines(spice.model_text(raw), dialect))
    if model.get('source_spice_dialect') is None:
        if dialect == 'ltspice' and any(_PSPICE_COMPARISON_XOR.search(card) for card in cards):
            raise ValueError('PSPICE_COMPARISON_XOR_SOURCE_DIALECT_REQUIRED')
        return cards, 0
    if model['source_spice_dialect'] != 'pspice' or dialect != 'ltspice':
        raise ValueError('SPICE_SOURCE_DIALECT_ADAPTER_UNAVAILABLE')
    converted = []
    count = 0
    for card in cards:
        if '^' in card and card[0].lower() in ('b', 'e', 'g'):
            card, occurrences = _PSPICE_COMPARISON_XOR.subn(r'IF(XOR((\1),(\2)),', card)
            count += occurrences
        if '^' in card:
            raise ValueError('PSPICE_XOR_REQUIRES_VERIFIED_ADAPTER')
        converted.append(card)
    return converted, count


def read(model,folder,*,adaptation=None):
    cards=[];count=0
    for path in [model['model_asset'],*model.get('dependency_assets',[])]:
        asset_cards, occurrences = source_cards((folder/path).read_bytes(), model)
        cards.extend(asset_cards)
        count += occurrences
    if adaptation is not None:adaptation['comparison_xor_occurrences']=count
    return cards


def isolate(lines,entry,index):
    cards=logical_cards(lines);declarations={}
    for line in cards:
        cells=line.split()
        if cells[0].lower()=='.subckt':
            key=cells[1].lower()
            if key in declarations:raise ValueError('SPICE_BUNDLE_DUPLICATE_SUBCIRCUIT: '+cells[1])
            end=next((i for i,c in enumerate(cells) if c.lower()=='params:' or '=' in c),len(cells))
            declarations[key]=cells[2:end]
    if entry.lower() not in declarations:raise ValueError('SPICE_BUNDLE_ENTRY_MISSING')
    for line in cards:
        cells=line.split()
        if cells[0].lower().startswith('x'):
            end=next((i for i,c in enumerate(cells) if c.lower()=='params:' or '=' in c),len(cells))
            if end<2 or cells[end-1].lower() not in declarations:raise ValueError('SPICE_BUNDLE_DEPENDENCY_MISSING: '+cells[end-1])
            if end-2!=len(declarations[cells[end-1].lower()]):raise ValueError('SPICE_BUNDLE_DEPENDENCY_PIN_COUNT: '+cells[end-1])
    wrapper='mcp_scope_'+str(index);ports=['port'+str(i) for i in range(len(declarations[entry.lower()]))]
    if wrapper.lower() in declarations:raise ValueError('SPICE_BUNDLE_RESERVED_SCOPE')
    return [f'.subckt {wrapper} '+' '.join(ports),'Xentry '+' '.join(ports)+' '+entry,*cards,f'.ends {wrapper}'],wrapper
