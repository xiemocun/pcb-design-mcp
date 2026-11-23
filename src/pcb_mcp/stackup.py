"""Declared 2/4-layer geometry, serialized and checked against native KiCad.

This module also runs in KiCad's Python 3.9 worker. Stack dimensions are design
inputs, not a board-house acceptance or an impedance qualification.
"""
import json
import math
import re

try:
    from .sexpr import parse, children, one
except ImportError:
    from sexpr import parse, children, one


def copper_layers(constraints):
    count = constraints.get('layers', 2)
    if count == 2:
        return ['F.Cu', 'B.Cu']
    if count == 4:
        return ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu']
    raise ValueError('UNSUPPORTED_COPPER_LAYER_COUNT')


def fabrication_layers(constraints):
    return copper_layers(constraints) + [
        'F.Paste', 'B.Paste', 'F.Silkscreen', 'B.Silkscreen',
        'F.Mask', 'B.Mask', 'Edge.Cuts']


def inspect_gerbers(folder, constraints):
    """Verify X2 layer identities, so equal file counts cannot hide an omission."""
    copper = copper_layers(constraints)
    expected = {'Copper,L%d,%s' % (i+1, 'Top' if i==0 else 'Bot' if i==len(copper)-1 else 'Inr')
                for i in range(len(copper))}
    expected.update(kind+','+side for kind in ('Paste','Legend','Soldermask') for side in ('Top','Bot'))
    expected.add('Profile,NP')
    observed = {}
    for path in sorted(folder.glob('*.gbr')):
        text = path.read_text()
        functions = re.findall(r'%TF\.FileFunction,([^*]+)\*%', text)
        if len(functions)!=1 or functions[0] in observed:
            raise ValueError('GERBER_LAYER_IDENTITY_MISSING_OR_DUPLICATED')
        if '%MOMM*%' not in text or not text.rstrip().endswith('M02*'):
            raise ValueError('GERBER_UNITS_OR_TERMINATOR_INVALID')
        observed[functions[0]] = path.name
    if set(observed)!=expected:
        raise ValueError('GERBER_LAYER_SET_MISMATCH')
    return dict(passed=True, layer_count=len(observed), functions=observed,
                scope='X2 layer identity, millimetre units and termination, not CAM or electrical qualification')


def validate(constraints):
    names = copper_layers(constraints)
    stack = constraints.get('stackup')
    if len(names) == 4 and not stack:
        raise ValueError('FOUR_LAYER_STACKUP_REQUIRED')
    if not stack:
        return
    if [x['name'] for x in stack['copper']] != names:
        raise ValueError('STACKUP_COPPER_ORDER_MISMATCH')
    if len(stack['dielectrics']) != len(names) - 1:
        raise ValueError('STACKUP_DIELECTRIC_COUNT_MISMATCH')
    total = sum(x['thickness_um'] / 1000 for x in stack['copper'])
    total += sum(x['thickness_mm'] for x in stack['dielectrics'])
    if not math.isclose(total, constraints.get('thickness_mm', 1.6), abs_tol=1e-6):
        raise ValueError('STACKUP_TOTAL_THICKNESS_MISMATCH')


def serialize(stack):
    rows = ['(stackup']
    for index, copper in enumerate(stack['copper']):
        rows.append('(layer ' + json.dumps(copper['name']) +
                    ' (type "copper") (thickness %.12g))' % (copper['thickness_um'] / 1000))
        if index < len(stack['dielectrics']):
            dielectric = stack['dielectrics'][index]
            rows.append('(layer "dielectric %d" (type %s) (thickness %.12g) '
                        '(material %s) (epsilon_r %.12g) (loss_tangent %.12g))' %
                        (index + 1, json.dumps(dielectric['kind']), dielectric['thickness_mm'],
                         json.dumps(dielectric['material']), dielectric['epsilon_r'],
                         dielectric['loss_tangent']))
    rows.append('(dielectric_constraints yes))')
    return '\n'.join(rows)


def apply(path, constraints):
    """Populate the documented native stackup field, then let KiCad reload it.

    The SWIG GetStackupDescriptor result is an opaque pointer in KiCad 10.
    Only this schema-derived section of a generated board is changed.
    """
    validate(constraints)
    if not constraints.get('stackup'):
        return
    text = path.read_text()
    setup = children(parse(text), 'setup')[0]
    existing = children(setup, 'stackup')
    if len(existing) > 1:
        raise ValueError('STACKUP_DUPLICATED')
    fragment = serialize(constraints['stackup'])
    if existing:
        start, end = existing[0]['start'], existing[0]['end']
    else:
        start = end = setup['start'] + len('(setup')
        fragment = '\n' + fragment
    path.write_text(text[:start] + fragment + text[end:])


def inspect(path, constraints):
    """Read the saved native descriptor independently of in-memory requests."""
    validate(constraints)
    tree = parse(path.read_text())
    layers = children(tree, 'layers')[0]
    actual = [row['v'][1] for row in layers['v'] if isinstance(row, dict)
              and len(row['v']) >= 2 and str(row['v'][1]).endswith('.Cu')]
    errors = []
    if actual != copper_layers(constraints):
        errors.append('PCB_COPPER_LAYERS_MISMATCH')
    thickness = float(one(children(tree, 'general')[0], 'thickness')[0])
    if not math.isclose(thickness, constraints.get('thickness_mm', 1.6), abs_tol=1e-6):
        errors.append('PCB_THICKNESS_MISMATCH')
    stack = constraints.get('stackup')
    observed = []
    if stack:
        native = children(children(tree, 'setup')[0], 'stackup')
        expected = []
        for i, copper in enumerate(stack['copper']):
            expected.append(dict(name=copper['name'], kind='copper',
                                 thickness_mm=copper['thickness_um'] / 1000))
            if i < len(stack['dielectrics']):
                expected.append(dict(name='dielectric ' + str(i + 1), **stack['dielectrics'][i]))
        if len(native) == 1:
            for row in children(native[0], 'layer'):
                name = row['v'][1]
                if name not in copper_layers(constraints) and not name.startswith('dielectric '):
                    continue
                value = dict(name=name, kind=one(row, 'type')[0],
                             thickness_mm=float(one(row, 'thickness')[0]))
                if name.startswith('dielectric '):
                    value.update(material=one(row, 'material')[0],
                                 epsilon_r=float(one(row, 'epsilon_r')[0]),
                                 loss_tangent=float(one(row, 'loss_tangent')[0]))
                observed.append(value)
        if len(observed) != len(expected) or any(
                set(a) != set(e) or any(
                    not math.isclose(a[key], e[key], rel_tol=1e-8, abs_tol=1e-9)
                    if isinstance(e[key], (int, float)) else a[key] != e[key]
                    for key in e) for a, e in zip(observed, expected)):
            errors.append('PCB_NATIVE_STACKUP_MISMATCH')
    return dict(passed=not errors, copper_layers=actual, thickness_mm=thickness,
                native_stackup=observed, errors=errors,
                qualification='declared_geometry_not_impedance_or_manufacturer_acceptance')
