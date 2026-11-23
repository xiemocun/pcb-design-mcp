"""Read-only native geometry for assembly and explicit via filling requirements."""
try:from .mounting import mechanical_pad
except ImportError:from mounting import mechanical_pad


def extract(b,k):
    data=dict(thickness_mm=k.ToMM(b.GetDesignSettings().GetBoardThickness()),coordinates='PCB top view: X right, Y down from board origin; native positions CSV uses Y up',components={},fill_cap_holes=[])
    # A copper pad covered by mask is not an exposed solder land. Include
    # mask-only apertures (e.g. divided module EP) and native mask expansion.
    smd=[(f.GetReference(),p) for f in b.GetFootprints() for p in f.Pads() if p.GetAttribute()==k.PAD_ATTRIB_SMD and p.IsOnLayer(k.F_Mask)]
    def hosts_at(pos,drill):
        hosts=[]
        for ref,p in smd:
            margin=p.GetSolderMaskExpansion(k.F_Cu)
            circle=k.SHAPE_CIRCLE(pos,max(0,drill//2+margin))
            if p.GetEffectiveShape(k.F_Mask).Collide(circle):hosts.append(ref+'.'+(p.GetNumber() or 'mask_aperture'))
        return hosts
    for f in b.GetFootprints():
        if mechanical_pad(f,k) is not None:continue
        pads=[]
        for pad in f.Pads():
            pos=pad.GetPosition();drill=pad.GetDrillSize();row=dict(pin=pad.GetNumber(),net=pad.GetNetname(),x=k.ToMM(pos.x),y=k.ToMM(pos.y),drill_x_mm=k.ToMM(drill.x),drill_y_mm=k.ToMM(drill.y));pads.append(row)
            if drill.x and pad.GetAttribute()==k.PAD_ATTRIB_PTH:
                hosts=hosts_at(pos,max(drill.x,drill.y))
                if hosts:data['fill_cap_holes'].append(dict(kind='footprint_thermal_hole',reference=f.GetReference(),hosts=hosts,**row))
        data['components'][f.GetReference()]=dict(x=k.ToMM(f.GetPosition().x),y=k.ToMM(f.GetPosition().y),angle=f.GetOrientationDegrees(),assembly_method='THT/manual after reflow' if f.GetAttributes() & k.FP_THROUGH_HOLE else 'SMT',pads=pads)
    for v in b.GetTracks():
        if type(v)!=k.PCB_VIA:continue
        hosts=hosts_at(v.GetPosition(),v.GetDrillValue())
        if hosts:data['fill_cap_holes'].append(dict(kind='board_via_in_pad',hosts=hosts,x=k.ToMM(v.GetPosition().x),y=k.ToMM(v.GetPosition().y),drill_x_mm=k.ToMM(v.GetDrillValue()),drill_y_mm=k.ToMM(v.GetDrillValue())))
    return data
