"""Fixed KiCad-10 adapter; typed component placements, no executable input.

Isolated because the bundled pcbnew module uses Python 3.9, unlike the MCP server.
This version-specific adapter must be replaced before migrating to KiCad 11.
"""
import json,math,re,shutil,sys,tempfile,uuid
from pathlib import Path
import pcbnew as k
from mounting import add as add_mounting, inspect as inspect_mounting, mechanical_pad
from native_fabrication import inspect as inspect_fabrication, routing_via_size, units
import interface_markings


def box_mm(box):
    return [k.ToMM(box.GetX()),k.ToMM(box.GetY()),k.ToMM(box.GetRight()),k.ToMM(box.GetBottom())]

def conductors(board):
    out=[]
    for t in board.GetTracks():
        kind='track' if type(t)==k.PCB_TRACK else 'via' if type(t)==k.PCB_VIA else 'unsupported'
        row=dict(uuid=t.m_Uuid.AsString(),kind=kind,net=t.GetNetname(),layer=board.GetLayerName(t.GetLayer()),bbox_mm=box_mm(t.GetBoundingBox()),width_mm=k.ToMM(t.GetWidth(k.F_Cu) if kind=='via' else t.GetWidth()),length_mm=k.ToMM(t.GetLength()) if kind!='via' else 0.0)
        if kind=='via':row.update(drill_mm=k.ToMM(t.GetDrillValue()),layer_pair=[board.GetLayerName(t.TopLayer()),board.GetLayerName(t.BottomLayer())])
        out.append(row)
    return sorted(out,key=lambda v:v['uuid'])


def summary(board, omit_silkscreen_uuids=()):
    result=dict(version=k.GetBuildVersion(),conductors=conductors(board),components={},track_count=len(list(board.GetTracks())),outline_count=sum(d.GetLayer()==k.Edge_Cuts for d in board.GetDrawings()),outline=[[k.ToMM(d.GetStart().x),k.ToMM(d.GetStart().y),k.ToMM(d.GetEnd().x),k.ToMM(d.GetEnd().y)] for d in board.GetDrawings() if d.GetLayer()==k.Edge_Cuts],copper_items=[dict(bbox_mm=box_mm(t.GetBoundingBox())) for t in board.GetTracks()],copper_zone_count=sum(not z.GetIsRuleArea() for z in board.Zones()))
    labels=[dict(text=item.GetText(),x_mm=k.ToMM(item.GetPosition().x),y_mm=k.ToMM(item.GetPosition().y))
            for item in board.GetDrawings() if isinstance(item,k.PCB_TEXT) and item.GetLayer()==k.F_SilkS
            and item.m_Uuid.AsString() not in omit_silkscreen_uuids]
    if labels:result['silkscreen_labels']=labels
    for f in board.GetFootprints():
        if mechanical_pad(f,k) is not None:continue
        zones=[dict(bbox_mm=box_mm(z.GetBoundingBox()),all_copper=all(z.IsOnLayer(layer) for layer in board.GetEnabledLayers().CuStack()),no_tracks=z.GetDoNotAllowTracks(),no_vias=z.GetDoNotAllowVias(),no_pads=z.GetDoNotAllowPads(),no_fills=z.GetDoNotAllowZoneFills(),no_footprints=z.GetDoNotAllowFootprints()) for z in f.Zones() if z.GetIsRuleArea()]
        result['copper_zone_count']+=sum(not z.GetIsRuleArea() for z in f.Zones())
        result['components'][f.GetReference()]=dict(value=f.GetValue(),footprint=str(f.GetFPID().GetLibNickname())+':'+str(f.GetFPID().GetLibItemName()),position_mm=[k.ToMM(f.GetPosition().x),k.ToMM(f.GetPosition().y)],angle_deg=f.GetOrientationDegrees(),bbox_mm=box_mm(f.GetBoundingBox(False,False)),rule_areas=zones,pads=sorted([dict(number=p.GetNumber(),net=p.GetNetname(),at_mm=[k.ToMM(p.GetPosition().x),k.ToMM(p.GetPosition().y)]) for p in f.Pads() if p.GetNumber()],key=lambda p:(p['number'],p['net'])),source_fields={name:f.GetField(name).GetText() for name in ['Manufacturer','MPN','SourceURL'] if f.GetField(name)})
    return result


