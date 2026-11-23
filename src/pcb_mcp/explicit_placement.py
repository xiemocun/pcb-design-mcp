"""Bounded client placements checked against native geometry, never arbitrary code.

Coordinates express an engineering layout decision; they confer no electrical,
RF, acoustic or thermal correctness. Hash-bound DRC and net verification remain.
"""
PCB_UNITS_PER_MM = 1_000_000


def silkscreen_position(label):
    return tuple(round(label[axis] * PCB_UNITS_PER_MM) for axis in ('x_mm', 'y_mm'))


def validate(state,bc):
    try:from .stackup import validate as validate_stackup,copper_layers
    except ImportError:from stackup import validate as validate_stackup,copper_layers
    validate_stackup(bc)
    if bc.get('interface_markings'):
        try:from .interface_markings import requirements
        except ImportError:from interface_markings import requirements
        requirements(state,bc)
    rows=bc.get('placements',[]);explicit=bc.get('placement_profile')=='explicit'
    ids=[p['component_id'] for p in rows]
    if explicit:
        if len(ids)!=len(set(ids)) or set(ids)!=set(state['components']):
            raise ValueError('Explicit placements must name every current component exactly once')
    elif rows:raise ValueError('Placements require the explicit profile')
    from_path=bc.get('path_constraints',[])
    assignments={(p['component_id'],p['pin_id']):n['id'] for n in state['nets'].values() for p in n['pins']} if from_path else {}
    for row in from_path:
        ends=[]
        for role in ('source','target'):
            endpoint=row[role];key=(endpoint['component_id'],endpoint['pin_id'])
            component=state['components'].get(key[0])
            if not component or not any(p['pin_id']==key[1] for p in component['pins']) or key not in assignments:raise ValueError('PATH_ENDPOINT_NOT_CONNECTED')
            ends.append(key)
        if ends[0]==ends[1] or assignments[ends[0]]!=assignments[ends[1]]:raise ValueError('PATH_ENDPOINT_NET_MISMATCH')
    for row in rows:
        if not 0<=row['x_mm']<=bc['width_mm'] or not 0<=row['y_mm']<=bc['height_mm']:
            raise ValueError('Placement origin outside board')
    ground=bc.get('ground_plane_net_id')
    if ground is not None:
        if ground not in state['nets'] or state['nets'][ground]['kind']!='ground':raise ValueError('Plane must name an existing ground net')
        if len(bc.get('ground_plane_reason',''))<5:raise ValueError('Ground plane requires an engineering reason')
    for row in bc.get('copper_keepouts',[]):
        if not 0<=row['x_min_mm']<row['x_max_mm']<=bc['width_mm'] or not 0<=row['y_min_mm']<row['y_max_mm']<=bc['height_mm']:
            raise ValueError('Invalid bounded keepout rectangle')
        if not any(row[k] for k in ('no_tracks','no_vias','no_fills')):raise ValueError('Keepout forbids nothing')
    for row in bc.get('local_routes',[]):
        if not explicit:raise ValueError('Local routes require explicit placement')
        if row['layer'] not in copper_layers(bc):raise ValueError('LOCAL_ROUTE_LAYER_DISABLED')
        if row['net_id'] not in state['nets']:raise ValueError('Local route refers to an absent net')
        radius=row['width_mm']/2+bc.get('edge_clearance_mm',.5)
        points=[(p['x_mm'],p['y_mm']) for p in row['points']]
        if any(not radius<=x<=bc['width_mm']-radius or not radius<=y<=bc['height_mm']-radius for x,y in points):raise ValueError('Local route outside copper bounds')
        if any(a==b for a,b in zip(points,points[1:])):raise ValueError('Zero-length local route segment')


    for row in bc.get('local_vias',[]):
        if not explicit:raise ValueError('Local vias require explicit placement')
        if row['net_id'] not in state['nets']:raise ValueError('Local via refers to an absent net')
        if row['drill_mm']<bc['min_drill_mm'] or row['diameter_mm']-row['drill_mm']<.25:raise ValueError('Local via drill/annulus below process minimum')
        radius=row['diameter_mm']/2+bc.get('edge_clearance_mm',.5)
        if not radius<=row['x_mm']<=bc['width_mm']-radius or not radius<=row['y_mm']<=bc['height_mm']-radius:raise ValueError('Local via outside copper bounds')


