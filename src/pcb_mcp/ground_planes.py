"""KiCad-10 solid ground-plane generation and native connectivity evidence.

This bounded policy keeps an independently connected track backbone. It never
puts zone nodes into the track-resistance graph or treats a plane as zero ohms.
Source-reviewed timer and ESP32/buck layouts request bounded geometry policies.
"""
import hashlib,json,math
from pathlib import Path
try:from .stackup import copper_layers
except ImportError:from stackup import copper_layers
try:from .native_fabrication import routing_via_size,units
except ImportError:from native_fabrication import routing_via_size,units
LIBRARY='PCB_MCP:LTC6992xS6-1'
LOCAL_VIA_RADIUS_MM=3.5  # Project placement bound, not a manufacturer specification.

MAIN_BUCK='PCB_MCP:TPSM82821A_SIL'
MAIN_ESP='RF_Module:ESP32-WROOM-32E'
MAIN_LIBRARIES={MAIN_BUCK,MAIN_ESP,'74xGxx:74LVC1G07','74xGxx:74LVC1G17','Connector:USB_C_Receptacle_USB2.0_16P','Connector_Generic:Conn_01x06','Device:C','Device:C_Polarized','Device:R','PCB_MCP:AM2302_SIP4','PCB_MCP:SN74LVC1G14_DBV','PCB_MCP:TPS2553DBV','PCB_MCP:TUSB320LAI_GPIO','Regulator_Linear:TLV75533PDBV'}

def mainboard(s):
    return any(c['library_id']==MAIN_BUCK for c in s['components'].values()) and any(c['library_id']==MAIN_ESP for c in s['components'].values())

def definition(s):
    explicit=s.get('board_constraints',{}).get('ground_plane_net_id')
    if explicit is not None:
        try:from .explicit_placement import validate
        except ImportError:from explicit_placement import validate
        validate(s,s['board_constraints'])
        bc=s['board_constraints'];margin=max(.8,bc['edge_clearance_mm']+.3)
        return dict(net=s['nets'][explicit]['name'],layers=copper_layers(bc),bounds_mm=[margin,margin,bc['width_mm']-margin,bc['height_mm']-margin],clearance_mm=max(.15,bc['clearance_mm']),min_thickness_mm=max(.2,bc['track_width_mm']),policy='explicit_bounded_ground_planes_v1')
    if mainboard(s):
        if any(c['library_id'] not in MAIN_LIBRARIES for c in s['components'].values()):raise ValueError('GROUND_PLANE_LAYOUT_UNSUPPORTED')
        grounds=[n for n in s['nets'].values() if n['kind']=='ground']
        if len(grounds)!=1:raise ValueError('GROUND_PLANE_GROUND_INVALID')
        bc=s['board_constraints'];margin=max(.8,bc['edge_clearance_mm']+.3)
        return dict(net=grounds[0]['name'],layers=copper_layers(bc),bounds_mm=[margin,margin,bc['width_mm']-margin,bc['height_mm']-margin],clearance_mm=max(.3,bc['clearance_mm']),min_thickness_mm=max(.3,bc['track_width_mm']),policy='esp32_buck_ground_planes_v1')
    timers=[c for c in s['components'].values() if c['library_id']==LIBRARY]
    if not timers:return None
    if len(timers)!=1:raise ValueError('GROUND_PLANE_TIMER_AMBIGUOUS')
    c=timers[0];p=next(p for p in c['pins'] if p['number']=='2')
    nets=[n for n in s['nets'].values() if any(x['component_id']==c['id'] and x['pin_id']==p['pin_id'] for x in n['pins'])]
    if len(nets)!=1 or nets[0]['kind']!='ground':raise ValueError('GROUND_PLANE_GROUND_INVALID')
    # Unknown active-device keepouts must not be covered by an assumed plane.
    if any(x['library_id'] not in [LIBRARY,'Device:R','Device:C','Connector_Generic:Conn_01x02'] for x in s['components'].values()):raise ValueError('GROUND_PLANE_LAYOUT_UNSUPPORTED')
    bc=s['board_constraints'];margin=max(.8,bc['edge_clearance_mm']+.3)
    return dict(net=nets[0]['name'],layers=copper_layers(bc),bounds_mm=[margin,margin,bc['width_mm']-margin,bc['height_mm']-margin],clearance_mm=max(.3,bc['clearance_mm']),min_thickness_mm=max(.3,bc['track_width_mm']),policy='precision_timer_ground_planes_v1')

