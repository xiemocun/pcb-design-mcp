"""Deterministic duplicate and dead-end track removal after SES import.

Only straight PCB_TRACK segments are removed. Every retained component/pad/net
is independently checked, and KiCad DRC must still pass before review/export.
"""
def try_widen(board,k,track,width_mm,clearance_mm):
    """Keep the router geometry when a width increase would hit other copper.

    This is a conservative mutation guard, not a replacement for native DRC.
    Rejected changes remain subject to the declared minimum-width rule.
    """
    old=track.GetWidth();track.SetWidth(k.FromMM(width_mm))
    layer=track.GetLayer();shape=track.GetEffectiveShape(layer)
    blockers=[]
    for item in list(board.GetTracks())+[p for f in board.GetFootprints() for p in f.Pads()]:
        if item is track or not item.IsOnLayer(layer):continue
        if item.GetNetCode()==track.GetNetCode() and item.GetNetCode()!=0:continue
        if item.GetEffectiveShape(layer).Collide(shape,k.FromMM(clearance_mm)):
            blockers.append(item.m_Uuid.AsString())
    for zone in list(board.Zones())+[z for f in board.GetFootprints() for z in f.Zones()]:
        if zone.IsOnLayer(layer) and zone.GetIsRuleArea() and zone.GetDoNotAllowTracks() and zone.Outline().Collide(shape):
            blockers.append(zone.m_Uuid.AsString())
    if blockers:track.SetWidth(old)
    return dict(applied=not blockers,blockers=sorted(blockers))


def clean(board,k,preserve_locked=False):
    removed=[];seen=set()
    def point(p):return (p.x,p.y)
    def record(t,reason):removed.append(dict(uuid=str(t.m_Uuid.AsString()),net=t.GetNetname(),layer=board.GetLayerName(t.GetLayer()),start_iu=point(t.GetStart()),end_iu=point(t.GetEnd()),reason=reason));board.RemoveNative(t)
    for t in list(board.GetTracks()):
        if type(t)!=k.PCB_TRACK:continue
        key=(t.GetLayer(),t.GetNetCode(),tuple(sorted([point(t.GetStart()),point(t.GetEnd())])),t.GetWidth())
        if key in seen and not (preserve_locked and t.IsLocked()):record(t,'exact_duplicate')
        else:seen.add(key)
    def on_segment(p,t):
        a,b=point(t.GetStart()),point(t.GetEnd());px,py=point(p);dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
        frac=max(0,min(1,((px-a[0])*dx+(py-a[1])*dy)/den)) if den else 0
        # Contact must include the center line, using the contacted item's own
        # half-width. Conservative contacts keep extra segments rather than
        # deleting real junctions. Independent DRC catches any disconnection.
        return (px-a[0]-frac*dx)**2+(py-a[1]-frac*dy)**2<=(t.GetWidth()/2+1)**2
    pads=[p for f in board.GetFootprints() for p in f.Pads()]
    while True:
        tracks=list(board.GetTracks());dead=None
        for t in tracks:
            if type(t)!=k.PCB_TRACK or (preserve_locked and t.IsLocked()):continue
            def connected(p):
                if any(pad.GetNetCode()==t.GetNetCode() and pad.IsOnLayer(t.GetLayer()) and pad.HitTest(p) for pad in pads):return True
                for other in tracks:
                    if other is t or other.GetNetCode()!=t.GetNetCode():continue
                    if type(other)==k.PCB_VIA:
                        if other.IsOnLayer(t.GetLayer()) and other.HitTest(p):return True
                    elif type(other)==k.PCB_TRACK and other.GetLayer()==t.GetLayer() and on_segment(p,other):return True
                return False
            if not connected(t.GetStart()) or not connected(t.GetEnd()):dead=t;break
        if dead is None:break
        record(dead,'dead_end_no_pad_via_or_same_layer_track_contact')
    board.BuildConnectivity()
    # Use the same native predicate as KiCad's DRC connectivity provider.
    # True ignores tracks wholly under pads; vias/arcs are never removed here.
    # Rebuild after every deletion so a newly exposed leaf is tested afresh.
    while True:
        connectivity=board.GetConnectivity()
        dead=next((t for t in board.GetTracks() if type(t)==k.PCB_TRACK
                   and not (preserve_locked and t.IsLocked())
                   and connectivity.TestTrackEndpointDangling(t,True)),None)
        if dead is None:break
        record(dead,'native_KiCad_DRC_dangling_track_endpoint')
        board.BuildConnectivity()
    return dict(policy='duplicate_then_conservative_and_native_KiCad_DRC_leaf_pruning',removed_count=len(removed),removed=removed,requires_independent_connectivity_and_drc=True)
