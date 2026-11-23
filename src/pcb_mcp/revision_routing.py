"""Seed a placement-only revision with compatible copper from its verified parent."""

try:
    from .route_cleanup import clean
    from .sexpr import children, one, parse
except ImportError:
    from route_cleanup import clean
    from sexpr import children, one, parse


def order_seed_copper(dsn):
    """Order inherited wires and vias without changing their Specctra records."""
    # KiCad emits a literal quote in this declaration. Keep source offsets
    # aligned while substituting a token the S-expression parser accepts.
    tree = parse(dsn.replace('(string_quote ")', '(string_quote Q)'))
    wiring, = children(tree, 'wiring')
    records = sorted(children(wiring, 'wire') + children(wiring, 'via'), key=lambda row: row['start'])
    ordered = sorted(records, key=lambda row: (row['v'][0] == 'via', one(row, 'net')[0]))
    segments = []
    cursor = 0
    for slot, record in zip(records, ordered):
        segments.append(dsn[cursor:slot['start']])
        segments.append(dsn[record['start']:record['end']])
        cursor = slot['end']
    segments.append(dsn[cursor:])
    return ''.join(segments)


def point(vector):
    return vector.x, vector.y


def via_key(via):
    return [via.GetNetname(), *point(via.GetPosition())]


def track_key(track):
    return (track.GetNetname(), track.GetLayer(), track.GetWidth(),
            frozenset((point(track.GetStart()), point(track.GetEnd()))))


def pose(footprint):
    return point(footprint.GetPosition()), footprint.GetOrientationDegrees(), footprint.GetLayer()


def seed(board, parent, k, constraints):
    """Work on the routing copy. The newly generated unrouted board stays intact."""
    footprints = {fp.GetReference(): fp for fp in board.GetFootprints()}
    old = {fp.GetReference(): fp for fp in parent.GetFootprints()}
    if footprints.keys() != old.keys():
        raise ValueError('REVISION_ROUTING_FOOTPRINT_MISMATCH')
    moved = sorted(ref for ref, fp in footprints.items() if pose(fp) != pose(old[ref]))
    result = dict(moved=moved, inherited_tracks=0, inherited_vias=[], dropped_colliding=0)
    if not moved:
        return dict(result, applied=False, reason='NO_PLACEMENT_CHANGE')
    zones = list(parent.Zones()) + [zone for fp in old.values() for zone in fp.Zones()]
    if any(not zone.GetIsRuleArea() for zone in zones):
        return dict(result, applied=False, reason='PARENT_HAS_COPPER_ZONES')

    layers = list(board.GetEnabledLayers().CuStack())
    pads = [pad for ref in moved for pad in footprints[ref].Pads()]
    keepouts = [zone for ref in moved for zone in footprints[ref].Zones() if zone.GetIsRuleArea()]
    clearance = k.FromMM(constraints['clearance_mm'])
    hole_clearance = k.FromMM(constraints['hole_clearance_mm'])
    fixed_tracks = {track_key(t) for t in board.GetTracks() if type(t) == k.PCB_TRACK}
    fixed_vias = {tuple(via_key(v)) for v in board.GetTracks() if type(v) == k.PCB_VIA}
    added = set()
    for conductor in parent.GetTracks():
        kind = type(conductor)
        if kind not in (k.PCB_TRACK, k.PCB_VIA):
            continue
        if kind == k.PCB_TRACK and track_key(conductor) in fixed_tracks:
            continue
        if kind == k.PCB_VIA and tuple(via_key(conductor)) in fixed_vias:
            continue
        native_net = board.FindNet(conductor.GetNetname())
        if native_net is None or not native_net.GetNetCode():
            continue
        if conflicts(conductor, kind == k.PCB_VIA, pads, keepouts, layers, clearance, hole_clearance):
            result['dropped_colliding'] += 1
            continue
        item = conductor.Duplicate()
        item.SetParent(board)
        item.SetNet(native_net)
        item.SetLocked(False)
        board.Add(item)
        added.add(item.m_Uuid.AsString())
    clean(board, k, preserve_locked=True)
    retained = [item for item in board.GetTracks() if item.m_Uuid.AsString() in added]
    result['inherited_tracks'] = sum(type(item) == k.PCB_TRACK for item in retained)
    result['inherited_vias'] = sorted(via_key(item) for item in retained if type(item) == k.PCB_VIA)
    return dict(result, applied=bool(retained))


def conflicts(item, via, pads, keepouts, layers, clearance, hole_clearance):
    for layer in layers:
        if not item.IsOnLayer(layer):
            continue
        shape = item.GetEffectiveShape(layer)
        for pad in pads:
            if pad.HasHole() and (via or pad.GetNetname() != item.GetNetname()):
                if pad.GetEffectiveHoleShape().Collide(shape, hole_clearance):
                    return True
            if (pad.IsOnLayer(layer) and pad.GetNetname() != item.GetNetname()
                    and pad.GetEffectiveShape(layer).Collide(shape, clearance)):
                return True
        if any(zone.IsOnLayer(layer) and (zone.GetDoNotAllowVias() if via else zone.GetDoNotAllowTracks())
               and zone.Outline().Collide(shape) for zone in keepouts):
            return True
    return False


def prune_unused_vias(board, k, inherited_vias):
    """Remove carried vias used on at most one layer after rerouting."""
    inherited = {tuple(key) for key in inherited_vias}
    removed = []
    for via in list(board.GetTracks()):
        if type(via) != k.PCB_VIA or via.IsLocked() or tuple(via_key(via)) not in inherited:
            continue
        board.BuildConnectivity()
        connectivity = board.GetConnectivity()
        if sum(connectivity.IsConnectedOnLayer(via, layer) for layer in board.GetEnabledLayers().CuStack()
               if via.IsOnLayer(layer)) <= 1:
            removed.append(dict(uuid=via.m_Uuid.AsString(), net_position_iu=via_key(via)))
            board.RemoveNative(via)
    return removed