def name(layer):return 'MCP_ground_'+layer

def through_contacts(board,k,netname):
    """Actual plated through pads are vertical copper connections too.

    In particular module exposed-pad land patterns can already contain thermal
    holes. Requiring extra vias inside their component courtyard is incorrect.
    Neither SMD pads nor nonplated mounting holes qualify.
    """
    return [t for t in board.GetTracks() if type(t)==k.PCB_VIA and t.GetNetname()==netname]+[p for f in board.GetFootprints() for p in f.Pads() if p.GetAttribute()==k.PAD_ATTRIB_PTH and p.GetNetname()==netname and p.GetDrillSize().x>0 and p.IsOnLayer(k.F_Cu) and p.IsOnLayer(k.B_Cu)]

def local_ground_pads(board,s,k,netname):
    targets=[]
    assignment={(p['component_id'],p['pin_id']):n['name'] for n in s['nets'].values() for p in n['pins']}
    if s.get('board_constraints',{}).get('ground_plane_net_id'):
        selected={c['ref'] for c in s['components'].values() if not c['library_id'].startswith('Connector') and c['library_id']!='Device:R'}
    elif mainboard(s):
        selected={c['ref'] for c in s['components'].values() if c['library_id']==MAIN_BUCK}
        supplies={n['name'] for n in s['nets'].values() if n['kind']=='power'}
        selected.update(c['ref'] for c in s['components'].values() if c['library_id'] in ['Device:C','Device:C_Polarized'] and len({assignment.get((c['id'],p['pin_id'])) for p in c['pins']} & supplies)==1 and netname in {assignment.get((c['id'],p['pin_id'])) for p in c['pins']})
    else:
        selected={c['ref'] for c in s['components'].values() if c['library_id']==LIBRARY}
        timer=next(c for c in s['components'].values() if c['library_id']==LIBRARY)
        supply=assignment[(timer['id'],next(p['pin_id'] for p in timer['pins'] if p['number']=='5'))]
        selected.update(c['ref'] for c in s['components'].values() if c['library_id']=='Device:C' and {assignment.get((c['id'],p['pin_id'])) for p in c['pins']}=={supply,netname})
    for f in board.GetFootprints():
        if f.GetReference() in selected:targets.extend(p for p in f.Pads() if p.GetNetname()==netname)
    return targets