def plan(state):
    bc=state['board_constraints'];validate(state,bc)
    if bc.get('placement_profile')!='explicit':return None
    return {p['component_id']:(p['x_mm'],p['y_mm'],p['rotation_deg']) for p in bc['placements']}


def native_check(board,k,state):
    """Check actual courtyards, with a bounding-box fallback for missing geometry."""
    bc=state['board_constraints'];placed=[]
    for fp in board.GetFootprints():
        q=fp.GetBoundingBox(False,False)
        a=[k.ToMM(q.GetX()),k.ToMM(q.GetY()),k.ToMM(q.GetRight()),k.ToMM(q.GetBottom())]
        if not (a[0]>=0 and a[1]>=0 and a[2]<=bc['width_mm'] and a[3]<=bc['height_mm']):raise ValueError('EXPLICIT_BODY_OUTSIDE: '+fp.GetReference())
        fp.BuildCourtyardCaches()
        courtyard=fp.GetCourtyard(k.F_CrtYd)
        valid=courtyard.OutlineCount()>0 and not courtyard.IsSelfIntersecting()
        for ref,b,other,other_valid in placed:
            if a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]:
                if not valid or not other_valid or courtyard.Collide(other):
                    raise ValueError('EXPLICIT_BODY_OVERLAP: '+fp.GetReference()+' / '+ref)
        placed.append((fp.GetReference(),a,courtyard,valid))
    return [(ref,box) for ref,box,_,_ in placed]


def keepouts(board,k,state):
    for i,row in enumerate(state['board_constraints'].get('copper_keepouts',[])):
        z=k.ZONE(board);z.SetIsRuleArea(True);z.SetZoneName('MCP_explicit_keepout_'+str(i))
        layers=k.LSET()
        for layer in board.GetEnabledLayers().CuStack():layers.AddLayer(layer)
        z.SetLayerSet(layers)
        z.SetDoNotAllowTracks(row['no_tracks']);z.SetDoNotAllowVias(row['no_vias']);z.SetDoNotAllowZoneFills(row['no_fills']);z.SetDoNotAllowPads(False);z.SetDoNotAllowFootprints(False)
        p=z.Outline();p.NewOutline()
        for x,y in [(row['x_min_mm'],row['y_min_mm']),(row['x_max_mm'],row['y_min_mm']),(row['x_max_mm'],row['y_max_mm']),(row['x_min_mm'],row['y_max_mm'])]:p.Append(k.FromMM(x),k.FromMM(y))
        board.Add(z)


