"""Native nonplated tooling and all-layer copper clearance for a rectangle."""
import math


VALUE = 'Mechanical NPTH'


def validate(constraints):
    holes = constraints.get('mounting_holes', [])
    for index, hole in enumerate(holes):
        x, y = hole['x_mm'], hole['y_mm']
        radius = hole['diameter_mm'] / 2
        if not (radius < x < constraints['width_mm'] - radius and radius < y < constraints['height_mm'] - radius):
            raise ValueError('MOUNTING_HOLE_OUTSIDE_BOARD')
        web = constraints.get('hole_to_hole_mm', .25)
        if any(math.hypot(x - other['x_mm'], y - other['y_mm']) < radius + other['diameter_mm'] / 2 + web
               for other in holes[:index]):
            raise ValueError('MOUNTING_HOLE_WEB_TOO_NARROW')


def native_units(mm):
    return round(mm * 1_000_000)


def position(k, row):
    return k.VECTOR2I(native_units(row['x_mm']), native_units(row['y_mm']))


def attributes(k):
    return k.FP_BOARD_ONLY | k.FP_EXCLUDE_FROM_BOM | k.FP_EXCLUDE_FROM_POS_FILES


def mechanical_pad(fp, k):
    if fp.GetValue() != VALUE or fp.GetAttributes() & attributes(k) != attributes(k):
        return None
    pads = list(fp.Pads())
    if len(pads) != 1 or list(fp.Zones()):
        return None
    if any(graphic.IsOnCopperLayer() for graphic in fp.GraphicalItems()):
        return None
    pad = pads[0]
    drill = pad.GetDrillSize()
    if (pad.GetAttribute() != k.PAD_ATTRIB_NPTH or pad.GetNumber() or pad.GetNetCode()
            or pad.GetDrillShape() != k.PAD_DRILL_SHAPE_CIRCLE or drill.x <= 0 or drill.x != drill.y
            or pad.GetShape(k.F_Cu) != k.PAD_SHAPE_CIRCLE or pad.GetSize(k.F_Cu) != drill
            or pad.IsOnCopperLayer()):
        return None
    return pad


def keepout_polygon(k, zone, center, radius):
    # Circumscribe the requested circle, including a 2 nm rounding allowance.
    points = 64
    outer = radius / math.cos(math.pi / points) + 2
    polygon = zone.Outline()
    polygon.NewOutline()
    for index in range(points):
        angle = math.tau * index / points
        polygon.Append(center.x + round(outer * math.cos(angle)), center.y + round(outer * math.sin(angle)))
    return polygon