def stitch(board,k,s,d):
    """Fixed geometric candidate search; no client-specified coordinates."""
    minimum_drill=s['board_constraints'].get('min_drill_mm',.3)
    drill,diameter=routing_via_size(s['board_constraints'],(minimum_drill,max(.6,minimum_drill+.3)))
    clearance=max(d['clearance_mm'],s['board_constraints'].get('hole_clearance_mm',.15))
    placed=[]
    courtyards=[]
    for f in board.GetFootprints():
        f.BuildCourtyardCaches();poly=f.GetCourtyard(k.F_CrtYd)
        courtyards.append(poly if poly.OutlineCount() else f.GetBoundingBox(False,False))
    keepouts=[z for z in list(board.Zones())+[z for f in board.GetFootprints() for z in f.Zones()] if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    for pad in local_ground_pads(board,s,k,d['net']):
        origin=pad.GetPosition()
        found=min(2,sum(k.ToMM((t.GetPosition()-origin).EuclideanNorm())<=LOCAL_VIA_RADIUS_MM for t in through_contacts(board,k,d['net']))) if mainboard(s) or s.get('board_constraints',{}).get('ground_plane_net_id') else 0
        for radius in [1.+i*.25 for i in range(11)]:
            if found==2:break
            for angle in range(0,360,15):
                pos=k.VECTOR2I(origin.x+k.FromMM(radius*math.cos(math.radians(angle))),origin.y+k.FromMM(radius*math.sin(math.radians(angle))))
                x,y=k.ToMM(pos.x),k.ToMM(pos.y);x0,y0,x1,y1=d['bounds_mm']
                if not x0+diameter<x<x1-diameter or not y0+diameter<y<y1-diameter:continue
                # Keep drills outside actual non-rectangular courtyards and
                # pad copper; also clear every existing track and drilled via.
                circle=k.SHAPE_CIRCLE(pos,k.FromMM(diameter/2));blocked=False
                if any(poly.Collide(circle) if isinstance(poly,k.SHAPE_POLY_SET) else poly.Contains(pos) for poly in courtyards):continue
                if any(z.Outline().Collide(circle) for z in keepouts):continue
                for item in list(board.GetTracks())+[p for f in board.GetFootprints() for p in f.Pads()]:
                    if type(item)==k.PCB_TRACK and item.GetNetname()==d['net']:continue
                    for layer in board.GetEnabledLayers().CuStack():
                        if item.IsOnLayer(layer) and item.GetEffectiveShape(layer).Collide(circle,k.FromMM(clearance)):blocked=True;break
                    if blocked:break
                if blocked:continue
                via=k.PCB_VIA(board);via.SetPosition(pos);via.SetWidth(units(diameter));via.SetDrill(units(drill));via.SetViaType(k.VIATYPE_THROUGH);via.SetLayerPair(k.F_Cu,k.B_Cu);via.SetNetCode(pad.GetNetCode());board.Add(via)
                placed.append(dict(pad=pad.GetParentFootprint().GetReference()+'.'+pad.GetNumber(),uuid=via.m_Uuid.AsString(),at_mm=[x,y]));found+=1
                if found==2:break
        if found!=2:raise ValueError('GROUND_PLANE_LOCAL_VIA_PLACEMENT_FAILED: '+pad.GetParentFootprint().GetReference()+'.'+pad.GetNumber())
    return placed


def antenna_keepouts(board,k,s):
    """Compare to the hash-bound real footprint, then test actual copper."""
    expected=[];errors=[]
    if not mainboard(s):return expected,errors
    for c in s['components'].values():
        if c['library_id']!=MAIN_ESP:continue
        binding=s['footprint_bindings'][c['id']];source=Path(binding['source'])
        if hashlib.sha256(source.read_text().encode()).hexdigest()!=binding['definition_hash']:raise ValueError('GROUND_PLANE_RF_SOURCE_CHANGED')
        fp=next(f for f in board.GetFootprints() if f.GetReference()==c['ref'])
        if fp.IsFlipped():raise ValueError('GROUND_PLANE_RF_SIDE_UNSUPPORTED')
        template=k.FootprintLoad(str(source.parent),binding['footprint_id'].split(':',1)[1])
        template.SetOrientationDegrees(fp.GetOrientationDegrees());template.SetPosition(fp.GetPosition())
        zones=[z for z in template.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowZoneFills()]
        if not zones:raise ValueError('GROUND_PLANE_RF_SOURCE_MISSING')
        actual=[z for z in fp.Zones() if z.GetIsRuleArea()]
        for z in zones:
            layers=[l for l in board.GetEnabledLayers().CuStack() if z.IsOnLayer(l)];shape=polygon_hash(z.Outline())
            matches=[a for a in actual if polygon_hash(a.Outline())==shape and [l for l in board.GetEnabledLayers().CuStack() if a.IsOnLayer(l)]==layers and a.GetDoNotAllowTracks() and a.GetDoNotAllowVias() and a.GetDoNotAllowPads() and a.GetDoNotAllowZoneFills()]
            if len(matches)!=1:errors.append('GROUND_PLANE_RF_KEEPOUT_CHANGED')
            expected.append(dict(ref=c['ref'],polygon=k.SHAPE_POLY_SET(z.Outline()),layers=layers,hash=shape))
    return expected,errors

def add(board,k,s):
    d=definition(s)
    if not d:return None
    _,errors=antenna_keepouts(board,k,s)
    if errors:raise ValueError(','.join(errors))
    if any(not z.GetIsRuleArea() for z in board.Zones()):raise ValueError('GROUND_PLANE_EXISTING_ZONE')
    d['local_stitching']=stitch(board,k,s,d)
    for layer in d['layers']:
        z=k.ZONE(board);z.SetLayer(board.GetLayerID(layer));z.SetNetCode(board.FindNet(d['net']).GetNetCode());z.SetZoneName(name(layer))
        z.SetLocalClearance(k.FromMM(d['clearance_mm']));z.SetMinThickness(k.FromMM(d['min_thickness_mm']));z.SetPadConnection(k.ZONE_CONNECTION_FULL);z.SetIslandRemovalMode(k.ISLAND_REMOVAL_MODE_ALWAYS)
        x0,y0,x1,y1=d['bounds_mm'];poly=z.Outline();poly.NewOutline()
        for x,y in [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]:poly.Append(k.FromMM(x),k.FromMM(y))
        board.Add(z)
    board.BuildConnectivity()
    if not k.ZONE_FILLER(board).Fill(board.Zones()):raise ValueError('GROUND_PLANE_FILL_FAILED')
    return d

def polygon_hash(poly):
    def chain(c):
        pts=[(c.CPoint(i).x,c.CPoint(i).y) for i in range(c.PointCount())]
        if not pts:return []
        # Normalize starting vertex and winding without altering geometry.
        start=min(range(len(pts)),key=pts.__getitem__);a=pts[start:]+pts[:start];b=[a[0]]+list(reversed(a[1:]));return min(a,b)
    result=[]
    for i in range(poly.OutlineCount()):result.append([chain(poly.COutline(i)),sorted(chain(poly.CHole(i,j)) for j in range(poly.HoleCount(i)))])
    return hashlib.sha256(json.dumps(sorted(result),separators=(',',':')).encode()).hexdigest()


def stitched_return_requested(s,d):
    return any(s['nets'][r['net_id']]['name']==d['net'] and r.get('return_model')=='stitched_ground_planes' for r in s.get('pcb_copper_policy',{}).get('rules',[]))


def stitching_graph(board,k,d):
    """Connect individual filled polygons through actual plated annuli.

    A shared net/zone name is never an edge. Four points within each annulus
    must contact the SAME filled polygon on each layer. This proves a copper
    connection only, not zero resistance, current division or ampacity.
    """
    nodes=[];adj={};links=[]
    for z in board.Zones():
        if z.GetIsRuleArea() or z.GetNetname()!=d['net']:continue
        poly=z.GetFilledPolysList(z.GetLayer())
        for i in range(poly.OutlineCount()):
            key=z.GetZoneName()+':'+str(i);nodes.append((key,z.GetLayer(),poly,i));adj[key]=set()
    for t in through_contacts(board,k,d['net']):
        contacts=[]
        for key,layer,poly,index in nodes:
            if not t.IsOnLayer(layer):continue
            outer=(t.GetWidth(layer) if type(t)==k.PCB_VIA else min(t.GetSize().x,t.GetSize().y))/2
            inner=(t.GetDrillValue() if type(t)==k.PCB_VIA else max(t.GetDrillSize().x,t.GetDrillSize().y))/2
            if not 0<inner<outer:continue
            radius=int((outer+inner)/2);p=t.GetPosition()
            if all(poly.Contains(k.VECTOR2I(p.x+dx,p.y+dy),index) for dx,dy in [(radius,0),(-radius,0),(0,radius),(0,-radius)]):contacts.append(key)
        if len(contacts)>1:
            links.append(dict(uuid=t.m_Uuid.AsString(),nodes=contacts))
            for a in contacts:adj[a].update(set(contacts)-{a})
    reached=set();queue=list(adj)[:1]
    while queue:
        key=queue.pop()
        if key in reached:continue
        reached.add(key);queue.extend(adj[key]-reached)
    return dict(polygon_count=len(nodes),polygons=list(adj),plated_links=links,unreached_polygons=sorted(set(adj)-reached),connected=bool(adj) and len(reached)==len(adj),qualification='actual_polygon_annulus_connectivity_not_zero_resistance_or_current_distribution')

def inspect(path,board,k,s):
    zones=[z for z in board.Zones() if not z.GetIsRuleArea()]
    if not zones:return dict(status='not_present',supported=False,zones=[])
    d=definition(s)
    footprint_zones=[z for f in board.GetFootprints() for z in f.Zones() if not z.GetIsRuleArea()]
    if not d or footprint_zones:return dict(status='unsupported',supported=False,zones=[],errors=['GROUND_PLANE_LAYOUT_UNSUPPORTED'])
    keepouts,errors=antenna_keepouts(board,k,s);rows=[];saved_polys={};layer_vias=[];rf_rows=[]
    stitched=stitched_return_requested(s,d);graph=stitching_graph(board,k,d)
    if stitched and not graph['connected']:errors.append('GROUND_PLANE_STITCH_GRAPH_DISCONNECTED')
    expected={(name(layer),layer,d['net']) for layer in d['layers']}
    observed={(z.GetZoneName(),board.GetLayerName(z.GetLayer()),z.GetNetname()) for z in zones}
    if len(zones)!=len(d['layers']) or observed!=expected:errors.append('GROUND_PLANE_IDENTITY')
    board.BuildConnectivity();conn=board.GetConnectivity()
    pads=[p for f in board.GetFootprints() for p in f.Pads() if p.GetNetname()==d['net']]
    ident=lambda p:p.GetParentFootprint().GetReference()+'.'+p.GetNumber()
    for z in zones:
        layer=z.GetLayer();poly=z.GetFilledPolysList(layer);contact_pads=[p for p in conn.GetConnectedPads(z) if p.GetNetname()==d['net']];contacts={ident(p) for p in contact_pads};contact_ids={p.m_Uuid.AsString() for p in contact_pads}
        saved_polys[z.GetZoneName()]=k.SHAPE_POLY_SET(poly)
        # GetConnectedTracks excludes vias in this KiCad binding. Test actual
        # filled copper at four points inside each annular ring instead; never
        # use the transitive GetConnectedItems cluster as a direct contact.
        connected_vias=set()
        for t in through_contacts(board,k,d['net']):
            if not t.IsOnLayer(layer):continue
            outer=(t.GetWidth(layer) if type(t)==k.PCB_VIA else min(t.GetSize().x,t.GetSize().y))/2
            inner=(t.GetDrillValue() if type(t)==k.PCB_VIA else max(t.GetDrillSize().x,t.GetDrillSize().y))/2
            if not 0<inner<outer:continue
            radius=int((outer+inner)/2);center=t.GetPosition()
            if all(poly.Contains(k.VECTOR2I(center.x+dx,center.y+dy)) for dx,dy in [(radius,0),(-radius,0),(0,radius),(0,-radius)]):connected_vias.add(t.m_Uuid.AsString())
        layer_vias.append(connected_vias)
        # A continuous bottom plane is required. Mainboard top polygons are
        # independently checked below for through connections to that plane.
        complex_plane=mainboard(s) or bool(s.get('board_constraints',{}).get('ground_plane_net_id'))
        if not z.IsFilled() or poly.OutlineCount()==0 or (not stitched and poly.OutlineCount()!=1 and (not complex_plane or layer!=k.F_Cu)):errors.append('GROUND_PLANE_EMPTY_OR_SPLIT')
        expected_pads={ident(p) for p in pads if p.IsOnLayer(layer)}
        expected_ids={p.m_Uuid.AsString() for p in pads if p.IsOnLayer(layer)}
        if (not expected_ids and layer in [k.F_Cu,k.B_Cu]) or not expected_ids<=contact_ids:errors.append('GROUND_PLANE_PAD_DISCONNECTED')
        if len(list(z.GetLayerSet().Seq()))!=1 or z.GetPadConnection()!=k.ZONE_CONNECTION_FULL or z.GetIslandRemovalMode()!=k.ISLAND_REMOVAL_MODE_ALWAYS or z.GetMinThickness()<k.FromMM(d['min_thickness_mm']) or (z.GetLocalClearance() or 0)<k.FromMM(d['clearance_mm']):errors.append('GROUND_PLANE_SETTINGS')
        x0,y0,x1,y1=d['bounds_mm'];outline=z.Outline()
        coords={(outline.COutline(0).CPoint(i).x,outline.COutline(0).CPoint(i).y) for i in range(outline.COutline(0).PointCount())} if outline.OutlineCount()==1 else set()
        if coords!={(k.FromMM(x),k.FromMM(y)) for x,y in [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]} or outline.HoleCount(0):errors.append('GROUND_PLANE_BOUNDARY')
        rows.append(dict(name=z.GetZoneName(),layer=board.GetLayerName(layer),net=z.GetNetname(),filled_polygon_count=poly.OutlineCount(),filled_area_mm2=poly.Area()/1e12,fill_sha256=polygon_hash(poly),direct_ground_pads=sorted(contacts),direct_ground_pad_count=len(contact_ids),expected_ground_pad_count=len(expected_ids),direct_ground_vias=sorted(connected_vias),expected_layer_ground_pads=sorted(expected_pads)))
        for area in keepouts:
            if layer not in area['layers']:continue
            overlap=k.SHAPE_POLY_SET(poly);overlap.BooleanIntersection(area['polygon'])
            rf_rows.append(dict(ref=area['ref'],layer=board.GetLayerName(layer),expected_outline_sha256=area['hash'],copper_overlap_area_mm2=overlap.Area()/1e12,copper_overlap_polygons=overlap.OutlineCount()))
            if overlap.OutlineCount():errors.append('GROUND_PLANE_RF_COPPER_INTRUSION')
    common_vias=set.intersection(*layer_vias) if layer_vias else set();local=[];islands=[]
    if mainboard(s) or s.get('board_constraints',{}).get('ground_plane_net_id'):
        # Mainboard bottom copper must be one polygon. Each top polygon must
        # have an actual through via contacting that bottom plane; merely
        # sharing the zone's GND name is insufficient. Timer policy unchanged.
        for z in zones:
            if z.GetLayer()!=k.F_Cu:continue
            poly=z.GetFilledPolysList(k.F_Cu)
            for i in range(poly.OutlineCount()):
                contacts=[t.m_Uuid.AsString() for t in through_contacts(board,k,d['net']) if t.m_Uuid.AsString() in common_vias and poly.Contains(t.GetPosition(),i)]
                islands.append(dict(polygon=i,through_vias=contacts))
                if not contacts:errors.append('GROUND_PLANE_TOP_ISLAND_UNSTITCHED')
        for area in keepouts:
            for item in list(board.GetTracks())+[p for f in board.GetFootprints() for p in f.Pads()]:
                if any(item.IsOnLayer(layer) and area['polygon'].Collide(item.GetEffectiveShape(layer)) for layer in board.GetEnabledLayers().CuStack() if layer in area['layers']):errors.append('GROUND_PLANE_RF_CONDUCTOR_INTRUSION')

    for pad in local_ground_pads(board,s,k,d['net']):
        near=[dict(uuid=t.m_Uuid.AsString(),kind='via' if type(t)==k.PCB_VIA else 'plated_through_pad',distance_mm=k.ToMM((t.GetPosition()-pad.GetPosition()).EuclideanNorm())) for t in through_contacts(board,k,d['net']) if t.m_Uuid.AsString() in common_vias and k.ToMM((t.GetPosition()-pad.GetPosition()).EuclideanNorm())<=LOCAL_VIA_RADIUS_MM+.000001]
        if len(near)<2:errors.append('GROUND_PLANE_LOCAL_VIAS_MISSING')
        local.append(dict(pad=ident(pad),vias=near,project_max_radius_mm=LOCAL_VIA_RADIUS_MM))
    # Refill the board loaded for this inspection, in memory only. Reloading
    # the same project twice in one pcbnew process can lose rule context.
    # Native XOR tests geometry, not fracture bridge/vertex ordering.
    fresh=board;fresh.BuildConnectivity()
    if not k.ZONE_FILLER(fresh).Fill(fresh.Zones()):errors.append('GROUND_PLANE_REFILL_FAILED')
    differences=[]
    for z in fresh.Zones():
        if z.GetIsRuleArea():continue
        delta=saved_polys[z.GetZoneName()];delta.BooleanXor(z.GetFilledPolysList(z.GetLayer()))
        differences.append(dict(name=z.GetZoneName(),xor_polygon_count=delta.OutlineCount(),xor_area_mm2=delta.Area()/1e12))
    if any(x['xor_polygon_count'] for x in differences):errors.append('GROUND_PLANE_STALE_FILL')
    # Separate proof: removal of every plane must not break the pre-existing
    # ground return. This also keeps the existing track-only analysis honest.
    bare=k.LoadBoard(str(path))
    for z in list(bare.Zones()):
        if not z.GetIsRuleArea():bare.RemoveNative(z)
    bare.BuildConnectivity();bc=bare.GetConnectivity();bp=[p for f in bare.GetFootprints() for p in f.Pads() if p.GetNetname()==d['net']]
    seen=set();queue=bp[:1]
    while queue:
        item=queue.pop();uid=item.m_Uuid.AsString()
        if uid in seen:continue
        seen.add(uid);queue.extend(x for x in bc.GetConnectedItems(item) if x.GetNetname()==d['net'] and x.m_Uuid.AsString() not in seen)
    backbone=bool(bp) and all(p.m_Uuid.AsString() in seen for p in bp)
    if not backbone and not stitched:errors.append('GROUND_PLANE_BACKBONE_BROKEN')
    return dict(status='pass' if not errors else 'fail',supported=True,policy=d,zones=rows,local_ground_stitching=local,top_polygon_stitching=islands,antenna_keepout_checks=rf_rows,errors=sorted(set(errors)),refill_xor=differences,native_refill_matches_saved=not any(x.startswith('GROUND_PLANE_REFILL') or x=='GROUND_PLANE_STALE_FILL' for x in errors),track_only_ground_connected=backbone,return_model='stitched_ground_planes' if stitched else 'tracks',return_connected=graph['connected'] if stitched else backbone,stitch_graph=graph,zone_resistance_modeled=False,full_return_inductance_qualified=False,method='KiCad bottom continuity, top polygon stitching, physical pad/via contacts, source-bound RF exclusion, native refill XOR and plane-removal connectivity')
