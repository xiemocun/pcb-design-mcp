"""Place reference fields against native pad masks; no client positions accepted."""
def place(board,k,width,height):
    def rect(q):return [k.ToMM(q.GetX()),k.ToMM(q.GetY()),k.ToMM(q.GetRight()),k.ToMM(q.GetBottom())]
    def intersects(a,b):return a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]
    masks=[]
    for item in board.GetDrawings():
        if isinstance(item,k.PCB_TEXT) and item.GetLayer() in [k.F_SilkS,k.B_SilkS]:
            a,b,c,d=rect(item.GetBoundingBox());masks.append([a-.18,b-.18,c+.18,d+.18])
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            a,b,c,d=rect(pad.GetBoundingBox());masks.append([a-.18,b-.18,c+.18,d+.18])
        # References must avoid native silk outlines as well as pad openings.
        for item in fp.GraphicalItems():
            if item.GetLayer() in [k.F_SilkS,k.B_SilkS]:
                a,b,c,d=rect(item.GetBoundingBox());masks.append([a-.18,b-.18,c+.18,d+.18])
    placed=[];moves=[]
    for fp in sorted(board.GetFootprints(),key=lambda f:f.GetReference()):
        field=fp.Reference()
        if not field.IsVisible():continue
        pos=field.GetPosition();x,y=k.ToMM(pos.x),k.ToMM(pos.y)
        candidates=[(x,y)]+sorted(((x+dx*.5,y+dy*.5) for dx in range(-16,17) for dy in range(-16,17) if dx or dy),key=lambda p:((p[0]-x)**2+(p[1]-y)**2,p))
        for nx,ny in candidates:
            field.SetPosition(k.VECTOR2I(k.FromMM(nx),k.FromMM(ny)));q=rect(field.GetBoundingBox())
            if q[0]>=.3 and q[1]>=.3 and q[2]<=width-.3 and q[3]<=height-.3 and not any(intersects(q,z) for z in masks+placed):break
        else:raise ValueError('REFERENCE_TEXT_CAPACITY_EXCEEDED: '+fp.GetReference())
        placed.append(q)
        if (nx,ny)!=(x,y):moves.append(dict(reference=fp.GetReference(),before_mm=[x,y],after_mm=[nx,ny]))
    return moves
