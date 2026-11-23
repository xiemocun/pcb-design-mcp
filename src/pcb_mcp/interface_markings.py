"""Place and inspect silk text tied to native physical pads and their nets."""

try:
    from .native_fabrication import units
except ImportError:
    from native_fabrication import units


TEXT = {'pin_1': '1', 'positive': '+', 'return': '-'}
ROLES = {'1': 'pin_1', 'PIN 1': 'pin_1', '+': 'positive', '-': 'return', 'GND': 'return', '0V': 'return'}
MAX_DISTANCE_MM = 4.0


def requirements(state, constraints):
    seen = set()
    result = []
    for row in constraints.get('interface_markings', []):
        component = state['components'].get(row['component_id'])
        role, number = row['role'], row['number']
        pin = next((p for p in component['pins'] if number in p.get('physical_numbers', [p['number']])), None) if component else None
        if pin is None or role == 'pin_1' and number != '1':
            raise ValueError('INTERFACE_MARK_PHYSICAL_PAD_INVALID')
        net = state['nets'].get(row.get('net_id'))
        if (row.get('net_id') is not None and
                (net is None or dict(component_id=component['id'], pin_id=pin['pin_id']) not in net['pins'])):
            raise ValueError('INTERFACE_MARK_PAD_NET_MISMATCH:' + component['ref'] + ':' + number)
        if role != 'pin_1' and (net is None or net['kind'] != {'positive': 'power', 'return': 'ground'}[role]):
            raise ValueError('INTERFACE_MARK_POWER_ROLE_MISMATCH:' + component['ref'] + ':' + number)
        key = (component['ref'], number, role)
        if key in seen:
            raise ValueError('INTERFACE_MARK_DUPLICATE')
        seen.add(key)
        result.append(dict(ref=component['ref'], number=number, role=role, net=net['name'] if net else None))
    return result


def bounds(box, inflate=0):
    return box.GetX() - inflate, box.GetY() - inflate, box.GetRight() + inflate, box.GetBottom() + inflate


