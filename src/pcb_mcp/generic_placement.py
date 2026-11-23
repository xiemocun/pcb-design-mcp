"""Actual-geometry edge connector and local-passive placement for generic boards."""
import math
try:from .pin_roles import ground_names
except ImportError:from pin_roles import ground_names

def grid_shape(count,width,height,margin,sizes):
 """Try finite cell arrangements using actual native bounds; never resize PCB.

 Preserve the old arrangement when it fits. A count crossing a sqrt rounding
 threshold must not reject a board if another equally bounded grid fits.
 """
 if count<=0 or len(sizes)!=count:raise ValueError('PLACEMENT_GEOMETRY_REQUIRED')
 preferred=math.ceil(math.sqrt(count*width/height))
 candidates=sorted(range(1,count+1),key=lambda cols:(cols!=preferred,abs(cols-preferred),cols))
 for cols in candidates:
  rows=math.ceil(count/cols);cw=(width-2*margin)/cols;ch=(height-2*margin)/rows
  if all(x+4<=cw and y+4<=ch for x,y in sizes):return cols,rows
 raise ValueError('PLACEMENT_CAPACITY_EXCEEDED: no bounded grid fits native footprint sizes')

def _pack_shelves(rectangles, width, height):
    """Pack padded rectangles in integer micrometres, tallest first."""
    order = sorted(rectangles, key=lambda ref: (-rectangles[ref][1], -rectangles[ref][0], ref))
    shelves = []
    centers = {}
    occupied_height = 0
    for ref in order:
        w, h = rectangles[ref]
        if w > width:
            return None
        # Each earlier shelf is at least as tall as this footprint. Reuse the
        # tightest available remainder before consuming another row of height.
        fit = min(((width - used - w, index) for index, (_, used) in enumerate(shelves)
                   if used + w <= width), default=None)
        if fit is None:
            if occupied_height + h > height:
                return None
            y, x = occupied_height, 0
            shelves.append((y, w))
            occupied_height += h
        else:
            _, index = fit
            y, x = shelves[index]
            shelves[index] = (y, x + w)
        centers[ref] = (x + w/2, y + h/2)
    return centers, (max(used for _, used in shelves), occupied_height)


def _compact_positions(sizes, width, height, margin):
    """Try both shelf directions at successively smaller placement gaps."""
    unit = 1000
    available = tuple(math.floor((size - 2*margin)*unit) for size in (width, height))
    for gap_mm in (4, 3, 2, 1):
        padded = {ref: tuple(math.ceil(size*unit) + gap_mm*unit for size in dimensions)
                  for ref, dimensions in sizes.items()}
        for across_rows in (True, False):
            rectangles = padded if across_rows else {ref: size[::-1] for ref, size in padded.items()}
            extent = available if across_rows else available[::-1]
            result = _pack_shelves(rectangles, *extent)
            if result is None:
                continue
            centers, used = result
            # Spread the centers across spare board area for routing access.
            # Scaling center separations up cannot create a body overlap.
            factors = tuple(size / occupied for size, occupied in zip(extent, used))
            positions = {ref: tuple(margin + coordinate*factor/unit for coordinate, factor in
                                    zip(center, factors)) for ref, center in centers.items()}
            return positions if across_rows else {ref: center[::-1] for ref, center in positions.items()}
    raise ValueError('PLACEMENT_CAPACITY_EXCEEDED: native-size packing exhausted within board bounds')