def local_routes(board,k,state):
    """Author declared copper, with collision rejection and independent DRC later."""
    try:from .route_cleanup import try_widen
    except ImportError:from route_cleanup import try_widen
    try:from .native_fabrication import routing_via_size,units
    except ImportError:from native_fabrication import routing_via_size,units
    records=[]
    for index,row in enumerate(state['board_constraints'].get('local_routes',[])):
        net=board.FindNet(state['nets'][row['net_id']]['name'])
        for a,b in zip(row['points'],row['points'][1:]):
            t=k.PCB_TRACK(board);t.SetStart(k.VECTOR2I(k.FromMM(a['x_mm']),k.FromMM(a['y_mm'])));t.SetEnd(k.VECTOR2I(k.FromMM(b['x_mm']),k.FromMM(b['y_mm'])))
            t.SetLayer(board.GetLayerID(row['layer']));t.SetNet(net);t.SetWidth(k.FromMM(row['width_mm']));t.SetLocked(True);board.Add(t)
            check=try_widen(board,k,t,row['width_mm'],state['board_constraints']['clearance_mm'])
            if not check['applied']:raise ValueError('LOCAL_ROUTE_COLLISION: '+str(index)+' '+net.GetNetname()+' '+','.join(check['blockers']))
            records.append(dict(route=index,uuid=t.m_Uuid.AsString(),net=net.GetNetname(),width_mm=row['width_mm']))
        if row.get('via_at_end'):
            p=row['points'][-1];v=k.PCB_VIA(board);v.SetPosition(k.VECTOR2I(k.FromMM(p['x_mm']),k.FromMM(p['y_mm'])))
            fallback=max(.2,state['board_constraints']['min_drill_mm'])
            drill,diameter=routing_via_size(state['board_constraints'],(fallback,max(.5,fallback+.3)))
            v.SetWidth(units(diameter));v.SetDrill(units(drill));v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetNet(net);v.SetLocked(True)
            circle=k.SHAPE_CIRCLE(v.GetPosition(),units(diameter/2));blockers=[]
            for other in list(board.GetTracks())+[p for f in board.GetFootprints() for p in f.Pads()]:
                if other.GetNetCode()==v.GetNetCode():continue
                for layer in board.GetEnabledLayers().CuStack():
                    if other.IsOnLayer(layer) and other.GetEffectiveShape(layer).Collide(circle,k.FromMM(state['board_constraints']['clearance_mm'])):blockers.append(other.m_Uuid.AsString());break
            for z in list(board.Zones())+[z for f in board.GetFootprints() for z in f.Zones()]:
                if z.GetIsRuleArea() and z.GetDoNotAllowVias() and z.Outline().Collide(circle):blockers.append(z.m_Uuid.AsString())
            if blockers:raise ValueError('LOCAL_VIA_COLLISION: '+str(index)+' '+net.GetNetname()+' '+','.join(blockers))
            board.Add(v);records.append(dict(route=index,uuid=v.m_Uuid.AsString(),net=net.GetNetname(),kind='via'))
    return records


def local_vias(board,k,state):
    """Explicit through vias. Filled/capped intent survives into process records."""
    try:from .native_fabrication import units
    except ImportError:from native_fabrication import units
    records=[];bc=state['board_constraints']
    for index,row in enumerate(bc.get('local_vias',[])):
        net=board.FindNet(state['nets'][row['net_id']]['name'])
        v=k.PCB_VIA(board);v.SetPosition(k.VECTOR2I(k.FromMM(row['x_mm']),k.FromMM(row['y_mm'])))
        v.SetWidth(units(row['diameter_mm']));v.SetDrill(units(row['drill_mm']));v.SetViaType(k.VIATYPE_THROUGH);v.SetLayerPair(k.F_Cu,k.B_Cu);v.SetNet(net);v.SetLocked(True)
        circle=k.SHAPE_CIRCLE(v.GetPosition(),k.FromMM(row['diameter_mm']/2));blockers=[]
        for other in list(board.GetTracks())+[p for f in board.GetFootprints() for p in f.Pads()]:
            if other.GetNetCode()!=v.GetNetCode():
                for layer in board.GetEnabledLayers().CuStack():
                    if other.IsOnLayer(layer) and other.GetEffectiveShape(layer).Collide(circle,k.FromMM(bc['clearance_mm'])):blockers.append(other.m_Uuid.AsString());break
            if type(other)==k.PCB_VIA:
                delta=other.GetPosition()-v.GetPosition()
                if (delta.x**2+delta.y**2)**.5 < (other.GetDrillValue()+v.GetDrillValue())/2+k.FromMM(bc['hole_to_hole_mm']):blockers.append('hole:'+other.m_Uuid.AsString())
        for z in list(board.Zones())+[z for f in board.GetFootprints() for z in f.Zones()]:
            if z.GetIsRuleArea() and z.GetDoNotAllowVias() and z.Outline().Collide(circle):blockers.append('keepout:'+z.m_Uuid.AsString())
        if blockers:raise ValueError('LOCAL_VIA_COLLISION: '+str(index)+' '+net.GetNetname()+' '+','.join(blockers))
        board.Add(v);records.append(dict(index=index,uuid=v.m_Uuid.AsString(),net=net.GetNetname(),**row))
    return records