def add(board, k, constraints):
    holes = constraints.get('mounting_holes', [])
    if not holes:
        return
    validate(constraints)
    refs = {fp.GetReference() for fp in board.GetFootprints()}
    number = 0
    layers = k.LSET()
    for layer in board.GetEnabledLayers().CuStack():
        layers.AddLayer(layer)
    for row in holes:
        number += 1
        while 'MH' + str(number) in refs:
            number += 1
        ref = 'MH' + str(number)
        center = position(k, row)
        diameter = native_units(row['diameter_mm'])
        footprint = k.FOOTPRINT(board)
        footprint.SetReference(ref)
        footprint.SetValue(VALUE)
        footprint.SetAttributes(attributes(k))
        footprint.Reference().SetVisible(False)
        footprint.Value().SetVisible(False)
        pad = k.PAD(footprint)
        pad.SetAttribute(k.PAD_ATTRIB_NPTH)
        pad.SetShape(k.PAD_SHAPE_CIRCLE)
        pad.SetDrillShape(k.PAD_DRILL_SHAPE_CIRCLE)
        pad.SetSize(k.VECTOR2I(diameter, diameter))
        pad.SetDrillSize(k.VECTOR2I(diameter, diameter))
        mask = k.LSET()
        mask.AddLayer(k.F_Mask)
        mask.AddLayer(k.B_Mask)
        pad.SetLayerSet(mask)
        footprint.Add(pad)
        courtyard = k.PCB_SHAPE(footprint)
        courtyard.SetShape(k.SHAPE_T_CIRCLE)
        courtyard.SetStart(k.VECTOR2I(0, 0))
        courtyard.SetEnd(k.VECTOR2I((diameter + 1) // 2 + native_units(.1), 0))
        courtyard.SetWidth(native_units(.05))
        courtyard.SetLayer(k.F_CrtYd)
        footprint.Add(courtyard)
        board.Add(footprint)
        footprint.SetPosition(center)
        zone = k.ZONE(board)
        zone.SetIsRuleArea(True)
        zone.SetZoneName('MCP_mounting_' + ref)
        zone.SetLayerSet(layers)
        zone.SetDoNotAllowTracks(True)
        zone.SetDoNotAllowVias(True)
        zone.SetDoNotAllowPads(True)
        zone.SetDoNotAllowZoneFills(True)
        zone.SetDoNotAllowFootprints(False)
        keepout_polygon(k, zone, center, (diameter + 1) // 2 + native_units(row['keepout_mm']))
        board.Add(zone)


def inspect(board, k, constraints):
    holes = constraints.get('mounting_holes', [])
    footprints = list(board.GetFootprints())
    pads = {fp.GetReference(): pad for fp in footprints if (pad := mechanical_pad(fp, k)) is not None}
    zones = {}
    for zone in board.Zones():
        name = zone.GetZoneName()
        if name.startswith('MCP_mounting_'):
            zones.setdefault(name, []).append(zone)
    if not holes and not pads and not zones:
        return None
    components = [fp for fp in footprints if fp.GetReference() not in pads]
    found = set()
    failures = []
    rows = []
    layers = tuple(board.GetEnabledLayers().CuStack())
    for index, row in enumerate(holes, start=1):
        center = position(k, row)
        diameter = native_units(row['diameter_mm'])
        radius = (diameter + 1) // 2 + native_units(row['keepout_mm'])
        matching = [ref for ref, pad in pads.items() if pad.GetPosition() == center and pad.GetDrillSize().x == diameter]
        if len(matching) != 1 or matching[0] in found:
            failures.append('MOUNTING_HOLE_MISMATCH:' + str(index))
            continue
        ref = matching[0]
        found.add(ref)
        areas = zones.get('MCP_mounting_' + ref, [])
        expected_zone = k.ZONE(board)
        expected_polygon = keepout_polygon(k, expected_zone, center, radius)
        valid_area = False
        if len(areas) == 1:
            area = areas[0]
            delta = k.SHAPE_POLY_SET(area.Outline())
            delta.BooleanXor(expected_polygon)
            valid_area = (area.GetIsRuleArea() and all(area.IsOnLayer(layer) for layer in layers)
                          and area.GetDoNotAllowTracks() and area.GetDoNotAllowVias()
                          and area.GetDoNotAllowPads() and area.GetDoNotAllowZoneFills()
                          and not delta.OutlineCount())
        if not valid_area:
            failures.append('MOUNTING_KEEPOUT_CHANGED:' + ref)
        intrusions = []
        for fp in components:
            box = fp.GetBoundingBox(False, False)
            dx = max(box.GetX() - center.x, center.x - box.GetRight(), 0)
            dy = max(box.GetY() - center.y, center.y - box.GetBottom(), 0)
            if dx * dx + dy * dy < radius * radius:
                intrusions.append(fp.GetReference())
        if intrusions:
            failures.append('MOUNTING_KEEPOUT_COMPONENT:' + ref + ':' + ','.join(sorted(intrusions)))
        rows.append(dict(ref=ref, **row, rule_area_valid=valid_area, component_intrusions=sorted(intrusions)))
    if pads.keys() - found:
        failures.append('MOUNTING_HOLE_UNEXPECTED')
    if zones.keys() - {'MCP_mounting_' + ref for ref in found}:
        failures.append('MOUNTING_KEEPOUT_UNEXPECTED')
    return dict(passed=not failures, holes=rows, failures=failures)