def escape_isolation(path,state):
    from copper_escape import definitions,allowed_segment
    out=[]
    for d in definitions(state):
        b=k.LoadBoard(str(path));a=summary(b);netname=state['nets'][d['net_id']]['name']
        rule=next(r for r in state['pcb_copper_policy']['rules'] if r['net_id']==d['net_id'])
        ids={t['uuid'] for t in a['conductors'] if t['net']==netname and t['width_mm']+1e-6<rule['min_width_mm'] and allowed_segment(d,t,a,netname)}
        for t in list(b.GetTracks()):
            if t.m_Uuid.AsString() in ids:b.RemoveNative(t)
        pads=[p for f in b.GetFootprints() if f.GetReference()!=d['ref'] for p in f.Pads() if p.GetNetname()==netname]
        b.BuildConnectivity();conn=b.GetConnectivity();seen=set();queue=pads[:1]
        while queue:
            item=queue.pop();ident=item.m_Uuid.AsString()
            if ident in seen:continue
            seen.add(ident)
            queue.extend(x for x in conn.GetConnectedItems(item) if x.GetNetname()==netname and x.m_Uuid.AsString() not in seen)
        out.append(dict(net_id=d['net_id'],removed_tracks=sorted(ids),non_controller_pad_count=len(pads),non_controller_pads_still_connected=bool(pads) and all(p.m_Uuid.AsString() in seen for p in pads),qualification='native_connectivity_after_removing_fanout_not_current_measurement_or_barrel_resistance_proof'))
    return out


def order_routing_items(board):
    """Order a disposable board before native serialization and DSN export."""
    from ground_planes import polygon_hash

    def point(vector):
        return vector.x, vector.y

    def copper_key(item):
        start, end = sorted((point(item.GetStart()), point(item.GetEnd())))
        geometry = (start, end)
        if type(item) == k.PCB_VIA:
            geometry += (item.TopLayer(), item.BottomLayer(), item.GetViaType(), item.GetDrillValue(),
                         item.GetWidth(item.TopLayer()))
        else:
            geometry += (item.GetWidth(),)
            if type(item) == k.PCB_ARC:
                geometry += (point(item.GetMid()),)
        return item.GetNetname(), item.GetLayer(), item.GetClass(), geometry, item.IsLocked()

    def zone_key(zone):
        return (zone.GetZoneName(), zone.GetNetname(), zone.GetAssignedPriority(),
                tuple(layer for layer in board.GetEnabledLayers().CuStack() if zone.IsOnLayer(layer)),
                zone.GetIsRuleArea(), zone.GetDoNotAllowTracks(), zone.GetDoNotAllowVias(),
                zone.GetDoNotAllowZoneFills(), polygon_hash(zone.Outline()))

    ordered = [*sorted(board.GetFootprints(), key=lambda item: item.GetReference()),
               *sorted(board.GetTracks(), key=copper_key),
               *sorted((item for item in board.GetDrawings() if item.GetLayer() == k.Edge_Cuts),
                       key=lambda item: (point(item.GetStart()), point(item.GetEnd()))),
               *sorted(board.Zones(), key=zone_key)]
    for index, item in enumerate(ordered, start=1):
        item.SetUuid(k.KIID(str(uuid.UUID(int=index))))