def overlaps(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def environment(board, k):
    footprints = {fp.GetReference(): fp for fp in board.GetFootprints()}
    pads = {side: [] for side in (k.F_SilkS, k.B_SilkS)}
    bodies = {side: [] for side in pads}
    texts = list(board.GetDrawings())
    for ref, fp in footprints.items():
        back = fp.IsFlipped()
        silk, fab = (k.B_SilkS, k.B_Fab) if back else (k.F_SilkS, k.F_Fab)
        graphics = list(fp.GraphicalItems())
        texts.extend(graphics)
        pads[silk].extend((ref, p) for p in fp.Pads() if p.GetNumber())
        shape_boxes = [bounds(item.GetBoundingBox()) for item in graphics
                       if isinstance(item, k.PCB_SHAPE) and item.GetLayer() == fab]
        if shape_boxes:
            bodies[silk].append((min(b[0] for b in shape_boxes), min(b[1] for b in shape_boxes),
                                 max(b[2] for b in shape_boxes), max(b[3] for b in shape_boxes)))
        else:
            bodies[silk].append(bounds(fp.GetBoundingBox(False, False)))
    return footprints, pads, bodies, texts


def nearest_pad(position, pads):
    nearest = None
    distance = None
    for ref, pad in pads:
        center = pad.GetPosition()
        d = (center.x - position.x) ** 2 + (center.y - position.y) ** 2
        key = (ref, pad.GetNumber())
        if distance is None or d < distance:
            nearest, distance = key, d
        elif d == distance and key != nearest:
            nearest = None
    return nearest, distance


def pad_for(row, footprints, k):
    fp = footprints.get(row['ref'])
    matches = [p for p in fp.Pads() if p.GetNumber() == row['number']] if fp else []
    if len({(p.GetPosition().x, p.GetPosition().y, p.GetNetname()) for p in matches}) != 1:
        return None, None
    pad = matches[0]
    if row.get('net') is not None and pad.GetNetname() != row['net']:
        return None, None
    return pad, k.B_SilkS if fp.IsFlipped() else k.F_SilkS


def author(board, k, rows, width_mm, height_mm):
    if not rows:
        return []
    footprints, pads, bodies, items = environment(board, k)
    masks = {side: [] for side in pads}
    for fp in footprints.values():
        for pad in fp.Pads():
            for side, mask in ((k.F_SilkS, k.F_Mask), (k.B_SilkS, k.B_Mask)):
                if pad.IsOnLayer(mask):
                    masks[side].append(bounds(pad.GetBoundingBox(), units(.18)))
    for item in items:
        if item.GetLayer() in masks:
            masks[item.GetLayer()].append(bounds(item.GetBoundingBox(), units(.18)))
    radius = units(MAX_DISTANCE_MM)
    offsets = sorted(((x*x+y*y, x, y) for x in range(-8, 9) for y in range(-8, 9) if x*x+y*y <= 64))
    created = []
    for row in rows:
        pad, layer = pad_for(row, footprints, k)
        if pad is None:
            raise ValueError('INTERFACE_MARK_PAD_NET_MISMATCH:' + row['ref'] + ':' + row['number'])
        item = k.PCB_TEXT(board)
        item.SetText(TEXT[row['role']])
        item.SetLayer(layer)
        item.SetMirrored(layer == k.B_SilkS)
        item.SetTextSize(k.VECTOR2I(units(1), units(1)))
        item.SetTextThickness(units(.15))
        center = pad.GetPosition()
        for _, dx, dy in offsets:
            item.SetPosition(k.VECTOR2I(center.x + units(dx/2), center.y + units(dy/2)))
            box = item.GetBoundingBox()
            area = bounds(box)
            if (area[0] < units(.3) or area[1] < units(.3)
                    or area[2] > units(width_mm-.3) or area[3] > units(height_mm-.3)):
                continue
            if any(overlaps(area, blocked) for blocked in masks[layer] + bodies[layer]):
                continue
            terminal, distance = nearest_pad(box.GetCenter(), pads[layer])
            if terminal == (row['ref'], row['number']) and distance <= radius * radius:
                board.Add(item)
                masks[layer].append(bounds(box, units(.18)))
                created.append(item.m_Uuid.AsString())
                break
        else:
            raise ValueError('INTERFACE_MARK_SILKSCREEN_SPACE_UNAVAILABLE:' + row['ref'] + ':' + row['number'] + ':' + row['role'])
    return created


def inspect(board, k, rows):
    footprints, pads, bodies, items = environment(board, k)
    findings = []
    marks = {}
    radius = units(MAX_DISTANCE_MM)
    for item in items:
        if not isinstance(item, k.PCB_TEXT) or not item.IsVisible():
            continue
        layer = item.GetLayer()
        role = ROLES.get(item.GetShownText(False).strip().upper())
        if layer not in pads or not role or item.IsMirrored() != (layer == k.B_SilkS):
            continue
        box = item.GetBoundingBox()
        terminal, distance = nearest_pad(box.GetCenter(), pads[layer])
        if terminal is None or distance > radius * radius or any(overlaps(bounds(box), body) for body in bodies[layer]):
            continue
        marks.setdefault((*terminal, role), []).append(dict(
            uuid=item.m_Uuid.AsString(), text=item.GetShownText(False), layer=board.GetLayerName(layer),
            center_mm=[k.ToMM(box.GetCenter().x), k.ToMM(box.GetCenter().y)],
            distance_to_pad_mm=k.ToMM(distance ** .5)))

    measurements = []
    for row in rows:
        ref, number, role = row['ref'], row['number'], row['role']
        pad, _ = pad_for(row, footprints, k)
        matching = marks.get((ref, number, role), [])
        if pad is None or role == 'pin_1' and number != '1':
            findings.append('PCB_INTERFACE_MARK_PAD_NET_MISMATCH:' + ref + ':' + number)
        elif not matching:
            findings.append('PCB_INTERFACE_MARK_MISSING_OR_MISPLACED:' + ref + ':' + number + ':' + role)
        opposite = {'positive': 'return', 'return': 'positive'}.get(role)
        if opposite and (ref, number, opposite) in marks:
            findings.append('PCB_INTERFACE_MARK_CONTRADICTORY_POLARITY:' + ref + ':' + number)
        if role == 'pin_1' and any(r == ref and n != number and label == role for r, n, label in marks):
            findings.append('PCB_INTERFACE_MARK_PIN_ONE_AT_WRONG_PAD:' + ref)
        measurements.append(dict(row, actual_net=pad.GetNetname() if pad is not None else None, marks=matching))
    return dict(passed=not findings, failures=findings, maximum_pad_distance_mm=MAX_DISTANCE_MM, measurements=measurements)
