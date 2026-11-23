"""Native geometry based engineering workbench; never a manufacturing exporter.
Raster search proposes copper. Independent native DRC decides correctness.
Run with KiCad Python: source.pcb target.pcb drc.json [net ...]
"""
import sys,json,heapq,math,shutil,time
from pathlib import Path
from PIL import Image,ImageDraw
import pcbnew as k
sys.path.insert(0,str(Path('src/pcb_mcp').resolve()))
source,target,report=map(Path,sys.argv[1:4]);wanted=set(sys.argv[4:]);b=k.LoadBoard(str(source))
for ext in ['.kicad_pro','.kicad_dru']:
    if source.with_suffix(ext).exists():shutil.copy2(source.with_suffix(ext),target.with_suffix(ext))
saved_zones=[]
for z in list(b.Zones()):
    if not z.GetIsRuleArea():saved_zones.append(z.Duplicate(False));b.RemoveNative(z)
b.BuildConnectivity();allitems=list(b.GetTracks())+[p for f in b.GetFootprints() for p in f.Pads()];byid={x.m_Uuid.AsString():x for x in allitems}
S=.025;W=3601;H=2601;N=W*H
layers=[k.F_Cu,k.B_Cu];moves=[(1,0,10),(-1,0,10),(0,1,10),(0,-1,10),(1,1,14),(1,-1,14),(-1,1,14),(-1,-1,14)]
def grid(p):return round(k.ToMM(p.x)/S),round(k.ToMM(p.y)/S)
def pos(x,y):return k.VECTOR2I(k.FromMM(x*S),k.FromMM(y*S))
def polygons(item,layer,clear):
    poly=k.SHAPE_POLY_SET()
    item.TransformShapeToPolygon(poly,layer,k.FromMM(clear),k.FromMM(.004),k.ERROR_OUTSIDE)
    return [[(k.ToMM(poly.COutline(i).CPoint(j).x)/S,k.ToMM(poly.COutline(i).CPoint(j).y)/S) for j in range(poly.COutline(i).PointCount())] for i in range(poly.OutlineCount())]
def component(item):
    conn=b.GetConnectivity();seen={};todo=[item]
    while todo:
        q=todo.pop();uid=q.m_Uuid.AsString()
        if uid in seen:continue
        seen[uid]=q
        todo.extend(x for x in conn.GetConnectedItems(q) if x.GetNetCode()==item.GetNetCode() and x.m_Uuid.AsString() in byid)
    return list(seen.values())
def coordinates(items):
    rows={}
    for q in items:
        points=[q.GetStart(),q.GetEnd()] if type(q)==k.PCB_TRACK else [q.GetPosition()]
        for layer in range(2):
            if not q.IsOnLayer(layers[layer]):continue
            for p in points:
                x,y=grid(p);rows[(layer,y,x)]=(p.x,p.y)
    return rows

