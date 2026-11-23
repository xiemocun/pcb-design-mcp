"""Measure nominal vias and copper extents from a KiCad board."""


def routing_via_size(constraints, fallback=(.3, .6)):
    size = constraints.get('via_size')
    return (size['drill_mm'], size['pad_mm']) if size else fallback


def units(mm):
    return round(mm * 1_000_000)


def rectangle_bounds(board, k):
    outline = k.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(outline, False):
        return None
    if outline.OutlineCount() != 1 or outline.HoleCount(0):
        return None
    contour = outline.COutline(0)
    points = [(point.x, point.y) for point in (contour.CPoint(i) for i in range(contour.PointCount()))]
    if len(points) < 4:
        return None
    left, right = min(x for x, _ in points), max(x for x, _ in points)
    top, bottom = min(y for _, y in points), max(y for _, y in points)
    corners = {(left, top), (right, top), (right, bottom), (left, bottom)}
    if left == right or top == bottom or not corners.issubset(points):
        return None
    perimeter = 0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        if not (x1 == x2 and x1 in (left, right) or y1 == y2 and y1 in (top, bottom)):
            return None
        perimeter += abs(x2 - x1) + abs(y2 - y1)
    return (left, top, right, bottom) if perimeter == 2 * (right - left + bottom - top) else None


def via_measurement(board, via, layers, k):
    flashed = [layer for layer in layers if via.IsOnLayer(layer) and via.FlashLayer(layer)]
    padstack = via.Padstack()
    drill = via.GetDrillValue()
    primary = via.GetPrimaryDrillSize()
    secondary = (via.GetSecondaryDrillSize(), via.GetTertiaryDrillSize())
    return dict(
        uuid=via.m_Uuid.AsString(),
        drill_mm=k.ToMM(drill),
        round_drill=(via.GetPrimaryDrillShape() == k.PAD_DRILL_SHAPE_CIRCLE
                     and (primary.x <= 0 or primary == k.VECTOR2I(drill, drill))),
        additional_machining=(any(size is not None and (size.x > 0 or size.y > 0) for size in secondary)
                              or any(via.IsBackdrilledOrPostMachined(layer) for layer in layers)),
        pads_mm={board.GetLayerName(layer): k.ToMM(via.GetWidth(layer)) for layer in flashed},
        nonround_pad_layers=[board.GetLayerName(layer) for layer in flashed
                             if padstack.Shape(layer) != k.PAD_SHAPE_CIRCLE
                             or padstack.Size(layer) != k.VECTOR2I(via.GetWidth(layer), via.GetWidth(layer))],
    )


def copper_pad(pad, layer, k):
    if not pad.IsOnLayer(layer):
        return False
    if pad.GetAttribute() != k.PAD_ATTRIB_NPTH:
        return pad.FlashLayer(layer)
    hole = pad.GetDrillSize()
    bore_shape = k.PAD_SHAPE_CIRCLE if pad.GetDrillShape() == k.PAD_DRILL_SHAPE_CIRCLE else k.PAD_SHAPE_OVAL
    return pad.GetSize(layer) != hole or pad.GetShape(layer) != bore_shape or pad.GetOffset(layer) != k.VECTOR2I(0, 0)


def copper_items(board, k, layers):
    tracks = list(board.GetTracks())
    drawings = list(board.GetDrawings())
    zones = list(board.Zones())
    pads = []
    for fp in board.GetFootprints():
        pads.extend(fp.Pads())
        drawings.extend(fp.GraphicalItems())
        drawings.extend(field for field in fp.GetFields() if field.IsVisible())
        zones.extend(fp.Zones())
    for layer in layers:
        for track in tracks:
            if track.IsOnLayer(layer) and (not isinstance(track, k.PCB_VIA) or track.FlashLayer(layer)):
                kind = 'via' if isinstance(track, k.PCB_VIA) else 'track'
                yield kind, track, layer, track.GetEffectiveShape(layer, k.FLASHING_ALWAYS_FLASHED).BBox()
        for pad in pads:
            if copper_pad(pad, layer, k):
                yield 'pad', pad, layer, pad.GetEffectiveShape(layer, k.FLASHING_ALWAYS_FLASHED).BBox()
        for drawing in drawings:
            if not drawing.IsOnLayer(layer):
                continue
            if isinstance(drawing, k.PCB_SHAPE):
                yield 'copper_graphic', drawing, layer, drawing.GetEffectiveShape(layer).BBox()
            else:
                polygon = k.SHAPE_POLY_SET()
                drawing.TransformShapeToPolygon(polygon, layer, 0, 50, k.ERROR_OUTSIDE)
                if polygon.OutlineCount():
                    yield 'copper_graphic', drawing, layer, polygon.BBox()
                elif not isinstance(drawing, k.PCB_TEXT) or drawing.GetShownText(False).strip():
                    yield 'copper_graphic', drawing, layer, None
        for zone in zones:
            if not zone.GetIsRuleArea() and zone.IsOnLayer(layer):
                fill = zone.GetFilledPolysList(layer)
                if fill.OutlineCount():
                    yield 'filled_zone', zone, layer, fill.BBox()


def inspect(board, k, *, via_size=None, edge_clearance_mm=None, rectangle=False):
    errors = []
    layers = tuple(board.GetEnabledLayers().CuStack())
    board.BuildConnectivity()
    result = dict(failures=errors)
    if via_size is not None:
        measurements = [via_measurement(board, item, layers, k) for item in board.GetTracks()
                        if isinstance(item, k.PCB_VIA)]
        for via in measurements:
            if (not via['round_drill'] or via['additional_machining']
                    or units(via['drill_mm']) != units(via_size['drill_mm'])):
                errors.append('PCB_VIA_DRILL_MISMATCH:' + via['uuid'])
            if (not via['pads_mm'] or via['nonround_pad_layers']
                    or any(units(mm) != units(via_size['pad_mm']) for mm in via['pads_mm'].values())):
                errors.append('PCB_VIA_PAD_MISMATCH:' + via['uuid'])
        result['via_size'] = dict(expected=via_size, measured=sorted(measurements, key=lambda via: via['uuid']))
    if rectangle or edge_clearance_mm is not None:
        bounds = rectangle_bounds(board, k)
        result['rectangle_bounds_mm'] = [k.ToMM(value) for value in bounds] if bounds else None
        if bounds is None:
            errors.append('PCB_RECTANGLE_OUTLINE_MISMATCH')
        elif edge_clearance_mm is not None:
            left, top, right, bottom = bounds
            limit = units(edge_clearance_mm)
            nearest = {}
            counts = {}
            for kind, item, layer, box in copper_items(board, k, layers):
                uuid = item.m_Uuid.AsString()
                name = board.GetLayerName(layer)
                if box is None:
                    errors.append('PCB_COPPER_EDGE_GEOMETRY_UNSUPPORTED:' + name + ':' + uuid)
                    continue
                distance = min(box.GetX() - left, box.GetY() - top, right - box.GetRight(), bottom - box.GetBottom())
                if distance < limit:
                    errors.append('PCB_COPPER_EDGE_CLEARANCE_VIOLATION:' + name + ':' + uuid)
                if name not in nearest or distance < units(nearest[name]['distance_mm']):
                    nearest[name] = dict(distance_mm=k.ToMM(distance), uuid=uuid, kind=kind)
                counts[name] = counts.get(name, 0) + 1
            result['copper_edge'] = dict(required_minimum_mm=edge_clearance_mm, nearest_by_layer=nearest, measured_counts=counts)
    return dict(result, passed=not errors)