def export_dsn(b,folder,constraints,*,inherited_copper=False):
    # Specctra sessions cannot represent the unnamed mechanical footprint.
    # The routing copy keeps the all-layer rule area around each drill.
    if constraints.get('mounting_holes'):
        for footprint in list(b.GetFootprints()):
            if mechanical_pad(footprint,k) is not None:b.RemoveNative(footprint)
    source = Path(b.GetFileName())
    order_routing_items(b)
    # KiCad serializes these collections by UUID and reads them back in that
    # order. Keep the temporary UUIDs out of the delivered board and its checks.
    with tempfile.TemporaryDirectory(prefix='route-export-', dir=folder) as temporary:
        routing_path = Path(temporary) / 'order.kicad_pcb'
        for suffix in ('.kicad_pro', '.kicad_dru'):
            shutil.copyfile(source.with_suffix(suffix), routing_path.with_suffix(suffix))
        if not k.SaveBoard(str(routing_path), b, True):
            raise ValueError('Routing copy save failed')
        ordered = k.LoadBoard(str(routing_path))
        if not k.ExportSpecctraDSN(ordered, str(folder/'input.dsn')):
            raise ValueError('DSN export failed')
    # KiCad otherwise lowers the DSN SMD-to-SMD clearance to one quarter.
    # Preserve the explicit user constraint for every routing rule.
    dsn=(folder/'input.dsn').read_text()
    if '(unit um)' not in dsn:raise ValueError('Unsupported DSN units')
    header,rest=dsn.split('\n',1)
    if not re.fullmatch(r'\(pcb\s+(?:"(?:\\.|[^"\\])*"|[^\s()"]+)',header):raise ValueError('Unsupported native DSN header')
    # Identical geometry/rules must not contain a random project/build path
    # as the design identifier. File paths remain in command provenance.
    dsn='(pcb "pcb-mcp"\n'+rest
    dsn=re.sub(r'\(clearance [0-9.]+ \(type smd_smd\)\)', '(clearance '+str(constraints['clearance_mm']*1000)+' (type smd_smd))',dsn)
    if inherited_copper:
        from revision_routing import order_seed_copper
        dsn=order_seed_copper(dsn)
    (folder/'input.dsn').write_text(dsn)