def repair(a,c,width):
    global byid,allitems
    if a.GetNetname()=='MIC4_P' and type(a)==k.PAD:a,c=c,a
    ca,cc=component(a),component(c)
    if a.m_Uuid.AsString() in {x.m_Uuid.AsString() for x in cc}:return 'already_connected'
    net=a.GetNetname();starts=coordinates(ca);ends=coordinates(cc)
    # Native polygons are expanded before rasterization, with 0.02 mm extra
    # for grid approximation. Grid paths are still only proposals.
    blocked=[];vblocked=[]
    for layer in layers:
        im=Image.new('1',(W,H),0);draw=ImageDraw.Draw(im);vi=Image.new('1',(W,H),0);vd=ImageDraw.Draw(vi)
        for q in allitems:
            if not q.IsOnLayer(layer) or q.GetNetCode()==a.GetNetCode():continue
            for poly in polygons(q,layer,.15+width/2+.005):draw.polygon(poly,fill=1)
            for poly in polygons(q,layer,.15+.25+.005):vd.polygon(poly,fill=1)
        for z in list(b.Zones())+[z for f in b.GetFootprints() for z in f.Zones()]:
            if not z.IsOnLayer(layer):continue
            poly=k.SHAPE_POLY_SET(z.Outline());poly.Inflate(k.FromMM(.25+.005),k.CORNER_STRATEGY_ROUND_ALL_CORNERS,k.FromMM(.004))
            for i in range(poly.OutlineCount()):
                pts=[(k.ToMM(poly.COutline(i).CPoint(j).x)/S,k.ToMM(poly.COutline(i).CPoint(j).y)/S) for j in range(poly.COutline(i).PointCount())]
                if z.GetDoNotAllowTracks():draw.polygon(pts,fill=1)
                if z.GetDoNotAllowVias():vd.polygon(pts,fill=1)
        # All existing drill holes including same-net holes constrain a new via.
        for q in allitems:
            drill=q.GetDrillValue() if type(q)==k.PCB_VIA else q.GetDrillSize().x if type(q)==k.PAD else 0
            if drill:
                x,y=grid(q.GetPosition());r=(k.ToMM(drill)/2+.1+.25+.02)/S;vd.ellipse((x-r,y-r,x+r,y+r),fill=1)
        blocked.append(im.convert('L').tobytes());vblocked.append(vi.convert('L').tobytes())
    # Permit a layer change at an existing same-net via instead of a new drill.
    reuse={}
    for q in allitems:
        if q.GetNetCode()==a.GetNetCode() and (type(q)==k.PCB_VIA or type(q)==k.PAD and q.GetAttribute()==k.PAD_ATTRIB_PTH):
            x,y=grid(q.GetPosition());reuse[(x,y)]=q.GetPosition()
    endset={l*N+y*W+x for l,y,x in ends if 0<x<W-1 and 0<y<H-1 and not blocked[l][y*W+x]};endpoints=[(key%W,(key%N)//W) for key in endset]
    if not endpoints:return 'target_grid_blocked'
    # Distances to target bounding box are admissible and cheap.
    minx=min(p[0] for p in endpoints);maxx=max(p[0] for p in endpoints);miny=min(p[1] for p in endpoints);maxy=max(p[1] for p in endpoints)
    def heuristic(x,y):
        dx=max(minx-x,0,x-maxx);dy=max(miny-y,0,y-maxy);return 10*max(dx,dy)+4*min(dx,dy)
    queue=[];cost={};parent={};startnodes={}
    for row,p in starts.items():
        l,y,x=row
        if not 10<x<W-10 or not 10<y<H-10 or blocked[l][y*W+x]:continue
        idx=l*N+y*W+x;cost[idx]=0;startnodes[idx]=p;heapq.heappush(queue,(heuristic(x,y),0,idx))
    if not queue:return 'source_grid_blocked'
    count=0;t0=time.monotonic();found=None
    while queue:
        _,g,idx=heapq.heappop(queue)
        if cost.get(idx)!=g:continue
        if idx in endset:found=idx;break
        count+=1
        if count>4000000 or time.monotonic()-t0>50:return 'search_bound'
        l=idx//N;r=idx%N;y=r//W;x=r%W
        for dx,dy,weight in moves:
            nx,ny=x+dx,y+dy
            if not 10<nx<W-10 or not 10<ny<H-10:continue
            nr=ny*W+nx;ni=l*N+nr
            if blocked[l][nr] or (dx and dy and (blocked[l][y*W+nx] or blocked[l][ny*W+x])):continue
            ng=g+weight
            if ng<cost.get(ni,10**12):cost[ni]=ng;parent[ni]=idx;heapq.heappush(queue,(ng+heuristic(nx,ny),ng,ni))
        ni=(1-l)*N+r
        if not blocked[1-l][r] and ((x,y) in reuse or not (vblocked[0][r] or vblocked[1][r])):
            ng=g+150
            if ng<cost.get(ni,10**12):cost[ni]=ng;parent[ni]=idx;heapq.heappush(queue,(ng+heuristic(x,y),ng,ni))
    if found is None:return 'no_grid_path'
    route=[found]
    while route[-1] in parent:route.append(parent[route[-1]])
    route.reverse();pts=[(i//N,(i%N)//W,i%W) for i in route]
    # Compress only collinear runs. Preserve exact original pad/track endpoints.
    compact=[pts[0]]
    for i in range(1,len(pts)-1):
        d1=tuple(pts[i][j]-pts[i-1][j] for j in range(3));d2=tuple(pts[i+1][j]-pts[i][j] for j in range(3))
        if d1!=d2:compact.append(pts[i])
    compact.append(pts[-1]);objects=[]
    def track(layer,p,q):
        if p==q:return
        t=k.PCB_TRACK(b);t.SetStart(p);t.SetEnd(q);t.SetLayer(layers[layer]);t.SetWidth(k.FromMM(width));t.SetNetCode(a.GetNetCode());b.Add(t);objects.append(t)
    first=compact[0];last=compact[-1]
    track(first[0],k.VECTOR2I(*startnodes[route[0]]),pos(first[2],first[1]))
    for p,q in zip(compact,compact[1:]):
        l,y,x=p;ll,yy,xx=q
        if l==ll:track(l,pos(x,y),pos(xx,yy))
        elif (x,y) in reuse:
            for lay in range(2):track(lay,pos(x,y),reuse[(x,y)])
        else:
            v=k.PCB_VIA(b);v.SetPosition(pos(x,y));v.SetWidth(k.FromMM(.5));v.SetDrill(k.FromMM(.2));v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetNetCode(a.GetNetCode());b.Add(v);objects.append(v)
    track(last[0],pos(last[2],last[1]),k.VECTOR2I(*ends[last]))
    allitems+=objects;byid.update({x.m_Uuid.AsString():x for x in objects});b.BuildConnectivity()
    return dict(added=len(objects),expanded=count,seconds=round(time.monotonic()-t0,2))
raw=json.loads(report.read_text());findings=raw.get('findings',raw.get('violations',[])+raw.get('unconnected_items',[]));records=[]
findings=sorted(findings,key=lambda f:0 if any('REFP34' in x['description'] for x in f['items']) else 1 if any('A3V3' in x['description'] for x in f['items']) else 2)
for f in findings:
    if f['type']!='unconnected_items':continue
    a,c=[byid.get(x['uuid']) for x in f['items'][:2]]
    if a is None or c is None:continue
    if wanted and a.GetNetname() not in wanted:continue
    width={'A3V3':.2,'D3V3':.35,'DC5V':.6,'BAT':.8,'VSYS':.8,'V5':.6,'SPK_P':.25,'SPK_N':.25}.get(a.GetNetname(),.15)
    result=repair(a,c,width);row=dict(net=a.GetNetname(),result=result);records.append(row);print(row,flush=True)
# Native DRC lists redundant one-layer vias; removing them requires fresh DRC.
for f in findings:
    if f['type']!='via_dangling':continue
    v=byid.get(f['items'][0]['uuid'])
    if v is None or type(v)!=k.PCB_VIA:continue
    conn=b.GetConnectivity();touch=list(conn.GetConnectedItems(v));present=set()
    for q in touch:
        if type(q)==k.PCB_TRACK:present.add(q.GetLayer())
        elif type(q)==k.PAD:present.update(l for l in layers if q.IsOnLayer(l))
    pads=[p for p in allitems if type(p)==k.PAD and p.GetNetCode()==v.GetNetCode()]
    cluster={q.m_Uuid.AsString() for q in component(pads[0])} if pads else set()
    complete=bool(pads) and all(p.m_Uuid.AsString() in cluster for p in pads)
    if complete and len(present)<2 and v.GetNetname()!='GND':b.RemoveNative(v)
from route_cleanup import clean
cleanup=clean(b,k)
for z in saved_zones:b.Add(z)
b.BuildConnectivity()
if saved_zones:k.ZONE_FILLER(b).Fill(b.Zones())
k.SaveBoard(str(target),b)
target.with_suffix('.repair.json').write_text(json.dumps(records,indent=2))
print('Saved engineering proposal',str(target),'independent DRC REQUIRED',flush=True)
