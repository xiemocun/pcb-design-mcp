"""KiCad direct contacts, deliberately not GetConnectedItems' whole cluster."""


def extract(board,k,names):
    board.BuildConnectivity();conn=board.GetConnectivity();nodes={};objects={}
    candidates=list(board.GetTracks())+[p for f in board.GetFootprints() for p in f.Pads()]
    for item in candidates:
        if item.GetNetname() not in names:continue
        ident=item.m_Uuid.AsString();objects[ident]=item
        row=dict(uuid=ident,net=item.GetNetname())
        if isinstance(item,k.PAD):row.update(kind='pad',reference=item.GetParentFootprint().GetReference(),number=item.GetNumber())
        elif type(item)==k.PCB_VIA:row.update(kind='via')
        elif type(item)==k.PCB_TRACK:row.update(kind='track',width_mm=k.ToMM(item.GetWidth()),length_mm=k.ToMM(item.GetLength()),layer=board.GetLayerName(item.GetLayer()))
        else:row.update(kind='unsupported')
        nodes[ident]=row
    for ident,item in objects.items():
        local=list(conn.GetConnectedTracks(item))+list(conn.GetConnectedPads(item))
        nodes[ident]['contacts']=sorted({p.m_Uuid.AsString() for p in local if p.m_Uuid.AsString() in nodes and p.GetNetCode()==item.GetNetCode() and p.m_Uuid.AsString()!=ident})
    return dict(method='KiCad GetConnectedTracks and GetConnectedPads direct copper contacts',nodes=nodes,zones_supported=False)