def main():
    if not k.GetBuildVersion().startswith('10.'):raise ValueError('Unsupported KiCad major version; adapter requires KiCad 10')
    action=sys.argv[1];folder=Path(sys.argv[2]);state=json.loads((folder/'spec.json').read_text())
    if action=='build':
        constraints=state['board_constraints']
        from stackup import apply as apply_stackup
        b=k.BOARD();b.SetCopperLayerCount(constraints['layers'])
        b.GetDesignSettings().SetBoardThickness(k.FromMM(constraints.get('thickness_mm',1.6)))
        constraints=state['board_constraints'];w=constraints['width_mm'];h=constraints['height_mm'];margin=constraints['edge_margin_mm']
        via_drill, via_pad = map(units, routing_via_size(constraints))
        # Net class and design rules use the same requested values in DSN and DRC.
        d=b.GetDesignSettings();d.m_MinClearance=k.FromMM(constraints['clearance_mm']);d.m_TrackMinWidth=k.FromMM(constraints['track_width_mm'])
        d.m_CopperEdgeClearance=units(constraints['edge_clearance_mm']);d.m_HoleToHoleMin=k.FromMM(constraints.get('hole_to_hole_mm',.25));d.m_HoleClearance=k.FromMM(constraints.get('hole_clearance_mm',.25));d.m_MinThroughDrill=units(constraints.get('min_drill_mm',.3))
        nc=d.m_NetSettings.GetDefaultNetclass();nc.SetClearance(k.FromMM(constraints['clearance_mm']));nc.SetTrackWidth(k.FromMM(constraints['track_width_mm']))
        nc.SetViaDiameter(via_pad);nc.SetViaDrill(via_drill)
        netobjs={}
        for n in state['nets'].values():
            obj=k.NETINFO_ITEM(b,n['name']);b.Add(obj);netobjs[n['id']]=obj
        nc_file=folder/'native_nc_nets.json';nc_pads={}
        if nc_file.is_file():
            for net in json.loads(nc_file.read_text()):
                obj=k.NETINFO_ITEM(b,net['name']);b.Add(obj)
                for pin in net['pins']:nc_pads[pin['ref'],pin['number']]=obj
        elif any(i['intent']=='no_connect' for i in state.get('pin_intents',{}).values()):raise ValueError('NATIVE_NC_NETS_REQUIRED')
        assigned={(p['component_id'],p['pin_id']):n['id'] for n in state['nets'].values() for p in n['pins']}
        from placement_rules import reference_plan,reference_text_plan
        plan=reference_plan(state) if constraints['placement_profile']=='esp32_sensor_reference' else None
        from explicit_placement import plan as explicit_plan, native_check, keepouts as explicit_keepouts, silkscreen_position
        is_explicit=constraints.get('placement_profile')=='explicit'
        if is_explicit:plan=explicit_plan(state)
        text_plan=reference_text_plan(state) if plan and not is_explicit else {}
        parts=sorted(state['components'].values(),key=lambda c:c['ref']);cols=math.ceil(math.sqrt(len(parts)*w/h));rows=math.ceil(len(parts)/cols)
        metadata_file=folder/'native_component_fields.json'
        if state.get('part_bindings') and not metadata_file.is_file():raise ValueError('NATIVE_CAD_SOURCE_FIELDS_REQUIRED')
        source_fields=json.loads(metadata_file.read_text()) if metadata_file.is_file() else {}
        loaded={}
        for c in parts:
            binding=state['footprint_bindings'][c['id']];fp=k.FootprintLoad(str(Path(binding['source']).parent),binding['footprint_id'].split(':',1)[1])
            if fp is None:raise ValueError('Footprint load failed')
            fp.SetReference(c['ref']);fp.SetValue(c['value']);fp.SetFPID(k.LIB_ID(*binding['footprint_id'].split(':',1)))
            # Reference lettering belongs to this board's readability rules,
            # independent of the source footprint's pad/body geometry. Normalize
            # before packing so the resulting text box participates in layout.
            text_size=fp.Reference().GetTextSize()
            fp.Reference().SetTextSize(k.VECTOR2I(max(text_size.x,k.FromMM(.8)),max(text_size.y,k.FromMM(.8))))
            for name,value in source_fields.get(c['ref'],{}).items():
                field=fp.GetField(name)
                if field is None:field=k.PCB_FIELD(fp,k.FIELD_T_USER,name);fp.Add(field)
                field.SetText(value);field.SetVisible(False)
            b.Add(fp);loaded[c['id']]=fp
        if not plan:
            from generic_placement import bounded_positions
            sizes={fp.GetReference():(k.ToMM(fp.GetBoundingBox(False,False).GetWidth()),k.ToMM(fp.GetBoundingBox(False,False).GetHeight())) for fp in loaded.values()}
            positions=bounded_positions(sizes,w,h,margin)
        cw=(w-2*margin)/cols;ch=(h-2*margin)/rows
        if plan:parts.sort(key=lambda c:(c['id'] not in plan,c['ref']))
        placed=[]
        for i,c in enumerate(parts):
            fp=loaded[c['id']]
            # Bounding box includes courtyard, body and fields; put its center in
            # a bounded grid cell. Reject capacity rather than overlap quietly.
            box=fp.GetBoundingBox(False,False)
            cx=margin+(i%cols+.5)*cw;cy=margin+(i//cols+.5)*ch
            if not plan:cx,cy=positions[c['ref']]
            center=box.GetCenter()
            if plan and c['id'] in plan:
                x,y,angle=plan[c['id']];fp.SetOrientationDegrees(angle);fp.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)))
            elif plan:
                # Deterministic packing of additions with native body/pad boxes.
                # Keep full RF exclusion and 1mm separation from existing bodies.
                def rect(item):
                    q=item.GetBoundingBox(False,False)
                    return [k.ToMM(q.GetX()),k.ToMM(q.GetY()),k.ToMM(q.GetRight()),k.ToMM(q.GetBottom())]
                def overlaps(a,z):return a[0]<z[2] and a[2]>z[0] and a[1]<z[3] and a[3]>z[1]
                blocked=[[a-1,b-1,z+1,t+1] for a,b,z,t in map(rect,placed)]
                esp=next(f for f in placed if f.GetReference()==next(x['ref'] for x in parts if x['library_id']=='RF_Module:ESP32-WROOM-32E'))
                ex,ey=k.ToMM(esp.GetPosition().x),k.ToMM(esp.GetPosition().y)
                blocked.append([ex-24,ey-27.75,ex+24,ey-6.56])
                target=(ex-12,ey+4) if c['library_id']=='Device:C' else (w/2,h/2)
                candidates=sorted(((x,y) for x in range(math.ceil(margin),math.floor(w-margin)+1) for y in range(math.ceil(margin),math.floor(h-margin)+1)),key=lambda p:((p[0]-target[0])**2+(p[1]-target[1])**2,p))
                for x,y in candidates:
                    fp.SetPosition(k.VECTOR2I(k.FromMM(x),k.FromMM(y)));q=rect(fp)
                    if q[0]>=margin and q[1]>=margin and q[2]<=w-margin and q[3]<=h-margin and not any(overlaps(q,z) for z in blocked):break
                else:raise ValueError('PLACEMENT_CAPACITY_EXCEEDED: no safe space for '+c['ref'])
            else:fp.SetPosition(k.VECTOR2I(k.FromMM(cx)-center.x,k.FromMM(cy)-center.y))
            placed.append(fp)
            for p in fp.Pads():
                number=p.GetNumber()
                if not number:continue
                cp=next(pn for pn in c['pins'] if number in pn.get('physical_numbers',[pn['number']]))
                net=assigned.get((c['id'],cp['pin_id']))
                if net:p.SetNet(netobjs[net])
                elif (c['ref'],number) in nc_pads:p.SetNet(nc_pads[c['ref'],number])
            # Values stay on fabrication layer; references remain visible.
            fp.Value().SetVisible(False)
            if c['id'] in text_plan:
                tx,ty=text_plan[c['id']];fp.Reference().SetPosition(k.VECTOR2I(k.FromMM(tx),k.FromMM(ty)))
        for a,z in [((0,0),(w,0)),((w,0),(w,h)),((w,h),(0,h)),((0,h),(0,0))]:
            edge=k.PCB_SHAPE();edge.SetShape(k.SHAPE_T_SEGMENT);edge.SetStart(k.VECTOR2I(k.FromMM(a[0]),k.FromMM(a[1])));edge.SetEnd(k.VECTOR2I(k.FromMM(z[0]),k.FromMM(z[1])));edge.SetLayer(k.Edge_Cuts);edge.SetWidth(k.FromMM(.05));b.Add(edge)
        for rule in state.get('pcb_copper_policy',{}).get('rules',[]):
            name='MCP_'+rule['net_id'];nc=k.NETCLASS(name)
            nc.SetTrackWidth(k.FromMM(rule['min_width_mm']));nc.SetClearance(k.FromMM(constraints['clearance_mm']))
            nc.SetViaDiameter(via_pad);nc.SetViaDrill(via_drill)
            d.m_NetSettings.SetNetclass(name,nc)
            d.m_NetSettings.SetNetclassPatternAssignment(state['nets'][rule['net_id']]['name'],name)
            netobjs[rule['net_id']].SetNetClass(nc)
        if not plan:
            from generic_placement import improve as improve_generic_placement
            generic_moves=improve_generic_placement(b,k,state)
        else:generic_moves=[]
        from power_placement import improve as improve_power_placement
        power_moves=improve_power_placement(b,k,state) if plan and not is_explicit else []
        if is_explicit:native_check(b,k,state)
        explicit_keepouts(b,k,state)
        from explicit_placement import local_routes,local_vias
        local_route_records=local_routes(b,k,state)
        local_via_records=local_vias(b,k,state)
        if is_explicit and constraints.get('ground_plane_net_id'):
            # Reserve local return vias before signal routing can consume all
            # space near the supply pads. The routed fill is still independently
            # inspected; these vias do not establish a plane by themselves.
            from ground_planes import definition as plane_definition,stitch as seed_ground_vias
            seed_ground_vias(b,k,state,plane_definition(state))
        for label in constraints.get('silkscreen_labels',[]):
            item=k.PCB_TEXT(b)
            item.SetLayer(k.F_SilkS)
            item.SetText(label['text'])
            item.SetTextSize(k.VECTOR2I(k.FromMM(1.),k.FromMM(1.)))
            item.SetTextThickness(k.FromMM(.15))
            item.SetPosition(k.VECTOR2I(*silkscreen_position(label)))
            left,top,right,bottom=box_mm(item.GetBoundingBox())
            if left<.3 or top<.3 or right>w-.3 or bottom>h-.3:
                raise ValueError('SILKSCREEN_LABEL_OUTSIDE_BOARD: '+label['text'])
            b.Add(item)
        add_mounting(b,k,constraints)
        marking_rows=interface_markings.requirements(state,constraints)
        mark_ids=interface_markings.author(b,k,marking_rows,w,h)
        from reference_fields import place as place_reference_fields
        reference_moves=place_reference_fields(b,k,w,h)
        b.BuildListOfNets();b.SynchronizeNetsAndNetClasses(False)
        k.SaveBoard(str(folder/'unrouted.kicad_pcb'),b)
        apply_stackup(folder/'unrouted.kicad_pcb',constraints)
        b=k.LoadBoard(str(folder/'unrouted.kicad_pcb'))
        routing_base=folder/'routing_base.kicad_pcb'
        route_board=k.LoadBoard(str(folder/'unrouted.kicad_pcb'))
        inherited_copper=False
        if routing_base.is_file():
            from revision_routing import seed
            seeded=seed(route_board,k.LoadBoard(str(routing_base)),k,constraints)
            inherited_copper=seeded['applied']
            (folder/'revision_routing_seed.json').write_text(json.dumps(seeded,indent=2))
        export_dsn(route_board,folder,constraints,inherited_copper=inherited_copper)
        # KiCad's DSN exporter treats even non-restrictive board rule areas as
        # keepouts. Add only our informational fanout areas AFTER export; the
        # footprint's real RF keepouts were already exported unchanged.
        from copper_escape import all_definitions as definitions,area_name
        for definition in definitions(state):
            fp=next(f for f in b.GetFootprints() if f.GetReference()==definition['ref'])
            for number in definition['pads']:
                pad=next(p for p in fp.Pads() if p.GetNumber()==number);p=pad.GetPosition();radius=k.FromMM(definition['half_window_mm'])
                zone=k.ZONE(b);zone.SetIsRuleArea(True);zone.SetLayer(k.F_Cu);zone.SetZoneName(area_name(definition,number))
                for setter in ['SetDoNotAllowTracks','SetDoNotAllowVias','SetDoNotAllowPads','SetDoNotAllowCopperPour','SetDoNotAllowFootprints']:
                    if hasattr(zone,setter):getattr(zone,setter)(False)
                zone.SetDoNotAllowZoneFills(False)
                poly=zone.Outline();poly.NewOutline()
                for x,y in [(p.x-radius,p.y-radius),(p.x+radius,p.y-radius),(p.x+radius,p.y+radius),(p.x-radius,p.y+radius)]:poly.Append(x,y)
                b.Add(zone)
        k.SaveBoard(str(folder/'unrouted.kicad_pcb'),b)
        initial=dict(summary(b,mark_ids),reference_moves=reference_moves,power_moves=power_moves,generic_moves=generic_moves,local_route_records=local_route_records,local_via_records=local_via_records)
        if mark_ids:initial['interface_marking_uuids']=mark_ids
        (folder/'board_initial.json').write_text(json.dumps(initial,indent=2))
    elif action=='import':
        # SaveBoard normalizes/rewrites its adjacent project. The authoritative
        # rules were hashed before routing; retain their exact bytes.
        rules={folder/('routed.'+ext):(folder/('routed.'+ext)).read_bytes() for ext in ['kicad_pro','kicad_dru']}
        b=k.LoadBoard(str(folder/'unrouted.kicad_pcb'))
        if not k.ImportSpecctraSES(b,str(folder/'output.ses')):raise ValueError('SES import failed')
        from route_cleanup import clean,try_widen
        seed_report=folder/'revision_routing_seed.json'
        removed_vias=[]
        if seed_report.is_file():
            from revision_routing import prune_unused_vias
            removed_vias=prune_unused_vias(b,k,json.loads(seed_report.read_text())['inherited_vias'])
        cleanup=clean(b,k)
        if seed_report.is_file():cleanup['revision_unused_vias_removed']=removed_vias
        k.SaveBoard(str(folder/'routed.kicad_pcb'),b)
        for path,raw in rules.items():path.write_bytes(raw)
        # Restore backbone width if the router attached other ground loads
        # through a controller branch. Any new clearance error still fails DRC.
        from copper_escape import definitions,allowed_segment
        failed={x['net_id'] for x in escape_isolation(folder/'routed.kicad_pcb',state) if not x['non_controller_pads_still_connected']}
        restored=[];rejected=[];actual=summary(b)
        by_id={t.m_Uuid.AsString():t for t in b.GetTracks()}
        def widen(row,width,reason):
            result=try_widen(b,k,by_id[row['uuid']],width,state['board_constraints']['clearance_mm'])
            record=dict(uuid=row['uuid'],net=row['net'],from_mm=row['width_mm'],to_mm=width,reason=reason,**result)
            if not result['applied']:rejected.append(record)
            return record if result['applied'] else None
        for definition in definitions(state):
            if definition['net_id'] not in failed:continue
            rule=next(r for r in state['pcb_copper_policy']['rules'] if r['net_id']==definition['net_id'])
            netname=state['nets'][definition['net_id']]['name']
            for row in actual['conductors']:
                if row['net']==netname and row['width_mm']+1e-6<rule['min_width_mm'] and allowed_segment(definition,row,actual,netname):
                    changed=widen(row,rule['min_width_mm'],'restore_ground_backbone_for_non_controller_load')
                    if changed:restored.append(changed)
        # Router neckdown can extend beyond an authorized pad escape window.
        # Attempt the declared minimum only if foreign copper/keepouts remain
        # clear. Otherwise preserve the neckdown and its width-rule error.
        actual=summary(b);normalization=[];definitions_by_net={d['net_id']:d for d in definitions(state)}
        from short_escapes import definitions as neck_definitions,allowed as neck_allowed
        necks=neck_definitions(state)
        for rule in state.get('pcb_copper_policy',{}).get('rules',[]):
            netname=state['nets'][rule['net_id']]['name'];definition=definitions_by_net.get(rule['net_id'])
            for row in actual['conductors']:
                if row['kind']!='track' or row['net']!=netname or row['width_mm']+1e-6>=rule['min_width_mm']:continue
                if definition and allowed_segment(definition,row,actual,netname):continue
                if any(d['net_id']==rule['net_id'] and neck_allowed(d,row,actual,netname) for d in necks):continue
                changed=widen(row,rule['min_width_mm'],'enforce_declared_minimum_outside_managed_escape')
                if changed:normalization.append(changed)
        if restored or normalization:
            k.SaveBoard(str(folder/'routed.kicad_pcb'),b)
            for path,raw in rules.items():path.write_bytes(raw)
        cleanup['backbone_width_restoration']=restored;cleanup['requires_independent_copper_and_drc']=True
        cleanup['declared_width_restoration']=normalization
        cleanup['unsafe_width_changes_rejected']=rejected
        from ground_planes import add as add_ground_planes
        plane_policy=add_ground_planes(b,k,state)
        if plane_policy:
            k.SaveBoard(str(folder/'routed.kicad_pcb'),b)
            for path,raw in rules.items():path.write_bytes(raw)
            cleanup['ground_plane_policy']=plane_policy
        (folder/'router_result.json').write_text(json.dumps(cleanup,indent=2))
        (folder/'board_routed.json').write_text(json.dumps(summary(b),indent=2))
    elif action=='stabilize':
        # Refill in a fresh native process after serialization. KiCad can round
        # tiny polygon slivers on save; preserve strict XOR verification later.
        path=folder/'routed.kicad_pcb';b=k.LoadBoard(str(path))
        rules={folder/('routed.'+ext):(folder/('routed.'+ext)).read_bytes() for ext in ['kicad_pro','kicad_dru']}
        old={z.GetZoneName():k.SHAPE_POLY_SET(z.GetFilledPolysList(z.GetLayer())) for z in b.Zones() if not z.GetIsRuleArea()}
        b.BuildConnectivity()
        if old and not k.ZONE_FILLER(b).Fill(b.Zones()):raise ValueError('GROUND_PLANE_FILL_FAILED')
        changes=[]
        for z in b.Zones():
            if z.GetIsRuleArea():continue
            delta=old[z.GetZoneName()];delta.BooleanXor(z.GetFilledPolysList(z.GetLayer()))
            changes.append(dict(name=z.GetZoneName(),xor_polygon_count=delta.OutlineCount(),xor_area_mm2=delta.Area()/1e12))
        k.SaveBoard(str(path),b)
        for p,raw in rules.items():p.write_bytes(raw)
        (folder/'fill_serialization.json').write_text(json.dumps(changes,indent=2))
        (folder/'board_routed.json').write_text(json.dumps(summary(b),indent=2))
    elif action=='reseed':
        b=k.LoadBoard(str(folder/'seed.kicad_pcb'))
        from copper_escape import all_definitions as definitions,area_name
        expected={area_name(d,p) for d in definitions(state) for p in d['pads']}
        for zone in list(b.Zones()):
            if zone.GetZoneName() not in expected:continue
            if not zone.GetIsRuleArea() or any([zone.GetDoNotAllowTracks(),zone.GetDoNotAllowVias(),zone.GetDoNotAllowPads(),zone.GetDoNotAllowZoneFills(),zone.GetDoNotAllowFootprints()]):raise ValueError('Managed informational area unexpectedly restricts routing')
            b.RemoveNative(zone)
        export_dsn(b,folder,state['board_constraints'])
    elif action=='assembly':
        from assembly_geometry import extract
        print(json.dumps(extract(k.LoadBoard(str(folder/'routed.kicad_pcb')),k)))
    elif action=='inspect':
        path=folder/sys.argv[3];b=k.LoadBoard(str(path))
        constraints=state['board_constraints']
        marking_rows=interface_markings.requirements(state,constraints)
        mark_ids=json.loads((folder/'board_initial.json').read_text())['interface_marking_uuids'] if marking_rows else ()
        actual=summary(b,mark_ids)
        if marking_rows:
            actual['interface_markings']=interface_markings.inspect(b,k,marking_rows)
        from stackup import inspect as inspect_stackup
        actual['stackup']=inspect_stackup(path,state['board_constraints'])
        actual['mounting']=inspect_mounting(b,k,state['board_constraints'])
        actual['fabrication']=inspect_fabrication(b,k,via_size=constraints.get('via_size'),edge_clearance_mm=constraints['edge_clearance_mm'])
        actual['escape_isolation']=escape_isolation(path,state)
        from native_contacts import extract
        names={n['name'] for n in state['nets'].values() if n['kind'] in ['power','ground']}
        names.update(state['nets'][rule['net_id']]['name'] for rule in state.get('pcb_copper_policy',{}).get('rules',[]))
        requested={(row['source']['component_id'],row['source']['pin_id']) for row in state['board_constraints'].get('path_constraints',[])}
        names.update(n['name'] for n in state['nets'].values() if any((p['component_id'],p['pin_id']) in requested for p in n['pins']))
        actual['direct_contacts']=extract(b,k,names)
        from ground_planes import inspect as inspect_ground_planes
        actual['ground_planes']=inspect_ground_planes(path,b,k,state)
        print(json.dumps(actual))
    else:raise ValueError('Unsupported fixed worker action')


if __name__=='__main__':main()