def bounded_positions(sizes,width,height,margin):
 """Retain fitting layouts, then use adaptive native-size packing."""
 refs=sorted(sizes)
 try:
  cols,rows=grid_shape(len(refs),width,height,margin,[sizes[r] for r in refs])
  cw=(width-2*margin)/cols;ch=(height-2*margin)/rows
  return {ref:(margin+(i%cols+.5)*cw,margin+(i//cols+.5)*ch) for i,ref in enumerate(refs)}
 except ValueError as exc:
  if not str(exc).startswith('PLACEMENT_CAPACITY_EXCEEDED'):raise
 for axis in [1,0]:
  order=sorted(refs,key=lambda ref:(-sizes[ref][axis],-sizes[ref][1-axis],ref));x=y=margin;row_height=0;positions={}
  for ref in order:
   fw,fh=(v+4 for v in sizes[ref])
   if fw>width-2*margin or fh>height-2*margin:break
   if x+fw>width-margin:x=margin;y+=row_height;row_height=0
   if y+fh>height-margin:break
   positions[ref]=(x+fw/2,y+fh/2);x+=fw;row_height=max(row_height,fh)
  if len(positions)==len(refs):return positions
 return _compact_positions(sizes,width,height,margin)

def improve(board,k,state):
 fps={f.GetReference():f for f in board.GetFootprints()};comps=state['components'];moves=[];dims=state['board_constraints'];w,h,m=dims['width_mm'],dims['height_mm'],dims['edge_margin_mm']
 def xy(p):return k.ToMM(p.x),k.ToMM(p.y)
 def rect(f):
  q=f.GetBoundingBox(False,False);return [k.ToMM(q.GetX()),k.ToMM(q.GetY()),k.ToMM(q.GetRight()),k.ToMM(q.GetBottom())]
 def overlap(a,b):return a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]
 usb=[f for c in comps.values() if c['library_id']=='Connector:USB_C_Receptacle_USB2.0_16P' for f in [fps[c['ref']]]]
 if len(usb)>1:raise ValueError('GENERIC_USB_EDGE_ROLE_AMBIGUOUS')
 for f in usb:
  before=xy(f.GetPosition());f.SetOrientationDegrees(0.);f.SetPosition(k.VECTOR2I(k.FromMM(w/2),k.FromMM(h-3)));moves.append(dict(reference=f.GetReference(),policy='USB_edge',from_mm=before,to_mm=xy(f.GetPosition())))
 # Assign each shared-rail bypass to one host before moving any of them.
 # Otherwise the last IC steals every bypass from the preceding ICs.
 owners={};choices={}
 for host in sorted(comps.values(),key=lambda c:c['ref']):
  facts=state.get('part_bindings',{}).get(host['id'],{}).get('facts',{})
  if facts.get('power_budget_model')!='supply_pin_load':continue
  hf=fps[host['ref']];sn={p['number'] for p in host['pins'] if p['name'] in facts['supply_pins']};gn={p['number'] for p in host['pins'] if p['name'] in ground_names(state,host)}
  anchors=[p for p in hf.Pads() if p.GetNumber() in sn];supplies={p.GetNetCode() for p in anchors};grounds={p.GetNetCode() for p in hf.Pads() if p.GetNumber() in gn}
  for cap in comps.values():
   if cap['library_id']!='Device:C':continue
   cf=fps[cap['ref']];nets={p.GetNetCode() for p in cf.Pads()}
   if len(nets)!=2 or not nets&supplies or not nets&grounds:continue
   distance=min(math.dist(xy(p.GetPosition()),xy(q.GetPosition())) for p in cf.Pads() for q in anchors if p.GetNetCode()==q.GetNetCode())
   choices.setdefault(host['id'],[]).append((distance,cap['ref']))
 for host in sorted(choices,key=lambda cid:(len(choices[cid]),comps[cid]['ref'])):
  available=sorted((d,ref) for d,ref in choices[host] if ref not in owners)
  if not available:raise ValueError('GENERIC_DECOUPLING_COVERAGE: '+comps[host]['ref'])
  owners[available[0][1]]=host
 for ref in sorted({ref for candidates in choices.values() for _,ref in candidates}-set(owners)):
  owners[ref]=min((d,comps[host]['ref'],host) for host,candidates in choices.items() for d,name in candidates if name==ref)[2]
 for c in comps.values():
  facts=state.get('part_bindings',{}).get(c['id'],{}).get('facts',{})
  if facts.get('power_budget_model')!='supply_pin_load':continue
  f=fps[c['ref']];supply_numbers={p['number'] for p in c['pins'] if p['name'] in facts['supply_pins']};locals=set(facts.get('local_passive_pin_numbers',[]));anchors=[p for p in f.Pads() if p.GetNumber() in supply_numbers|locals]
  supply_nets={p.GetNetCode() for p in anchors if p.GetNumber() in supply_numbers};ground_nets={p.GetNetCode() for p in f.Pads() if p.GetNumber() in {q['number'] for q in c['pins'] if q['name'] in ground_names(state,c)}};local_nets={p.GetNetCode() for p in anchors if p.GetNumber() in locals};todo=[]
  for passive in comps.values():
   if passive['library_id'] not in ['Device:R','Device:C']:continue
   pf=fps[passive['ref']];nets={p.GetNetCode() for p in pf.Pads()};is_bypass=passive['library_id']=='Device:C' and bool(nets&supply_nets) and bool(nets&ground_nets)
   if is_bypass and owners.get(passive['ref'])!=c['id']:continue
   nn=nets&(supply_nets if is_bypass else local_nets)
   if nn:todo.append((not is_bypass,passive['ref'],pf,nn))
  for _,ref,pf,nn in sorted(todo,key=lambda x:(x[0],x[1])):
   targets=[p for p in anchors if p.GetNetCode() in nn];px,py=xy(targets[0].GetPosition());old=xy(pf.GetPosition());old_angle=pf.GetOrientationDegrees();candidates=[]
   blocked=[]
   for other in board.GetFootprints():
    if other.GetReference()==ref:continue
    a=rect(other);blocked.append([a[0]-.5,a[1]-.5,a[2]+.5,a[3]+.5])
   for angle in [0.,90.,180.,270.]:
    pf.SetOrientationDegrees(angle)
    for xi in range(math.floor((px-8)*2),math.ceil((px+8)*2)+1):
     for yi in range(math.floor((py-8)*2),math.ceil((py+8)*2)+1):
      x,y=xi/2,yi/2;pf.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));a=rect(pf)
      if not (a[0]>=m and a[1]>=m and a[2]<=w-m and a[3]<=h-m) or any(overlap(a,z) for z in blocked):continue
      distance=min(math.dist(xy(p.GetPosition()),xy(q.GetPosition())) for p in pf.Pads() for q in targets if p.GetNetCode()==q.GetNetCode());candidates.append((distance,math.dist((x,y),old),x,y,angle))
   if not candidates:raise ValueError('GENERIC_LOCAL_PASSIVE_CAPACITY: '+ref)
   best=min(candidates);_,_,x,y,angle=best;pf.SetOrientationDegrees(angle);pf.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));moves.append(dict(reference=ref,host=c['ref'],policy='source_model_local_passive',from_mm=old,to_mm=[x,y],from_angle=old_angle,to_angle=angle,pad_distance_mm=best[0]))
 return moves
